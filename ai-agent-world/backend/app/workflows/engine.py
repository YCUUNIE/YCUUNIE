"""Task & workflow engine.

Tasks are the atomic unit of work; a workflow is an ordered set of steps with a
progress marker. Both are persisted to SQLite and broadcast on the event bus so
the UI's workflow inspector stays live. Dependency cycles are detected and
rejected (the self-healing system relies on this).
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..database import get_database
from ..events import event_bus

TASK_STATUSES = ["PENDING", "ACTIVE", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"]


@dataclass
class Task:
    id: str
    name: str
    description: str = ""
    assignee: Optional[str] = None
    priority: int = 5
    status: str = "PENDING"
    dependencies: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Workflow:
    id: str
    name: str
    assignee: Optional[str] = None
    status: str = "ACTIVE"
    steps: List[Dict[str, Any]] = field(default_factory=list)  # {label, done}
    created_at: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 0.0
        return sum(1 for s in self.steps if s.get("done")) / len(self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "assignee": self.assignee, "status": self.status,
            "steps": self.steps, "created_at": self.created_at, "progress": self.progress,
        }


class WorkflowEngine:
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.workflows: Dict[str, Workflow] = {}
        self.db = get_database()

    # --- tasks -------------------------------------------------------------
    def _has_cycle(self, task_id: str, deps: List[str]) -> bool:
        # DFS from each dependency; a path back to task_id means a cycle.
        seen = set()

        def visit(node: str) -> bool:
            if node == task_id:
                return True
            if node in seen:
                return False
            seen.add(node)
            t = self.tasks.get(node)
            return bool(t and any(visit(d) for d in t.dependencies))

        return any(visit(d) for d in deps)

    async def create_task(self, name: str, description: str = "", assignee: Optional[str] = None,
                          priority: int = 5, dependencies: Optional[List[str]] = None) -> Task:
        deps = dependencies or []
        task = Task(id="task_" + uuid.uuid4().hex[:8], name=name, description=description,
                    assignee=assignee, priority=priority, dependencies=deps)
        if self._has_cycle(task.id, deps):
            task.status = "BLOCKED"
            await event_bus.emit("agent.error", scope="workflow", task_id=task.id,
                                 message="Circular dependency detected; task blocked")
        else:
            task.status = "ACTIVE" if assignee else "PENDING"
        self.tasks[task.id] = task
        await self.db.upsert("tasks", task.id, task.to_dict())
        await event_bus.emit("agent.task.created", task=task.to_dict(), agent_id=assignee or "")
        return task

    async def assign_task(self, task_id: str, assignee: str) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.assignee = assignee
        task.status = "ACTIVE"
        task.updated_at = time.time()
        await self.db.upsert("tasks", task.id, task.to_dict())
        await event_bus.emit("agent.task.created", task=task.to_dict(), agent_id=assignee)
        return task

    async def complete_task(self, task_id: str) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.status = "COMPLETED"
        task.updated_at = time.time()
        await self.db.upsert("tasks", task.id, task.to_dict())
        await event_bus.emit("agent.task.completed", task=task.to_dict(), agent_id=task.assignee or "")
        return task

    async def fail_task(self, task_id: str, reason: str = "") -> Optional[Task]:
        task = self.tasks.get(task_id)
        if not task:
            return None
        task.status = "FAILED"
        task.updated_at = time.time()
        await self.db.upsert("tasks", task.id, task.to_dict())
        await event_bus.emit("agent.error", scope="workflow", task_id=task_id, message=reason or "task failed")
        return task

    def tasks_for(self, agent: str) -> List[Task]:
        return [t for t in self.tasks.values() if t.assignee == agent and t.status in ("ACTIVE", "PENDING", "BLOCKED")]

    def active_task_for(self, agent: str) -> Optional[Task]:
        active = [t for t in self.tasks.values() if t.assignee == agent and t.status == "ACTIVE"]
        active.sort(key=lambda t: t.priority)
        return active[0] if active else None

    # --- workflows ---------------------------------------------------------
    async def create_workflow(self, name: str, steps: List[str], assignee: Optional[str] = None) -> Workflow:
        wf = Workflow(id="wf_" + uuid.uuid4().hex[:8], name=name, assignee=assignee,
                      steps=[{"label": s, "done": False} for s in steps])
        self.workflows[wf.id] = wf
        await self.db.upsert("workflows", wf.id, wf.to_dict())
        await event_bus.emit("workflow.started", workflow=wf.to_dict(), agent_id=assignee or "")
        return wf

    async def advance_workflow(self, workflow_id: str) -> Optional[Workflow]:
        wf = self.workflows.get(workflow_id)
        if not wf:
            return None
        for step in wf.steps:
            if not step["done"]:
                step["done"] = True
                break
        if all(s["done"] for s in wf.steps):
            wf.status = "COMPLETED"
            await event_bus.emit("workflow.completed", workflow=wf.to_dict(), agent_id=wf.assignee or "")
        await self.db.upsert("workflows", wf.id, wf.to_dict())
        return wf

    def workflow_for(self, agent: str) -> Optional[Workflow]:
        for wf in self.workflows.values():
            if wf.assignee == agent and wf.status == "ACTIVE":
                return wf
        return None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "workflows": [w.to_dict() for w in self.workflows.values()],
        }


_engine: Optional[WorkflowEngine] = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
