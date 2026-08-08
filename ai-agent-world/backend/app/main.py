"""FastAPI application entrypoint.

Run with:  ``python -m backend.app.main``  (or ``uvicorn backend.app.main:app``)

On startup it wires the event bus to the WebSocket broadcaster and to SQLite
persistence, loads the three default agents, seeds a little example memory into
the Obsidian vault, and starts the simulation loop.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import get_runtime
from .config import ROOT_DIR, get_config
from .database import get_database
from .events import Event, event_bus
from .memory import Memory, get_memory_provider
from .recovery import get_self_healing
from .api import router

app = FastAPI(title="AI Agent World", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Movement events are high-frequency; broadcast them but don't spam SQLite.
_NON_PERSISTED = {"agent.moved", "agent.state"}


async def _on_event(event: Event) -> None:
    # persist
    if event.type not in _NON_PERSISTED:
        with contextlib.suppress(Exception):
            await get_database().log_event(event.type, event.category, event.data, event.ts)
    # broadcast
    from .api import ws_manager
    with contextlib.suppress(Exception):
        await ws_manager.broadcast({"type": "event", "event": event.to_dict()})


def _write_agent_profiles() -> None:
    """Write each agent's profile note into the vault (Obsidian example vault)."""
    from .memory import get_vault
    vault = get_vault()
    if not vault.available:
        return
    for agent in get_runtime().agents.values():
        note = (
            f"---\nagent: {agent.name}\nrole: {agent.role}\ntype: profile\n---\n\n"
            f"# {agent.name} — {agent.role}\n\n"
            f"{agent.background}\n\n"
            f"## Personality\n\n" + "\n".join(f"- {p}" for p in agent.personality) + "\n\n"
            f"## Goals\n\n" + "\n".join(f"- {g}" for g in agent.goals) + "\n\n"
            f"## AI Provider\n\n- provider: {agent.provider.provider}\n- model: {agent.provider.model}\n"
        )
        with contextlib.suppress(Exception):
            vault.write_note(f"Agents/{agent.name}.md", note)


async def _seed_memory() -> None:
    memory = get_memory_provider()
    rt = get_runtime()
    for agent in rt.agents.values():
        existing = await memory.all_for(agent.name)
        if existing:
            continue
        seed = {
            "Alex": ("I love exploring; the northern forest looks unexplored and inviting.", "medium"),
            "Nova": ("The workshop needs improvement; I should gather wood and stone.", "medium"),
            "Echo": ("I keep a record of everything that happens in the settlement.", "medium"),
        }.get(agent.name)
        if seed:
            await memory.remember(Memory.new(agent.name, seed[0], importance=seed[1],
                                             kind="memory", source="world"))


@app.on_event("startup")
async def on_startup() -> None:
    event_bus.subscribe(_on_event)
    get_self_healing()  # subscribe to error events
    rt = get_runtime()
    rt.load_defaults()
    _write_agent_profiles()
    await _seed_memory()
    if get_config().get("simulation", "autostart", default=True):
        await rt.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await get_runtime().stop()


# --- serve the built frontend (if present) ---------------------------------
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


@app.get("/")
async def index():
    idx = FRONTEND_DIST / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({
        "app": "AI Agent World backend",
        "status": "running",
        "hint": "Start the frontend with `npm run dev` in /frontend, or build it "
                "(`npm run build`) to have the backend serve it here.",
        "endpoints": ["/api/state", "/api/health/detailed", "/ws"],
    })


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")


def main() -> None:
    import uvicorn
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
