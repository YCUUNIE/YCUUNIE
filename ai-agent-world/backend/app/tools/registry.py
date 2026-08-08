"""Agent tool system.

Agents never run arbitrary code. They emit a *structured action* (validated by
:mod:`app.schemas`) whose ``type`` must be in this registry's allowlist. Each
tool has an explicit schema and a guarded executor that mutates world/memory/
workflow state and emits events. This is the entire surface an agent can touch —
no shell, no filesystem outside the vault, no eval.

Adding a tool: give it a ``name``, a Pydantic action model, and an executor,
then register it below (see README, "Adding new tools").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..events import event_bus
from ..memory.base import Memory
from ..schemas import (
    Action, CompleteTaskAction, CreateTaskAction, IdleAction, InspectObjectAction,
    MoveToAction, MoveToLocationAction, ObserveAreaAction, TalkToAgentAction, WorkAction,
)


@dataclass
class ToolContext:
    agent: Any                         # app.agents.agent.Agent
    world: Any                         # app.world.World
    memory: Any                        # MemoryProvider
    workflows: Any                     # WorkflowEngine
    agents_by_name: Dict[str, Any]     # name -> Agent
    deliver_message: Callable[[str, str, str], Awaitable[None]]  # (target_name, from_name, msg)


# Human-readable tool catalogue (schema + description) exposed to the UI/agent.
TOOL_CATALOG: Dict[str, Dict[str, str]] = {
    "move_to": {"description": "Walk to a world coordinate.", "params": "x, y"},
    "move_to_location": {"description": "Walk to a named location.", "params": "location"},
    "talk_to_agent": {"description": "Say something to another agent.", "params": "target, message"},
    "observe_area": {"description": "Look around and perceive nearby things.", "params": "radius"},
    "inspect_object": {"description": "Examine a world object closely.", "params": "object_id"},
    "work": {"description": "Perform work at the current location.", "params": "target, note"},
    "create_task": {"description": "Create a task for self or another agent.", "params": "name, description, assignee"},
    "complete_task": {"description": "Mark a task complete.", "params": "task_id"},
    "idle": {"description": "Wait and do nothing this cycle.", "params": "reason"},
}

DEFAULT_ALLOWED = list(TOOL_CATALOG.keys())


class ToolRegistry:
    def __init__(self):
        self._executors: Dict[str, Callable] = {
            "move_to": self._move_to,
            "move_to_location": self._move_to_location,
            "talk_to_agent": self._talk_to_agent,
            "observe_area": self._observe_area,
            "inspect_object": self._inspect_object,
            "work": self._work,
            "create_task": self._create_task,
            "complete_task": self._complete_task,
            "idle": self._idle,
        }

    def catalog(self) -> Dict[str, Dict[str, str]]:
        return TOOL_CATALOG

    async def execute(self, ctx: ToolContext, action: Action) -> Dict[str, Any]:
        """Validate the allowlist, then run the tool. Returns an observation."""
        atype = action.type
        agent = ctx.agent
        allowed = agent.allowed_tools or DEFAULT_ALLOWED
        if atype not in allowed:
            await event_bus.emit("agent.error", agent_id=agent.id, scope="tool",
                                 message=f"Tool '{atype}' not permitted for {agent.name}")
            return {"ok": False, "error": f"tool_not_allowed:{atype}"}
        executor = self._executors.get(atype)
        if executor is None:
            return {"ok": False, "error": f"unknown_tool:{atype}"}
        await event_bus.emit("agent.tool.called", agent_id=agent.id, tool=atype)
        return await executor(ctx, action)

    # --- individual tools --------------------------------------------------
    async def _move_to(self, ctx: ToolContext, action: MoveToAction) -> Dict[str, Any]:
        agent, world = ctx.agent, ctx.world
        x = max(0, min(world.width, action.x))
        y = max(0, min(world.height, action.y))
        agent.set_target(x, y)
        agent.activity = f"walking toward ({int(x)}, {int(y)})"
        return {"ok": True, "moving_to": {"x": x, "y": y}}

    async def _move_to_location(self, ctx: ToolContext, action: MoveToLocationAction) -> Dict[str, Any]:
        loc = ctx.world.location(action.location)
        if not loc:
            # try nearest by fuzzy name
            for name, l in ctx.world.locations.items():
                if action.location.lower() in name.lower():
                    loc = l
                    break
        if not loc:
            return {"ok": False, "error": f"unknown_location:{action.location}"}
        ctx.agent.set_target(loc.x, loc.y)
        ctx.agent.activity = f"heading to {loc.name}"
        if loc.kind in ("forest", "structure", "water"):
            ctx.agent.state = ctx.agent.state.__class__.EXPLORING
        return {"ok": True, "location": loc.name, "target": {"x": loc.x, "y": loc.y}}

    async def _talk_to_agent(self, ctx: ToolContext, action: TalkToAgentAction) -> Dict[str, Any]:
        target = ctx.agents_by_name.get(action.target) or ctx.agents_by_name.get(action.target.title())
        ctx.agent.state = ctx.agent.state.__class__.TALKING
        ctx.agent.activity = f"talking to {action.target}"
        await event_bus.emit("agent.talked", agent_id=ctx.agent.id, agent_name=ctx.agent.name,
                             target=action.target, message=action.message)
        if target is None:
            return {"ok": False, "error": f"unknown_agent:{action.target}", "spoke": action.message}
        # Strengthen the social bond a touch.
        ctx.agent.adjust_relationship(target.id, friendship=0.02, trust=0.01)
        await ctx.deliver_message(target.name, ctx.agent.name, action.message)
        return {"ok": True, "spoke_to": target.name, "message": action.message}

    async def _observe_area(self, ctx: ToolContext, action: ObserveAreaAction) -> Dict[str, Any]:
        agent, world = ctx.agent, ctx.world
        agent.state = agent.state.__class__.EXPLORING
        agent.activity = "observing surroundings"
        # Observing can reveal hidden objects nearby.
        revealed = []
        for obj in world.objects_near(agent.x, agent.y, action.radius, include_hidden=True):
            if not obj.discovered:
                world.discover(obj.id, agent.name)
                revealed.append(obj.to_dict())
                await event_bus.emit("world.changed", change="discovered", object=obj.to_dict(),
                                     agent_id=agent.id, agent_name=agent.name)
        visible = [o.to_dict() for o in world.objects_near(agent.x, agent.y, action.radius)]
        nearby_agents = [a.name for a in ctx.agents_by_name.values()
                         if a.id != agent.id and abs(a.x - agent.x) < action.radius and abs(a.y - agent.y) < action.radius]
        return {"ok": True, "location": world.location_at(agent.x, agent.y),
                "objects": visible, "revealed": revealed, "agents_nearby": nearby_agents}

    async def _inspect_object(self, ctx: ToolContext, action: InspectObjectAction) -> Dict[str, Any]:
        obj = ctx.world.objects.get(action.object_id)
        if not obj:
            return {"ok": False, "error": f"unknown_object:{action.object_id}"}
        ctx.world.discover(obj.id, ctx.agent.name)
        ctx.agent.activity = f"inspecting {obj.label}"
        ctx.world.note_location(ctx.world.location_at(obj.x, obj.y) or obj.label,
                                inspected_by=ctx.agent.name, kind=obj.kind)
        return {"ok": True, "object": obj.to_dict()}

    async def _work(self, ctx: ToolContext, action: WorkAction) -> Dict[str, Any]:
        agent = ctx.agent
        agent.state = agent.state.__class__.WORKING
        agent.activity = action.note or "working"
        agent.energy = max(0.1, agent.energy - 0.05)
        # If there's a workflow, advance it.
        wf = ctx.workflows.workflow_for(agent.name)
        if wf:
            await ctx.workflows.advance_workflow(wf.id)
        await event_bus.emit("world.changed", change="work", agent_id=agent.id,
                             agent_name=agent.name, note=agent.activity)
        return {"ok": True, "worked_on": action.target or agent.activity}

    async def _create_task(self, ctx: ToolContext, action: CreateTaskAction) -> Dict[str, Any]:
        assignee = action.assignee or ctx.agent.name
        task = await ctx.workflows.create_task(action.name, action.description, assignee, action.priority)
        return {"ok": True, "task": task.to_dict()}

    async def _complete_task(self, ctx: ToolContext, action: CompleteTaskAction) -> Dict[str, Any]:
        task = await ctx.workflows.complete_task(action.task_id)
        if not task:
            return {"ok": False, "error": f"unknown_task:{action.task_id}"}
        return {"ok": True, "task": task.to_dict()}

    async def _idle(self, ctx: ToolContext, action: IdleAction) -> Dict[str, Any]:
        ctx.agent.state = ctx.agent.state.__class__.IDLE
        ctx.agent.activity = action.reason or "resting"
        return {"ok": True}


_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
