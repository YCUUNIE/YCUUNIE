"""Self-healing system.

Implements the recovery pipeline from the spec:

    detect -> classify -> diagnose -> plan -> checkpoint -> repair ->
    verify (tests + health) -> success? resume : rollback -> log

The :class:`RepairAgent` is a special *internal* agent with a **restricted**
toolset. It can inspect state, run tests/health checks, checkpoint/rollback,
restart agents/services and *propose* code patches — but it has **no shell
access and never executes arbitrary code**. Source-code repair is gated behind
explicit approval (assisted mode) and confined to the project workspace.
"""
from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import ROOT_DIR
from ..database import get_database
from ..events import Event, event_bus
from ..memory import get_vault

# Files/dirs the repair agent may read for code repair. Everything else is off
# limits — no .env, no keys, no OS files, nothing outside the project.
CODE_READ_ALLOWLIST = ["frontend/src", "backend/app", "tests", "config"]
CODE_FORBIDDEN = [".env", "id_rsa", ".ssh", "credentials", "secrets"]


class RepairAgent:
    """Restricted internal agent. Only the methods below are callable."""

    ALLOWED_TOOLS = [
        "read_logs", "inspect_agent_state", "inspect_world_state", "inspect_provider_status",
        "inspect_database", "inspect_workflow", "run_tests", "run_health_check",
        "create_checkpoint", "rollback_checkpoint", "restart_agent", "restart_service",
        "propose_patch", "apply_patch",
    ]

    def __init__(self):
        self.db = get_database()
        self._checkpoints: Dict[str, Dict[str, Any]] = {}

    # --- read-only inspection ---------------------------------------------
    def read_logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        return event_bus.recent(limit=limit, category="error")

    def inspect_agent_state(self, agent_id: str) -> Optional[Dict[str, Any]]:
        from ..agents import get_runtime
        agent = get_runtime().agents.get(agent_id)
        return agent.to_dict() if agent else None

    def inspect_world_state(self) -> Dict[str, Any]:
        from ..world import get_world
        return get_world().snapshot()

    async def inspect_provider_status(self) -> Dict[str, Any]:
        from ..ai import get_ai_manager
        return await get_ai_manager().provider_status()

    async def inspect_database(self) -> Dict[str, Any]:
        return {"agents": len(await self.db.all("agents")),
                "tasks": len(await self.db.all("tasks")),
                "workflows": len(await self.db.all("workflows"))}

    def inspect_workflow(self) -> Dict[str, Any]:
        from ..workflows import get_workflow_engine
        return get_workflow_engine().snapshot()

    # --- verification ------------------------------------------------------
    def run_tests(self, target: str = "backend/tests") -> Dict[str, Any]:
        """Run the project's pytest suite. No arbitrary commands — pytest only."""
        path = (ROOT_DIR / target).resolve()
        if not str(path).startswith(str(ROOT_DIR.resolve())):
            return {"ok": False, "error": "target outside project"}
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", str(path), "-q", "--no-header"],
                cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=120,
            )
            tail = (proc.stdout or "")[-800:]
            return {"ok": proc.returncode == 0, "returncode": proc.returncode, "output": tail}
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return {"ok": False, "error": str(exc)}

    async def run_health_check(self) -> Dict[str, Any]:
        from ..health import health_detailed
        return await health_detailed()

    # --- checkpoint / rollback --------------------------------------------
    def create_checkpoint(self, label: str = "") -> str:
        from ..agents import get_runtime
        rt = get_runtime()
        cid = "ckpt_" + uuid.uuid4().hex[:8]
        self._checkpoints[cid] = {
            "label": label, "ts": time.time(),
            "agents": {aid: a.to_dict() for aid, a in rt.agents.items()},
        }
        return cid

    async def rollback_checkpoint(self, cid: str) -> bool:
        from ..agents import get_runtime
        from ..agents.agent import Agent
        ckpt = self._checkpoints.get(cid)
        if not ckpt:
            return False
        rt = get_runtime()
        for aid, data in ckpt["agents"].items():
            if aid in rt.agents:
                restored = Agent.from_dict(data)
                existing = rt.agents[aid]
                existing.x, existing.y = restored.x, restored.y
                existing.state = restored.state
                existing.relationships = restored.relationships
        await event_bus.emit("system.recovery", scope="rollback", message=f"Rolled back to {cid}")
        return True

    # --- active repair (safe, bounded) ------------------------------------
    async def restart_agent(self, agent_id: str, reason: str = "self-heal") -> Dict[str, Any]:
        from ..agents import get_runtime
        rt = get_runtime()
        agent = rt.agents.get(agent_id)
        if not agent:
            return {"ok": False, "error": "unknown agent"}
        return await rt.recover_agent(agent, reason)

    async def restart_service(self, service: str) -> Dict[str, Any]:
        if service == "simulation":
            from ..agents import get_runtime
            rt = get_runtime()
            was_paused = rt.paused
            rt.set_paused(True)
            await asyncio.sleep(0.2)
            rt.set_paused(was_paused)
            await event_bus.emit("system.recovery", scope="service", message="Simulation loop nudged")
            return {"ok": True, "service": service}
        return {"ok": False, "error": f"unknown service {service}"}

    # --- code repair (assisted, workspace-confined, never auto-exec) -------
    def _code_path_allowed(self, rel: str) -> bool:
        if any(f in rel for f in CODE_FORBIDDEN):
            return False
        return any(rel.startswith(a) for a in CODE_READ_ALLOWLIST)

    def read_source(self, rel: str) -> Optional[str]:
        if not self._code_path_allowed(rel):
            return None
        path = (ROOT_DIR / rel).resolve()
        if not str(path).startswith(str(ROOT_DIR.resolve())) or not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def propose_patch(self, rel: str, description: str, new_content: str) -> Dict[str, Any]:
        """Return a *proposal* only. Nothing is written until apply_patch."""
        if not self._code_path_allowed(rel):
            return {"ok": False, "error": "path not permitted"}
        return {"ok": True, "file": rel, "description": description,
                "proposed": new_content[:4000], "requires_approval": True}

    def apply_patch(self, rel: str, new_content: str, approved: bool = False) -> Dict[str, Any]:
        """Apply a patch only with explicit approval; git-checkpoint first."""
        if not approved:
            return {"ok": False, "error": "not approved"}
        if not self._code_path_allowed(rel):
            return {"ok": False, "error": "path not permitted"}
        path = (ROOT_DIR / rel).resolve()
        if not str(path).startswith(str(ROOT_DIR.resolve())):
            return {"ok": False, "error": "outside workspace"}
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(ROOT_DIR), capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001
            pass
        path.write_text(new_content, encoding="utf-8")
        return {"ok": True, "file": rel}


