"""Memory provider interface.

The agent runtime depends only on :class:`MemoryProvider`, so new backends
(SQLite, Chroma, Qdrant, Postgres, a vector DB…) can be dropped in without
touching agents. See :class:`app.memory.obsidian.ObsidianMemoryProvider` for
the reference implementation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Memory:
    id: str
    agent: str
    content: str
    importance: str = "low"          # low | medium | high | critical
    kind: str = "memory"             # memory | observation | relationship | conversation | research
    source: str = "world"            # world | conversation | agent_interaction | obsidian
    created: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def new(agent: str, content: str, importance: str = "low", kind: str = "memory",
            source: str = "world", tags: Optional[List[str]] = None) -> "Memory":
        return Memory(id=uuid.uuid4().hex[:12], agent=agent, content=content,
                      importance=importance, kind=kind, source=source, tags=tags or [])

    def to_dict(self) -> dict:
        return {
            "id": self.id, "agent": self.agent, "content": self.content,
            "importance": self.importance, "kind": self.kind, "source": self.source,
            "created": self.created, "tags": self.tags,
        }


IMPORTANCE_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class MemoryProvider:
    async def remember(self, memory: Memory) -> Memory:
        raise NotImplementedError

    async def recall(self, agent: str, query: str, limit: int = 5) -> List[Memory]:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 10) -> List[Memory]:
        raise NotImplementedError

    async def summarize(self, agent: str, limit: int = 20) -> str:
        raise NotImplementedError

    async def forget(self, agent: str, memory_id: str) -> bool:
        raise NotImplementedError

    async def all_for(self, agent: str) -> List[Memory]:
        raise NotImplementedError
