"""Obsidian-backed memory + knowledge store.

Long-term, human-readable knowledge lives as Markdown notes with YAML
frontmatter inside a single configured vault. **All** file access is confined
to that vault by :meth:`ObsidianVault._safe` — an agent can never read or write
a path outside it, which is central to the security model.

Retrieval is targeted (keyword scoring), never "load the whole vault into the
prompt". The interface is deliberately swappable for a vector DB later.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import get_config
from .base import IMPORTANCE_RANK, Memory, MemoryProvider

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def _yaml_frontmatter(fields: Dict[str, object]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    out: Dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            out[key.strip()] = val.strip()
    return out


class ObsidianVault:
    """Low-level, sandboxed file access to the configured vault."""

    def __init__(self, vault_path: Optional[Path] = None, root_folder: str = "AI-Agents"):
        cfg = get_config()
        self.vault_path = (vault_path or cfg.vault_path).resolve()
        self.root_folder = root_folder
        self.root = self.vault_path / root_folder
        self.available = True
        try:
            self._ensure_structure()
        except OSError:
            self.available = False

    # --- security guard ----------------------------------------------------
    def _safe(self, relative: str) -> Path:
        """Resolve ``relative`` under the vault, refusing any escape."""
        target = (self.root / relative).resolve()
        if not str(target).startswith(str(self.root)):
            raise PermissionError(f"Path escapes vault sandbox: {relative}")
        return target

    def _ensure_structure(self) -> None:
        for sub in ("Agents", "Memories", "Conversations", "Tasks", "Workflows",
                    "World", "Research", "Logs", "System/Repairs"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # --- generic note ops (used by tools, all sandboxed) -------------------
    def read_note(self, relative: str) -> Optional[str]:
        path = self._safe(relative if relative.endswith(".md") else relative + ".md")
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def write_note(self, relative: str, content: str) -> str:
        path = self._safe(relative if relative.endswith(".md") else relative + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(self.vault_path))

    def append_note(self, relative: str, content: str) -> str:
        path = self._safe(relative if relative.endswith(".md") else relative + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(content)
        return str(path.relative_to(self.vault_path))

    def search_notes(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        tokens = set(_tokenize(query))
        results = []
        for path in self.root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            score = sum(1 for t in _tokenize(text) if t in tokens)
            if score:
                results.append({"path": str(path.relative_to(self.vault_path)),
                                "score": score, "excerpt": text[:200]})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    def open_location(self) -> str:
        return str(self.root)


class ObsidianMemoryProvider(MemoryProvider):
    def __init__(self, vault: Optional[ObsidianVault] = None):
        self.vault = vault or ObsidianVault(root_folder=get_config().get("obsidian", "root_folder", default="AI-Agents"))
        # In-memory index mirrors the on-disk notes for fast retrieval.
        self._index: Dict[str, List[Memory]] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        mem_root = self.vault.root / "Memories"
        if not mem_root.exists():
            return
        for path in mem_root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = _parse_frontmatter(text)
            agent = fm.get("agent", path.parent.name)
            body = text.split("---", 2)[-1].strip() if "---" in text else text
            mem = Memory(
                id=fm.get("id", path.stem),
                agent=agent,
                content=body,
                importance=fm.get("importance", "low"),
                kind=fm.get("type", "memory"),
                source=fm.get("source", "obsidian"),
            )
            self._index.setdefault(agent, []).append(mem)

    def _mem_filename(self, mem: Memory) -> str:
        stamp = datetime.fromtimestamp(mem.created).strftime("%Y%m%d-%H%M%S")
        return f"Memories/{mem.agent}/{stamp}-{mem.id}.md"

    async def remember(self, memory: Memory) -> Memory:
        self._index.setdefault(memory.agent, []).append(memory)
        if self.vault.available:
            fm = _yaml_frontmatter({
                "agent": memory.agent, "type": memory.kind, "importance": memory.importance,
                "source": memory.source, "id": memory.id,
                "created": datetime.fromtimestamp(memory.created).strftime("%Y-%m-%d %H:%M"),
                "tags": memory.tags,
            })
            self.vault.write_note(self._mem_filename(memory), f"{fm}\n\n{memory.content}\n")
        return memory

    async def recall(self, agent: str, query: str, limit: int = 5) -> List[Memory]:
        memories = self._index.get(agent, [])
        if not memories:
            return []
        tokens = set(_tokenize(query))

        def score(m: Memory) -> float:
            overlap = sum(1 for t in _tokenize(m.content) if t in tokens)
            recency = 1.0 / (1.0 + (time.time() - m.created) / 3600.0)
            return overlap * 2 + IMPORTANCE_RANK.get(m.importance, 0) * 1.5 + recency

        ranked = sorted(memories, key=score, reverse=True)
        return ranked[:limit]

    async def search(self, query: str, limit: int = 10) -> List[Memory]:
        tokens = set(_tokenize(query))
        scored = []
        for mems in self._index.values():
            for m in mems:
                overlap = sum(1 for t in _tokenize(m.content) if t in tokens)
                if overlap:
                    scored.append((overlap, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:limit]]

    async def summarize(self, agent: str, limit: int = 20) -> str:
        mems = sorted(self._index.get(agent, []), key=lambda m: m.created, reverse=True)[:limit]
        if not mems:
            return f"{agent} has no recorded memories yet."
        lines = [f"- ({m.importance}) {m.content}" for m in mems]
        return f"{agent} remembers:\n" + "\n".join(lines)

    async def forget(self, agent: str, memory_id: str) -> bool:
        mems = self._index.get(agent, [])
        for i, m in enumerate(mems):
            if m.id == memory_id:
                mems.pop(i)
                return True
        return False

    async def all_for(self, agent: str) -> List[Memory]:
        return sorted(self._index.get(agent, []), key=lambda m: m.created, reverse=True)


_provider: Optional[ObsidianMemoryProvider] = None
_vault: Optional[ObsidianVault] = None


def get_vault() -> ObsidianVault:
    global _vault
    if _vault is None:
        _vault = ObsidianVault(root_folder=get_config().get("obsidian", "root_folder", default="AI-Agents"))
    return _vault


def get_memory_provider() -> ObsidianMemoryProvider:
    global _provider
    if _provider is None:
        _provider = ObsidianMemoryProvider(vault=get_vault())
    return _provider
