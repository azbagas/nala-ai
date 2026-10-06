"""SQLite (WAL) data access layer (PRD §6.2, §7).

Implements persistence for messages, facts, open threads, summaries,
daily life, proactive plans, state, and LLM call observability.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from app.utils.time import now_utc, to_utc_iso

log = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Every message in the conversation
CREATE TABLE IF NOT EXISTS messages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  role           TEXT NOT NULL CHECK (role IN ('user', 'bot')),
  content        TEXT NOT NULL,
  origin         TEXT NOT NULL DEFAULT 'reply'
                 CHECK (origin IN ('reply', 'proactive', 'system')),
  proactive_type TEXT,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- Durable facts about the user
CREATE TABLE IF NOT EXISTS facts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  category          TEXT NOT NULL,
  content           TEXT NOT NULL,
  confidence        REAL DEFAULT 0.8,
  active            INTEGER NOT NULL DEFAULT 1,
  superseded_by     INTEGER REFERENCES facts(id),
  source_message_id INTEGER REFERENCES messages(id),
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

-- Things to follow up on
CREATE TABLE IF NOT EXISTS open_threads (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  topic          TEXT NOT NULL,
  detail         TEXT,
  target_date    TEXT,
  followup_after TEXT,
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'followed_up', 'resolved', 'expired')),
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- Compressed history
CREATE TABLE IF NOT EXISTS summaries (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  scope        TEXT NOT NULL CHECK (scope IN ('daily', 'rolling')),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  content      TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

-- Her simulated life, per day
CREATE TABLE IF NOT EXISTS daily_life (
  date          TEXT PRIMARY KEY,
  schedule_json TEXT NOT NULL,
  mood          TEXT,
  energy        TEXT,
  notes         TEXT,
  created_at    TEXT NOT NULL
);

-- Planned proactive sends
CREATE TABLE IF NOT EXISTS proactive_plan (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  window_name TEXT NOT NULL,
  planned_at  TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'planned'
              CHECK (status IN ('planned', 'sent', 'skipped', 'failed')),
  skip_reason TEXT,
  message_id  INTEGER REFERENCES messages(id),
  UNIQUE(date, window_name)
);

-- Key-value runtime state
CREATE TABLE IF NOT EXISTS state (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- Observability for model calls
CREATE TABLE IF NOT EXISTS llm_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  purpose       TEXT,
  model         TEXT,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  latency_ms    INTEGER,
  success       INTEGER,
  created_at    TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class MessageRecord:
    id: int
    role: str
    content: str
    origin: str
    proactive_type: str | None
    created_at: str


@dataclass(frozen=True)
class FactRecord:
    id: int
    category: str
    content: str
    confidence: float
    active: bool
    superseded_by: int | None
    source_message_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ThreadRecord:
    id: int
    topic: str
    detail: str | None
    target_date: str | None
    followup_after: str | None
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SummaryRecord:
    id: int
    scope: str
    period_start: str
    period_end: str
    content: str
    created_at: str


class MemoryStore:
    """Async interface for SQLite storage."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialized = False

    async def init_db(self) -> None:
        """Create tables and set WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()
        self._initialized = True
        log.info("database_initialized path=%s", self.db_path)

    @asynccontextmanager
    async def _connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        if not self._initialized:
            await self.init_db()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys=ON;")
            yield conn

    # -----------------------------------------------------------------------
    # Messages
    # -----------------------------------------------------------------------

    async def save_message(
        self,
        role: str,
        content: str,
        origin: str = "reply",
        proactive_type: str | None = None,
        created_at: datetime | None = None,
    ) -> int:
        ts = to_utc_iso(created_at or now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "INSERT INTO messages (role, content, origin, proactive_type, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (role, content.strip(), origin, proactive_type, ts),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_recent_messages(self, limit: int = 24) -> list[MessageRecord]:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, role, content, origin, proactive_type, created_at "
                "FROM (SELECT * FROM messages ORDER BY id DESC LIMIT ?) "
                "ORDER BY id ASC",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                MessageRecord(
                    id=r["id"],
                    role=r["role"],
                    content=r["content"],
                    origin=r["origin"],
                    proactive_type=r["proactive_type"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    async def get_messages_since_id(self, since_id: int) -> list[MessageRecord]:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, role, content, origin, proactive_type, created_at "
                "FROM messages WHERE id > ? ORDER BY id ASC",
                (since_id,),
            )
            rows = await cursor.fetchall()
            return [
                MessageRecord(
                    id=r["id"],
                    role=r["role"],
                    content=r["content"],
                    origin=r["origin"],
                    proactive_type=r["proactive_type"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    # -----------------------------------------------------------------------
    # Facts
    # -----------------------------------------------------------------------

    async def add_fact(
        self,
        category: str,
        content: str,
        confidence: float = 0.8,
        source_message_id: int | None = None,
    ) -> int:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "INSERT INTO facts (category, content, confidence, active, source_message_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (category.strip().lower(), content.strip(), confidence, source_message_id, ts, ts),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def supersede_fact(
        self,
        old_fact_id: int,
        new_content: str,
        category: str | None = None,
        confidence: float = 0.8,
        source_message_id: int | None = None,
    ) -> int:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            if not category:
                cur = await db.execute("SELECT category FROM facts WHERE id = ?", (old_fact_id,))
                row = await cur.fetchone()
                category = row["category"] if row else "other"

            cur = await db.execute(
                "INSERT INTO facts (category, content, confidence, active, source_message_id, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (category, new_content.strip(), confidence, source_message_id, ts, ts),
            )
            new_id = cur.lastrowid or 0

            await db.execute(
                "UPDATE facts SET active = 0, superseded_by = ?, updated_at = ? WHERE id = ?",
                (new_id, ts, old_fact_id),
            )
            await db.commit()
            return new_id

    async def get_active_facts(self) -> list[FactRecord]:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, category, content, confidence, active, superseded_by, source_message_id, created_at, updated_at "
                "FROM facts WHERE active = 1 ORDER BY category ASC, id ASC"
            )
            rows = await cursor.fetchall()
            return [
                FactRecord(
                    id=r["id"],
                    category=r["category"],
                    content=r["content"],
                    confidence=r["confidence"],
                    active=bool(r["active"]),
                    superseded_by=r["superseded_by"],
                    source_message_id=r["source_message_id"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    async def deactivate_fact(self, fact_id: int) -> bool:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "UPDATE facts SET active = 0, updated_at = ? WHERE id = ? AND active = 1",
                (ts, fact_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    # -----------------------------------------------------------------------
    # Open Threads
    # -----------------------------------------------------------------------

    async def add_thread(
        self,
        topic: str,
        detail: str | None = None,
        target_date: str | None = None,
        followup_after: str | None = None,
    ) -> int:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "INSERT INTO open_threads (topic, detail, target_date, followup_after, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (topic.strip(), detail.strip() if detail else None, target_date, followup_after, ts, ts),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_pending_threads(self) -> list[ThreadRecord]:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, topic, detail, target_date, followup_after, status, created_at, updated_at "
                "FROM open_threads WHERE status = 'pending' ORDER BY id ASC"
            )
            rows = await cursor.fetchall()
            return [
                ThreadRecord(
                    id=r["id"],
                    topic=r["topic"],
                    detail=r["detail"],
                    target_date=r["target_date"],
                    followup_after=r["followup_after"],
                    status=r["status"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    async def resolve_thread(self, thread_id: int) -> bool:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "UPDATE open_threads SET status = 'resolved', updated_at = ? WHERE id = ?",
                (ts, thread_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_thread(self, thread_id: int) -> bool:
        async with self._connection() as db:
            cursor = await db.execute("DELETE FROM open_threads WHERE id = ?", (thread_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def expire_threads_before(self, cutoff_date: str) -> int:
        """Mark pending threads whose target_date is older than cutoff_date as expired."""
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "UPDATE open_threads SET status = 'expired', updated_at = ? "
                "WHERE status = 'pending' AND target_date IS NOT NULL AND target_date < ?",
                (ts, cutoff_date),
            )
            await db.commit()
            return cursor.rowcount

    # -----------------------------------------------------------------------
    # Summaries
    # -----------------------------------------------------------------------

    async def add_summary(
        self,
        scope: str,
        period_start: str,
        period_end: str,
        content: str,
    ) -> int:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            cursor = await db.execute(
                "INSERT INTO summaries (scope, period_start, period_end, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope, period_start, period_end, content.strip(), ts),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_latest_summary(self) -> SummaryRecord | None:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, scope, period_start, period_end, content, created_at "
                "FROM summaries ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return SummaryRecord(
                id=row["id"],
                scope=row["scope"],
                period_start=row["period_start"],
                period_end=row["period_end"],
                content=row["content"],
                created_at=row["created_at"],
            )

    async def get_recent_daily_summaries(self, limit: int = 7) -> list[SummaryRecord]:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT id, scope, period_start, period_end, content, created_at "
                "FROM summaries WHERE scope = 'daily' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                SummaryRecord(
                    id=r["id"],
                    scope=r["scope"],
                    period_start=r["period_start"],
                    period_end=r["period_end"],
                    content=r["content"],
                    created_at=r["created_at"],
                )
                for r in reversed(rows)
            ]

    # -----------------------------------------------------------------------
    # Runtime State
    # -----------------------------------------------------------------------

    async def get_state(self, key: str, default: str | None = None) -> str | None:
        async with self._connection() as db:
            cursor = await db.execute("SELECT value FROM state WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row["value"] if row else default

    async def set_state(self, key: str, value: str) -> None:
        async with self._connection() as db:
            await db.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            await db.commit()

    # -----------------------------------------------------------------------
    # LLM Observability
    # -----------------------------------------------------------------------

    async def log_llm_call(
        self,
        purpose: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int,
        success: bool,
    ) -> None:
        ts = to_utc_iso(now_utc())
        async with self._connection() as db:
            await db.execute(
                "INSERT INTO llm_calls (purpose, model, input_tokens, output_tokens, latency_ms, success, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (purpose, model, input_tokens, output_tokens, latency_ms, 1 if success else 0, ts),
            )
            await db.commit()

    # -----------------------------------------------------------------------
    # Memory Reset (FR-3.7)
    # -----------------------------------------------------------------------

    async def reset_memory(self) -> None:
        """Wipes conversation history, facts, open threads, and summaries."""
        async with self._connection() as db:
            await db.execute("DELETE FROM facts;")
            await db.execute("DELETE FROM open_threads;")
            await db.execute("DELETE FROM summaries;")
            await db.execute("DELETE FROM messages;")
            await db.execute("DELETE FROM state WHERE key LIKE 'last_extracted%';")
            await db.commit()
        log.info("memory_reset_completed")
