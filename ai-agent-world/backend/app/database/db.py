"""SQLite persistence for *application state*.

SQLite holds structured, machine state: agents, tasks, workflows, events,
conversations, world state, relationships, configuration, simulation state and
repair history. Human-readable long-term knowledge lives in Obsidian instead.

sqlite3 is synchronous; calls are cheap and run inside the asyncio loop's
default executor via :meth:`_run` so they never block the event loop for long.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    category TEXT NOT NULL,
    data TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    sender TEXT NOT NULL,
    target TEXT,
    message TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS world_state (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS relationships (
    agent_id TEXT NOT NULL,
    other_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (agent_id, other_id)
);
CREATE TABLE IF NOT EXISTS configuration (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simulation_state (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repair_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data TEXT NOT NULL,
    ts REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (DATA_DIR / "app.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    async def _run(self, fn, *args):
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn, *args)

    # --- generic key/value style upserts -----------------------------------
    async def upsert(self, table: str, id_: str, data: Dict[str, Any]) -> None:
        def _do():
            self._conn.execute(
                f"INSERT INTO {table} (id, data, updated_at) VALUES (?,?,?) "
                f"ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
                (id_, json.dumps(data), time.time()),
            )
            self._conn.commit()
        await self._run(_do)

    async def get(self, table: str, id_: str) -> Optional[Dict[str, Any]]:
        def _do():
            row = self._conn.execute(f"SELECT data FROM {table} WHERE id=?", (id_,)).fetchone()
            return json.loads(row["data"]) if row else None
        return await self._run(_do)

    async def all(self, table: str) -> List[Dict[str, Any]]:
        def _do():
            rows = self._conn.execute(f"SELECT data FROM {table}").fetchall()
            return [json.loads(r["data"]) for r in rows]
        return await self._run(_do)

    async def delete(self, table: str, id_: str) -> None:
        def _do():
            self._conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))
            self._conn.commit()
        await self._run(_do)

    # --- events ------------------------------------------------------------
    async def log_event(self, type_: str, category: str, data: Dict[str, Any], ts: float) -> None:
        def _do():
            self._conn.execute(
                "INSERT INTO events (type, category, data, ts) VALUES (?,?,?,?)",
                (type_, category, json.dumps(data), ts),
            )
            self._conn.commit()
        await self._run(_do)

    async def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        def _do():
            rows = self._conn.execute(
                "SELECT type, category, data, ts FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                {"type": r["type"], "category": r["category"], "data": json.loads(r["data"]), "ts": r["ts"]}
                for r in reversed(rows)
            ]
        return await self._run(_do)

    # --- conversations -----------------------------------------------------
    async def log_message(self, channel: str, sender: str, target: Optional[str], message: str) -> None:
        def _do():
            self._conn.execute(
                "INSERT INTO conversations (channel, sender, target, message, ts) VALUES (?,?,?,?,?)",
                (channel, sender, target, message, time.time()),
            )
            self._conn.commit()
        await self._run(_do)

    async def chat_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        def _do():
            rows = self._conn.execute(
                "SELECT channel, sender, target, message, ts FROM conversations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
        return await self._run(_do)

    # --- simulation state --------------------------------------------------
    async def set_state(self, key: str, value: Any) -> None:
        def _do():
            self._conn.execute(
                "INSERT INTO simulation_state (key, data) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (key, json.dumps(value)),
            )
            self._conn.commit()
        await self._run(_do)

    async def get_state(self, key: str, default: Any = None) -> Any:
        def _do():
            row = self._conn.execute("SELECT data FROM simulation_state WHERE key=?", (key,)).fetchone()
            return json.loads(row["data"]) if row else default
        return await self._run(_do)

    # --- relationships -----------------------------------------------------
    async def set_relationship(self, agent_id: str, other_id: str, data: Dict[str, Any]) -> None:
        def _do():
            self._conn.execute(
                "INSERT INTO relationships (agent_id, other_id, data) VALUES (?,?,?) "
                "ON CONFLICT(agent_id, other_id) DO UPDATE SET data=excluded.data",
                (agent_id, other_id, json.dumps(data)),
            )
            self._conn.commit()
        await self._run(_do)

    async def relationships_for(self, agent_id: str) -> Dict[str, Dict[str, Any]]:
        def _do():
            rows = self._conn.execute(
                "SELECT other_id, data FROM relationships WHERE agent_id=?", (agent_id,)
            ).fetchall()
            return {r["other_id"]: json.loads(r["data"]) for r in rows}
        return await self._run(_do)

    # --- repair history ----------------------------------------------------
    async def log_repair(self, data: Dict[str, Any]) -> None:
        def _do():
            self._conn.execute("INSERT INTO repair_history (data, ts) VALUES (?,?)", (json.dumps(data), time.time()))
            self._conn.commit()
        await self._run(_do)

    async def repair_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _do():
            rows = self._conn.execute(
                "SELECT data, ts FROM repair_history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [{**json.loads(r["data"]), "ts": r["ts"]} for r in rows]
        return await self._run(_do)

    def close(self) -> None:
        self._conn.close()


_db: Optional[Database] = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
