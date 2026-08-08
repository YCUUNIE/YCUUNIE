"""System health monitoring.

Backs ``GET /health`` (liveness) and ``GET /health/detailed`` (per-subsystem
status shown in the UI's SYSTEM STATUS panel). Nothing here raises — an
unhealthy subsystem is reported, not thrown.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from .agents import get_runtime
from .ai import get_ai_manager
from .database import get_database
from .events import event_bus
from .memory import get_vault
from .workflows import get_workflow_engine

_start_time = time.time()


async def health_basic() -> Dict[str, Any]:
    return {"status": "healthy", "uptime": round(time.time() - _start_time, 1)}


async def health_detailed() -> Dict[str, Any]:
    subsystems: Dict[str, Any] = {}

    # backend
    subsystems["backend"] = {"status": "healthy"}

    # sqlite
    try:
        await get_database().get_state("__ping__", None)
        subsystems["sqlite"] = {"status": "healthy"}
    except Exception as exc:  # noqa: BLE001
        subsystems["sqlite"] = {"status": "error", "error": str(exc)[:120]}

    # obsidian vault
    vault = get_vault()
    subsystems["obsidian"] = {
        "status": "connected" if vault.available else "offline",
        "path": str(vault.root),
    }

    # agent runtime
    rt = get_runtime()
    agent_health = {}
    for aid, agent in rt.agents.items():
        agent_health[aid] = {
            "name": agent.name,
            "status": {"healthy": "healthy", "recovering": "recovering", "error": "error"}.get(
                agent.recovery_state, "healthy"),
        }
    subsystems["agent_runtime"] = {
        "status": "healthy" if rt.running else "stopped",
        "agents": agent_health,
        "running": rt.running, "paused": rt.paused,
    }

    # memory
    subsystems["memory"] = {"status": "healthy", "provider": "obsidian"}

    # tools + workflows
    subsystems["tools"] = {"status": "healthy"}
    subsystems["workflow_engine"] = {"status": "healthy",
                                     "workflows": len(get_workflow_engine().workflows)}

    # websocket (reported by the connection manager)
    from .api.routes import ws_manager
    subsystems["websocket"] = {"status": "connected" if ws_manager.count() > 0 else "idle",
                               "clients": ws_manager.count()}

    # providers
    subsystems["providers"] = await get_ai_manager().provider_status()

    # overall
    bad = [k for k, v in subsystems.items()
           if isinstance(v, dict) and v.get("status") in ("error", "stopped")]
    overall = "healthy" if not bad else ("degraded" if len(bad) == 1 else "error")

    return {
        "status": overall,
        "uptime": round(time.time() - _start_time, 1),
        "subsystems": subsystems,
        "recent_errors": event_bus.recent(limit=8, category="error"),
    }
