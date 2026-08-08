"""HTTP + WebSocket API.

The frontend talks only to this layer. It never sees API keys and never calls
an AI provider directly — every AI request is mediated by the backend.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..agents import get_runtime
from ..ai import get_ai_manager
from ..config import get_config
from ..database import get_database
from ..events import event_bus
from ..memory import get_memory_provider, get_vault
from ..recovery import get_self_healing
from ..workflows import get_workflow_engine

router = APIRouter()


class WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    def count(self) -> int:
        return len(self.active)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSManager()


# --- request models --------------------------------------------------------
class ChatRequest(BaseModel):
    target: str = "All Agents"
    message: str


class AgentPayload(BaseModel):
    data: Dict[str, Any]


class SpeedRequest(BaseModel):
    speed: float


# --- WebSocket -------------------------------------------------------------
@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    rt = get_runtime()
    await ws_manager.connect(ws)
    try:
        await ws.send_json({
            "type": "init",
            "data": {
                "state": rt.full_snapshot(),
                "config": get_config().as_public(),
                "events": event_bus.recent(limit=60),
                "chat": await get_database().chat_history(limit=40),
            },
        })
        while True:
            # Client may send pings or lightweight commands; we mostly push.
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        ws_manager.disconnect(ws)


# --- state & config --------------------------------------------------------
@router.get("/api/version")
async def api_version():
    from .. import BUILD, __version__
    return {"version": __version__, "build": BUILD}


@router.get("/api/config")
async def api_config():
    return get_config().as_public()


@router.get("/api/state")
async def api_state():
    return get_runtime().full_snapshot()


# --- agents ----------------------------------------------------------------
@router.get("/api/agents")
async def api_agents():
    return get_runtime().agents_snapshot()


@router.get("/api/agents/{agent_id}")
async def api_agent(agent_id: str):
    agent = get_runtime().agents.get(agent_id)
    return agent.to_dict() if agent else {"error": "not found"}


@router.post("/api/agents")
async def api_create_agent(payload: AgentPayload):
    agent = await get_runtime().add_agent(payload.data)
    return agent.to_dict()


@router.put("/api/agents/{agent_id}")
async def api_update_agent(agent_id: str, payload: AgentPayload):
    agent = await get_runtime().update_agent(agent_id, payload.data)
    return agent.to_dict() if agent else {"error": "not found"}


@router.get("/api/agents/{agent_id}/memory")
async def api_agent_memory(agent_id: str):
    rt = get_runtime()
    agent = rt.agents.get(agent_id)
    if not agent:
        return {"error": "not found"}
    mems = await get_memory_provider().all_for(agent.name)
    return {"agent": agent.name, "memories": [m.to_dict() for m in mems]}


# --- chat & commands -------------------------------------------------------
@router.get("/api/chat/history")
async def api_chat_history():
    return await get_database().chat_history(limit=80)


@router.post("/api/chat")
async def api_chat(req: ChatRequest):
    return await handle_chat(req.target, req.message)


async def handle_chat(target: str, message: str) -> Dict[str, Any]:
    rt = get_runtime()
    text = message.strip()
    await get_database().log_message("user", "You", target, text)
    await event_bus.emit("chat.user", sender="You", target=target, message=text)

    # slash commands
    if text.startswith("/"):
        return await _slash_command(text)

    # natural-language system commands
    nl = _interpret_nl(text)
    if nl is not None:
        if nl.get("_route_to_agent"):
            # e.g. "tell Alex to investigate the northern forest"
            r = await rt.chat_with_agent(nl["target"], nl["message"])
            return {"type": "chat", "responses": [r],
                    "note": f"Relayed your instruction to {nl['target']}."}
        return nl

    # otherwise: talk to an agent (or all)
    by_name = rt.by_name()
    if target in ("All Agents", "all", "All"):
        replies = []
        for name in by_name:
            r = await rt.chat_with_agent(name, text)
            replies.append(r)
        return {"type": "chat", "responses": replies}
    if target in ("System", "Repair Agent"):
        return {"type": "system", "responses": [{"agent": target,
                "reply": "Use /system or the failure-demo buttons to interact with recovery."}]}
    if target in by_name:
        r = await rt.chat_with_agent(target, text)
        return {"type": "chat", "responses": [r]}
    return {"type": "system", "responses": [{"agent": "System", "reply": f"Unknown target '{target}'."}]}


async def _slash_command(text: str) -> Dict[str, Any]:
    rt = get_runtime()
    parts = text[1:].split()
    cmd = parts[0].lower() if parts else ""
    arg = " ".join(parts[1:])

    if cmd == "help":
        return _sys("Commands: /help /agents /status /pause /resume /speed N /select NAME "
                    "/focus NAME /memory NAME /tasks /workflows /system")
    if cmd == "agents":
        return _sys(", ".join(f"{a.name} ({a.role}) — {a.state.value}" for a in rt.agents.values()))
    if cmd == "status":
        s = rt.full_snapshot()["sim"]
        return _sys(f"Day {s['day']} · running={s['running']} paused={s['paused']} speed=x{s['speed']}")
    if cmd == "pause":
        rt.set_paused(True)
        await event_bus.emit("system.recovery", scope="sim", message="Paused")
        return _sys("Simulation paused.")
    if cmd == "resume":
        rt.set_paused(False)
        await event_bus.emit("system.recovery", scope="sim", message="Resumed")
        return _sys("Simulation resumed.")
    if cmd == "speed" and arg:
        try:
            rt.set_speed(float(arg))
            return _sys(f"Speed set to x{rt.speed}.")
        except ValueError:
            return _sys("Usage: /speed N")
    if cmd in ("select", "focus") and arg:
        agent = rt.by_name().get(arg.title())
        if agent:
            await event_bus.emit("ui.focus", agent_id=agent.id)
            return _sys(f"Focusing {agent.name}.")
        return _sys(f"No agent named {arg}.")
    if cmd == "memory" and arg:
        agent = rt.by_name().get(arg.title())
        if agent:
            summary = await get_memory_provider().summarize(agent.name, limit=8)
            return _sys(summary)
        return _sys(f"No agent named {arg}.")
    if cmd == "tasks":
        tasks = get_workflow_engine().snapshot()["tasks"]
        return _sys("Tasks: " + (", ".join(f"{t['name']} [{t['status']}]" for t in tasks) or "none"))
    if cmd == "workflows":
        wfs = get_workflow_engine().snapshot()["workflows"]
        return _sys("Workflows: " + (", ".join(f"{w['name']} ({int(w['progress']*100)}%)" for w in wfs) or "none"))
    if cmd == "system":
        health = await _health_detailed()
        return _sys(f"System status: {health['status']}. Clients: {ws_manager.count()}.")
    return _sys(f"Unknown command '/{cmd}'. Try /help.")


def _interpret_nl(text: str) -> Optional[Dict[str, Any]]:
    """Map a few natural-language commands to structured actions. Returns None
    to let the message fall through to normal agent chat."""
    low = text.lower()
    rt = get_runtime()

    # bare control words typed as a whole message
    bare = low.strip().strip(".!?")
    if bare in ("stop", "pause", "halt", "freeze"):
        rt.set_paused(True)
        return _sys("Simulation paused. (Type 'resume' or press ▶ to continue.)")
    if bare in ("start", "resume", "play", "continue", "go", "unpause"):
        rt.set_paused(False)
        return _sys("Simulation resumed.")

    if re.search(r"\b(pause|stop|halt)\b", low) and ("simulation" in low or "sim" in low or "agents" in low):
        rt.set_paused(True)
        return _sys("Simulation paused.")
    if re.search(r"\b(resume|unpause|start|continue)\b", low) and ("simulation" in low or "sim" in low):
        rt.set_paused(False)
        return _sys("Simulation resumed.")
    m = re.search(r"speed (?:up )?(?:to )?(?:x)?(\d+)", low)
    if m:
        rt.set_speed(float(m.group(1)))
        return _sys(f"Speed set to x{rt.speed}.")

    # "tell/ask X to <do something>"  -> deliver as a directive to that agent
    m = re.search(r"(?:tell|ask) (\w+) to (.+)", low)
    if m:
        name, directive = m.group(1).title(), m.group(2)
        if name in rt.by_name():
            return {"type": "command", "target": name, "message": directive,
                    "_route_to_agent": True}
    return None


def _sys(msg: str) -> Dict[str, Any]:
    return {"type": "system", "responses": [{"agent": "System", "reply": msg}]}


# --- simulation controls ---------------------------------------------------
@router.post("/api/sim/pause")
async def api_pause():
    get_runtime().set_paused(True)
    return {"paused": True}


@router.post("/api/sim/resume")
async def api_resume():
    get_runtime().set_paused(False)
    return {"paused": False}


@router.post("/api/sim/step")
async def api_step():
    await get_runtime().step_once()
    return {"stepped": True}


@router.post("/api/sim/speed")
async def api_speed(req: SpeedRequest):
    get_runtime().set_speed(req.speed)
    return {"speed": get_runtime().speed}


@router.post("/api/sim/stop")
async def api_stop():
    get_runtime().set_paused(True)
    return {"stopped": True}


# --- events / tasks / workflows -------------------------------------------
@router.get("/api/events")
async def api_events(category: str = "all", limit: int = 100):
    return event_bus.recent(limit=limit, category=category)


@router.get("/api/tasks")
async def api_tasks():
    return get_workflow_engine().snapshot()["tasks"]


@router.get("/api/workflows")
async def api_workflows():
    return get_workflow_engine().snapshot()["workflows"]


# --- providers -------------------------------------------------------------
@router.get("/api/providers")
async def api_providers():
    mgr = get_ai_manager()
    return {"stats": mgr.stats(), "status": await mgr.provider_status(),
            "config": get_config().as_public()["providers"]}


@router.post("/api/providers/{name}/test")
async def api_provider_test(name: str):
    return await get_ai_manager().test_connection(name)


# --- health ----------------------------------------------------------------
async def _health_detailed():
    from ..health import health_detailed
    return await health_detailed()


@router.get("/api/health")
async def api_health():
    from ..health import health_basic
    return await health_basic()


@router.get("/api/health/detailed")
async def api_health_detailed():
    return await _health_detailed()


# --- self-healing / repairs ------------------------------------------------
@router.get("/api/repairs")
async def api_repairs():
    healer = get_self_healing()
    return {"enabled": healer.enabled, "repairs": healer.repairs,
            "history": await get_database().repair_history(limit=30)}


@router.post("/api/repair/simulate/{what}")
async def api_repair_simulate(what: str):
    return await get_self_healing().simulate(what)


@router.post("/api/repair/toggle")
async def api_repair_toggle():
    healer = get_self_healing()
    healer.enabled = not healer.enabled
    return {"enabled": healer.enabled}


# --- obsidian --------------------------------------------------------------
@router.get("/api/memory/location")
async def api_memory_location():
    vault = get_vault()
    files = []
    if vault.available:
        for p in sorted(vault.root.rglob("*.md"))[:100]:
            files.append(str(p.relative_to(vault.vault_path)))
    return {"path": vault.open_location(), "available": vault.available, "files": files}


@router.get("/api/memory/note")
async def api_memory_note(path: str):
    vault = get_vault()
    # `path` is vault-relative under the root folder; the vault guard blocks escapes.
    rel = path
    if rel.startswith(vault.root_folder + "/"):
        rel = rel[len(vault.root_folder) + 1:]
    try:
        content = vault.read_note(rel)
    except PermissionError:
        return {"error": "path not permitted"}
    return {"path": path, "content": content or "(empty)"}
