"""Central asynchronous event bus.

Every meaningful thing that happens in the simulation is an ``Event``. The bus
fans events out to subscribers (the WebSocket layer, the event feed persistence,
the self-healing monitor). It is deliberately tiny and dependency-free.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

# Canonical event categories, used by the UI event-feed filters.
CATEGORIES = {
    "agent": "Agents",
    "chat": "Chat",
    "task": "Tasks",
    "memory": "Memory",
    "workflow": "Workflows",
    "world": "World",
    "system": "System",
    "error": "Errors",
    "recovery": "Recovery",
}


def _category_for(event_type: str) -> str:
    if event_type.startswith("agent.error") or event_type.endswith(".error") or event_type.startswith("system.error"):
        return "error"
    if event_type.startswith("system.recovery") or event_type.startswith("agent.recovered") or "recover" in event_type:
        return "recovery"
    if event_type.startswith("workflow"):
        return "workflow"
    if event_type.startswith("agent.task") or event_type.startswith("task"):
        return "task"
    if event_type.startswith("agent.memory") or event_type.startswith("memory"):
        return "memory"
    if event_type.startswith("agent.talked") or event_type.startswith("chat"):
        return "chat"
    if event_type.startswith("agent"):
        return "agent"
    if event_type.startswith("world"):
        return "world"
    return "system"


@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    category: str = ""

    def __post_init__(self):
        if not self.category:
            self.category = _category_for(self.type)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": self.data, "ts": self.ts, "category": self.category}


Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: List[Subscriber] = []
        self._history: List[Event] = []
        self._history_limit = 500

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        self._subscribers.append(fn)
        return lambda: self._subscribers.remove(fn) if fn in self._subscribers else None

    async def emit(self, event_type: str, **data: Any) -> Event:
        event = Event(type=event_type, data=data)
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]
        # Deliver to every subscriber; a failing subscriber must not break others.
        for fn in list(self._subscribers):
            try:
                await fn(event)
            except Exception:  # pragma: no cover - defensive
                pass
        return event

    def recent(self, limit: int = 100, category: str = "all") -> List[Dict[str, Any]]:
        items = self._history
        if category and category != "all":
            items = [e for e in items if e.category == category]
        return [e.to_dict() for e in items[-limit:]]


# Module-level singleton used across the app.
event_bus = EventBus()
