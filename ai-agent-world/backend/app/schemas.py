"""Pydantic schemas for structured AI output and validated actions.

The LLM never drives the world with free text. It must return a
``Decision`` — validated here — whose ``action`` is one of a small, explicit
allowlist. Anything else is rejected and repaired/retried by the runtime.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class Importance(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


# --- Actions (the executable allowlist) ------------------------------------

class MoveToAction(BaseModel):
    type: Literal["move_to"] = "move_to"
    x: float
    y: float
    reason: Optional[str] = None


class MoveToLocationAction(BaseModel):
    type: Literal["move_to_location"] = "move_to_location"
    location: str


class TalkToAgentAction(BaseModel):
    type: Literal["talk_to_agent"] = "talk_to_agent"
    target: str
    message: str


class ObserveAreaAction(BaseModel):
    type: Literal["observe_area"] = "observe_area"
    radius: float = 200.0


class InspectObjectAction(BaseModel):
    type: Literal["inspect_object"] = "inspect_object"
    object_id: str


class WorkAction(BaseModel):
    type: Literal["work"] = "work"
    target: Optional[str] = None
    note: Optional[str] = None


class CreateTaskAction(BaseModel):
    type: Literal["create_task"] = "create_task"
    name: str
    description: str = ""
    assignee: Optional[str] = None
    priority: int = 5


class CompleteTaskAction(BaseModel):
    type: Literal["complete_task"] = "complete_task"
    task_id: str


class IdleAction(BaseModel):
    type: Literal["idle"] = "idle"
    reason: Optional[str] = None


Action = Union[
    MoveToAction,
    MoveToLocationAction,
    TalkToAgentAction,
    ObserveAreaAction,
    InspectObjectAction,
    WorkAction,
    CreateTaskAction,
    CompleteTaskAction,
    IdleAction,
]

ACTION_TYPES = [
    "move_to", "move_to_location", "talk_to_agent", "observe_area",
    "inspect_object", "work", "create_task", "complete_task", "idle",
]


class MemoryWrite(BaseModel):
    should_store: bool = False
    content: str = ""
    importance: Importance = Importance.low


class Decision(BaseModel):
    """The one structure every agent decision must conform to."""
    summary: str = Field(default="", description="Safe one-line summary, not raw chain-of-thought")
    intent: str = "idle"
    speech: Optional[str] = Field(default=None, description="Public utterance, if any")
    action: Action = Field(default_factory=IdleAction)
    memory: MemoryWrite = Field(default_factory=MemoryWrite)

    @field_validator("summary", "intent", mode="before")
    @classmethod
    def _coerce_str(cls, v):
        return "" if v is None else str(v)


class ChatReply(BaseModel):
    """Structured reply when the user chats to an agent."""
    reply: str
    action: Optional[Action] = None
    memory: MemoryWrite = Field(default_factory=MemoryWrite)
