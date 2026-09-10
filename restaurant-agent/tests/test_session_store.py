"""SQLite session store: persistence, idempotency ledger, housekeeping."""

from __future__ import annotations

from datetime import datetime, timedelta

from agent.conversation import Session
from agent.store import SessionStore


def _store(tmp_path) -> SessionStore:
    return SessionStore(tmp_path / "s.db")


def test_create_and_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    sid = store.create(Session(customer_id=7, name="Ada", email="ada@x.io"))
    loaded = store.load(sid)
    assert loaded is not None
    session, conversation = loaded
    assert session.customer_id == 7
    assert session.name == "Ada"
    assert conversation.messages == []


def test_load_unknown_session_is_none(tmp_path):
    assert _store(tmp_path).load("nope") is None


def test_save_persists_transcript_and_active_reservation(tmp_path):
    store = _store(tmp_path)
    sid = store.create(Session(customer_id=1, name="Ada"))
    session, conversation = store.load(sid)  # type: ignore[misc]
    conversation.add_user("hi")
    session.active_reservation_id = 42
    store.save(sid, session, conversation)

    reloaded_session, reloaded_convo = store.load(sid)  # type: ignore[misc]
    assert reloaded_session.active_reservation_id == 42
    assert reloaded_convo.messages == [{"role": "user", "content": "hi"}]


def test_idempotency_ledger(tmp_path):
    store = _store(tmp_path)
    assert store.get_processed("s1", "m1") is None
    store.record_processed("s1", "m1", "hash-a", {"reply": "hello"})
    hit = store.get_processed("s1", "m1")
    assert hit == {"request_hash": "hash-a", "response": {"reply": "hello"}}


def test_purge_expired_removes_old_rows(tmp_path):
    store = _store(tmp_path)
    sid = store.create(Session(customer_id=1, name="Ada"))
    # backdate it
    with store._conn() as conn:
        old = (datetime.now() - timedelta(days=99)).isoformat()
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (old, sid))
        conn.commit()
    removed = store.purge_expired(ttl_days=30)
    assert removed == 1
    assert store.load(sid) is None


def test_ready(tmp_path):
    assert _store(tmp_path).ready() is True


def test_persistence_survives_a_new_store_instance(tmp_path):
    db = tmp_path / "shared.db"
    sid = SessionStore(db).create(Session(customer_id=5, name="Grace"))
    # a fresh process would build a new SessionStore over the same file
    assert SessionStore(db).load(sid) is not None