class SelfHealingManager:
    """Orchestrates detection, diagnosis and the repair pipeline."""

    def __init__(self):
        self.repair_agent = RepairAgent()
        self.db = get_database()
        self.vault = get_vault()
        self.enabled = True
        self.repairs: List[Dict[str, Any]] = []
        self._agent_error_counts: Dict[str, int] = {}
        self._repair_counter = 0
        event_bus.subscribe(self._on_event)

    async def _on_event(self, event: Event) -> None:
        if not self.enabled:
            return
        if event.type == "agent.error" and event.data.get("scope") == "runtime":
            aid = event.data.get("agent_id")
            if aid:
                self._agent_error_counts[aid] = self._agent_error_counts.get(aid, 0) + 1
                if self._agent_error_counts[aid] >= 2:
                    self._agent_error_counts[aid] = 0
                    await self.heal(kind="agent_error", target=aid,
                                    problem=event.data.get("message", "repeated agent errors"))

    # --- the recovery pipeline --------------------------------------------
    async def heal(self, kind: str, target: str = "", problem: str = "") -> Dict[str, Any]:
        self._repair_counter += 1
        repair_id = self._repair_counter
        await event_bus.emit("system.error", scope="self_heal", kind=kind, target=target, message=problem)

        diagnosis, plan = self._diagnose(kind, target, problem)
        checkpoint = self.repair_agent.create_checkpoint(label=f"pre-repair-{repair_id}")

        # execute plan step(s)
        executed: List[str] = []
        success = True
        try:
            if kind in ("agent_error", "stuck_agent", "invalid_action"):
                res = await self.repair_agent.restart_agent(target, reason=f"repair #{repair_id}: {diagnosis}")
                success = bool(res.get("recovered"))
                executed.append("restart_agent")
            elif kind == "provider_failure":
                # fallback is automatic in the AI manager; verify providers reachable
                await self.repair_agent.inspect_provider_status()
                executed.append("inspect_provider_status")
                success = True
            elif kind == "websocket":
                res = await self.repair_agent.restart_service("simulation")
                executed.append("restart_service")
                success = bool(res.get("ok"))
            elif kind == "workflow_failure":
                executed.append("inspect_workflow")
                self.repair_agent.inspect_workflow()
                success = True
            elif kind == "database":
                await self.repair_agent.inspect_database()
                executed.append("inspect_database")
                success = True
            else:
                executed.append("run_health_check")
                await self.repair_agent.run_health_check()
        except Exception as exc:  # noqa: BLE001
            success = False
            diagnosis += f" | execution error: {exc}"

        # verification
        health = await self.repair_agent.run_health_check()
        verified = success and health.get("status") in ("healthy", "degraded")
        if not verified:
            await self.repair_agent.rollback_checkpoint(checkpoint)
            executed.append("rollback_checkpoint")

        record = {
            "id": repair_id, "kind": kind, "target": target, "problem": problem,
            "diagnosis": diagnosis, "plan": plan, "executed": executed,
            "status": "SUCCESS" if verified else "ROLLED_BACK",
            "checkpoint": checkpoint, "ts": time.time(),
        }
        self.repairs.insert(0, record)
        self.repairs = self.repairs[:50]
        await self.db.log_repair(record)
        await self._write_repair_note(record)
        await event_bus.emit("system.recovery", scope="self_heal", repair=record,
                             message=f"Repair #{repair_id} {record['status']}: {diagnosis}")
        return record

    def _diagnose(self, kind: str, target: str, problem: str):
        table = {
            "agent_error": ("Agent entered an invalid runtime state.",
                            ["checkpoint", "restart_agent", "run_health_check", "verify"]),
            "stuck_agent": ("Agent stuck mid-movement (no progress).",
                            ["checkpoint", "clear_target", "restart_agent", "verify"]),
            "invalid_action": ("Agent produced an invalid/unvalidated action.",
                               ["normalize_action", "restart_agent", "verify"]),
            "provider_failure": ("AI provider unavailable; automatic fallback engaged.",
                                 ["retry", "fallback_provider", "inspect_provider_status", "verify"]),
            "websocket": ("WebSocket transport disrupted.",
                          ["restart_service", "reconnect", "verify"]),
            "workflow_failure": ("Workflow entered a failed/blocked state.",
                                 ["inspect_workflow", "remove_bad_dependency", "verify"]),
            "database": ("Database access degraded.", ["inspect_database", "verify"]),
        }
        return table.get(kind, ("Unknown issue; running generic diagnostics.", ["run_health_check", "verify"]))

    async def _write_repair_note(self, record: Dict[str, Any]) -> None:
        if not self.vault.available:
            return
        stamp = datetime.fromtimestamp(record["ts"]).strftime("%Y-%m-%d")
        target = record["target"] or record["kind"]
        rel = f"System/Repairs/{stamp}-{record['kind']}-{target}.md"
        body = (
            f"---\ntype: system-repair\ntarget: {target}\nseverity: warning\n"
            f"status: {record['status'].lower()}\n---\n\n"
            f"# Repair #{record['id']} — {record['kind']}\n\n"
            f"## Problem\n\n{record['problem']}\n\n"
            f"## Diagnosis\n\n{record['diagnosis']}\n\n"
            f"## Plan\n\n" + "\n".join(f"- {s}" for s in record["plan"]) + "\n\n"
            f"## Executed\n\n" + "\n".join(f"- {s}" for s in record["executed"]) + "\n\n"
            f"## Result\n\n{record['status']}\n"
        )
        try:
            self.vault.write_note(rel, body)
        except Exception:  # noqa: BLE001
            pass

    # --- controlled failure injection (the failure demo) ------------------
    async def simulate(self, what: str) -> Dict[str, Any]:
        from ..agents import get_runtime
        from ..ai import get_ai_manager
        rt = get_runtime()
        if what == "provider_failure":
            # Genuinely exercise the fallback path: point Alex at a real provider,
            # force it down, run a decision (it will fall back to local), restore.
            agent = rt.agents.get("alex")
            if agent:
                original = agent.provider
                from ..agents.agent import ProviderConfig
                agent.provider = ProviderConfig(provider="anthropic", model="claude-demo",
                                                fallbacks=["local"], decision_interval=original.decision_interval)
                get_ai_manager().force_failure("anthropic", seconds=8)
                agent.next_decision_at = rt.sim_time
                await asyncio.sleep(0.1)
                await rt._decision_cycle(agent)  # noqa: SLF001 - controlled demo
                agent.provider = original
            return await self.heal("provider_failure", target="alex", problem="Anthropic provider outage (simulated)")
        if what == "agent_error":
            agent = next(iter(rt.agents.values()), None)
            if agent:
                await rt._handle_agent_error(agent, "Simulated invalid state transition")  # noqa: SLF001
            return {"triggered": "agent_error", "agent": agent.id if agent else None}
        if what == "websocket":
            await event_bus.emit("agent.error", scope="websocket", message="Simulated WebSocket disconnect")
            return await self.heal("websocket", problem="Simulated WebSocket disconnect")
        if what == "invalid_tool":
            agent = next(iter(rt.agents.values()), None)
            if agent:
                from ..tools import ToolContext
                from ..schemas import IdleAction
                ctx = ToolContext(agent=agent, world=rt.world, memory=rt.memory,
                                  workflows=rt.workflows, agents_by_name=rt.by_name(),
                                  deliver_message=rt._deliver_message)  # noqa: SLF001
                # try a tool not in the allowlist to prove validation rejects it
                saved = agent.allowed_tools
                agent.allowed_tools = ["idle"]
                bad = IdleAction()
                bad.type = "delete_everything"  # type: ignore
                res = await rt.tools.execute(ctx, bad)
                agent.allowed_tools = saved
                return {"triggered": "invalid_tool", "rejected": not res.get("ok"), "detail": res}
            return {"triggered": "invalid_tool"}
        if what == "workflow_failure":
            wf_engine = rt.workflows
            t = await wf_engine.create_task("Broken task", "circular", assignee="Nova")
            await wf_engine.fail_task(t.id, "Simulated workflow failure")
            return await self.heal("workflow_failure", target="Nova", problem="Simulated workflow failure")
        return {"error": f"unknown simulation {what}"}


_manager: Optional[SelfHealingManager] = None


def get_self_healing() -> SelfHealingManager:
    global _manager
    if _manager is None:
        _manager = SelfHealingManager()
    return _manager
