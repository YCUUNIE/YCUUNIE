"""Agent model.

An ``Agent`` is a real, stateful entity: identity, personality, goals, a
position and movement in the world, memory of relationships, its own AI provider
configuration, and recovery state. It is *not* a chatbot with a sprite — the
runtime drives it through a perceive→decide→act loop (:mod:`app.agents.runtime`).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentState(str, Enum):
    IDLE = "IDLE"
    WALKING = "WALKING"
    THINKING = "THINKING"
    TALKING = "TALKING"
    WORKING = "WORKING"
    EXPLORING = "EXPLORING"
    WAITING = "WAITING"
    SLEEPING = "SLEEPING"
    ERROR = "ERROR"
    RECOVERING = "RECOVERING"


@dataclass
class ProviderConfig:
    provider: str = "local"
    model: str = "rule-policy"
    temperature: float = 0.7
    max_tokens: int = 600
    decision_interval: float = 10.0
    fallbacks: List[str] = field(default_factory=lambda: ["local"])

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Agent:
    id: str
    name: str
    role: str
    personality: List[str] = field(default_factory=list)
    background: str = ""
    goals: List[str] = field(default_factory=list)
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    allowed_tools: List[str] = field(default_factory=list)
    color: str = "#5aa9e6"

    # runtime state
    x: float = 800.0
    y: float = 620.0
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    facing: str = "down"
    speed: float = 70.0                 # px / second
    state: AgentState = AgentState.IDLE
    activity: str = "settling in"
    energy: float = 1.0
    current_task_id: Optional[str] = None

    # bookkeeping
    inbox: List[Dict[str, Any]] = field(default_factory=list)
    relationships: Dict[str, Dict[str, float]] = field(default_factory=dict)
    last_action: Optional[Dict[str, Any]] = None
    last_summary: str = ""
    last_error: Optional[str] = None
    last_provider_used: str = ""
    last_latency: float = 0.0
    last_tokens: Dict[str, int] = field(default_factory=dict)
    memories_retrieved: int = 0
    recovery_state: str = "healthy"     # healthy | recovering | error
    stuck_counter: int = 0
    consecutive_errors: int = 0
    next_decision_at: float = 0.0
    thinking: bool = False

    # --- movement ----------------------------------------------------------
    def set_target(self, x: float, y: float) -> None:
        self.target_x = x
        self.target_y = y
        if self.state not in (AgentState.EXPLORING,):
            self.state = AgentState.WALKING

    def step_movement(self, dt: float) -> bool:
        if self.target_x is None or self.target_y is None:
            return False
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)
        if dist < 4:
            self.x, self.y = self.target_x, self.target_y
            self.target_x = self.target_y = None
            if self.state in (AgentState.WALKING,):
                self.state = AgentState.IDLE
            return True
        step = self.speed * dt
        if step >= dist:
            self.x, self.y = self.target_x, self.target_y
        else:
            self.x += dx / dist * step
            self.y += dy / dist * step
        # facing
        if abs(dx) > abs(dy):
            self.facing = "right" if dx > 0 else "left"
        else:
            self.facing = "down" if dy > 0 else "up"
        return True

    def is_moving(self) -> bool:
        return self.target_x is not None and self.target_y is not None

    # --- relationships -----------------------------------------------------
    def relationship(self, other: str) -> Dict[str, float]:
        return self.relationships.setdefault(other, {"trust": 0.5, "friendship": 0.5, "respect": 0.5})

    def adjust_relationship(self, other: str, **deltas: float) -> Dict[str, float]:
        rel = self.relationship(other)
        for key, delta in deltas.items():
            rel[key] = max(0.0, min(1.0, rel.get(key, 0.5) + delta))
        return rel

    # --- serialization -----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role, "personality": self.personality,
            "background": self.background, "goals": self.goals, "provider": self.provider.to_dict(),
            "allowed_tools": self.allowed_tools, "color": self.color,
            "x": round(self.x, 1), "y": round(self.y, 1), "facing": self.facing,
            "state": self.state.value, "activity": self.activity, "energy": round(self.energy, 2),
            "current_task_id": self.current_task_id, "relationships": self.relationships,
            "last_action": self.last_action, "last_summary": self.last_summary,
            "last_error": self.last_error, "last_provider_used": self.last_provider_used,
            "last_latency": round(self.last_latency, 3), "last_tokens": self.last_tokens,
            "memories_retrieved": self.memories_retrieved, "recovery_state": self.recovery_state,
        }

    def public_view(self) -> Dict[str, Any]:
        """Minimal state for movement/render updates over the socket."""
        return {
            "id": self.id, "x": round(self.x, 1), "y": round(self.y, 1), "facing": self.facing,
            "state": self.state.value, "activity": self.activity, "recovery_state": self.recovery_state,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Agent":
        prov = data.get("provider", {}) or {}
        agent = Agent(
            id=data["id"], name=data["name"], role=data.get("role", ""),
            personality=data.get("personality", []), background=data.get("background", ""),
            goals=data.get("goals", []),
            provider=ProviderConfig(
                provider=prov.get("provider", "local"), model=prov.get("model", "rule-policy"),
                temperature=prov.get("temperature", 0.7), max_tokens=prov.get("max_tokens", 600),
                decision_interval=prov.get("decision_interval", 10.0),
                fallbacks=prov.get("fallbacks", ["local"]),
            ),
            allowed_tools=data.get("allowed_tools", []),
            color=data.get("color", "#5aa9e6"),
            x=data.get("x", 800.0), y=data.get("y", 620.0),
        )
        return agent
