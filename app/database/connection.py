"""SQLite access. One short-lived connection per unit of work (safe across threads/processes)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    platform         TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    chat_id          TEXT,
    timezone         TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    entry_date       TEXT NOT NULL,
    description      TEXT NOT NULL,
    category         TEXT,
    duration_minutes INTEGER,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diary_user_date ON diary_entries (user_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_diary_user_created ON diary_entries (user_id, created_at);

CREATE TABLE IF NOT EXISTS reminders (
    id                  INTEGER PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES users(id),
    message             TEXT NOT NULL,
    scheduled_at_utc    TEXT NOT NULL,
    timezone            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sent', 'cancelled', 'failed')),
    sent_at_utc         TEXT,
    last_attempt_at_utc TEXT,
    lease_until_utc     TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rem_status_time ON reminders (status, scheduled_at_utc);
CREATE INDEX IF NOT EXISTS idx_rem_user_status_time ON reminders (user_id, status, scheduled_at_utc);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)  # executescript manages its own transaction
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Run a block in a write transaction; commit on success, roll back on error."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
