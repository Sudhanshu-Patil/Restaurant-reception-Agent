"""SQLite-backed session persistence.

Keeps two things across process restarts:

* **sessions** - the customer identity plus the full conversation transcript, so
  the REST/browser interface survives a redeploy.
* **processed messages** - a ``(session_id, client_message_id)`` ledger so a
  retried request returns the stored reply instead of repeating side effects.

Stdlib ``sqlite3`` only, one short-lived connection per call (WAL mode), so it is
safe to use from FastAPI's threadpool without a shared lock.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.conversation import Conversation, Session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id                    TEXT PRIMARY KEY,
    customer_id           INTEGER NOT NULL,
    name                  TEXT NOT NULL,
    phone                 TEXT,
    email                 TEXT,
    active_reservation_id INTEGER,
    transcript            TEXT NOT NULL DEFAULT '[]',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_messages (
    session_id        TEXT NOT NULL,
    client_message_id TEXT NOT NULL,
    request_hash      TEXT NOT NULL,
    response          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (session_id, client_message_id)
);
"""


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # -- sessions ------------------------------------------------------
    def create(self, session: Session) -> str:
        session_id = uuid.uuid4().hex
        now = _now()
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT INTO sessions (id, customer_id, name, phone, email, "
                "active_reservation_id, transcript, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, '[]', ?, ?)",
                (
                    session_id,
                    session.customer_id,
                    session.name,
                    session.phone,
                    session.email,
                    session.active_reservation_id,
                    now,
                    now,
                ),
            )
        return session_id

    def load(self, session_id: str) -> tuple[Session, Conversation] | None:
        with closing(self._conn()) as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        session = Session(
            customer_id=row["customer_id"],
            name=row["name"],
            phone=row["phone"],
            email=row["email"],
            active_reservation_id=row["active_reservation_id"],
        )
        conversation = Conversation(messages=json.loads(row["transcript"]))
        return session, conversation

    def save(self, session_id: str, session: Session, conversation: Conversation) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "UPDATE sessions SET active_reservation_id = ?, transcript = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    session.active_reservation_id,
                    json.dumps(conversation.messages),
                    _now(),
                    session_id,
                ),
            )

    # -- idempotency -------------------------------------------------
    def get_processed(self, session_id: str, client_message_id: str) -> dict[str, Any] | None:
        with closing(self._conn()) as conn:
            row = conn.execute(
                "SELECT request_hash, response FROM processed_messages "
                "WHERE session_id = ? AND client_message_id = ?",
                (session_id, client_message_id),
            ).fetchone()
        if row is None:
            return None
        return {"request_hash": row["request_hash"], "response": json.loads(row["response"])}

    def record_processed(
        self,
        session_id: str,
        client_message_id: str,
        request_hash: str,
        response: dict[str, Any],
    ) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO processed_messages "
                "(session_id, client_message_id, request_hash, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, client_message_id, request_hash, json.dumps(response), _now()),
            )

    # -- housekeeping ----------------------------------------------
    def purge_expired(self, ttl_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat()
        with closing(self._conn()) as conn, conn:
            cur = conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            conn.execute("DELETE FROM processed_messages WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    def ready(self) -> bool:
        try:
            with closing(self._conn()) as conn:
                conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
