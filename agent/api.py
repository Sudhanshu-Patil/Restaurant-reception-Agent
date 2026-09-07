"""REST interface + a minimal browser chat UI.

    uvicorn agent.api:app --port 8100

    GET  /                                -> single-page chat UI
    POST /sessions                       {phone|email, name?}   -> {session_id, ...}
    POST /sessions/{session_id}/messages {content, verbose?}    -> {reply, trace?}

Sessions are held in memory — fine for a single-process demo.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.agent import ReceptionAgent
from agent.backend.client import RestaurantClient
from agent.backend.errors import BackendError
from agent.config import Settings
from agent.conversation import Conversation, Session
from agent.identity import NewCustomerNeedsName, resolve_customer
from agent.llm.groq import GroqClient
from agent.memory.customer_memory import CustomerMemory
from agent.tools.catalog import registry

_settings = Settings.from_env()
_backend = RestaurantClient(_settings.restaurant_api_url, timeout=_settings.request_timeout)
_llm = GroqClient(
    _settings.groq_api_key,
    _settings.model,
    _settings.groq_base_url,
    _settings.request_timeout,
)
_agent = ReceptionAgent(_llm, registry, _backend, _settings)

app = FastAPI(title="Restaurant Reception Agent")

_WEB_DIR = Path(__file__).parent / "web"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_WEB_DIR / "index.html")


@dataclass
class _SessionState:
    session: Session
    memory: CustomerMemory
    conversation: Conversation


_SESSIONS: dict[str, _SessionState] = {}


class StartSessionIn(BaseModel):
    phone: str | None = None
    email: str | None = None
    name: str | None = None


class MessageIn(BaseModel):
    content: str
    verbose: bool = False


@app.post("/sessions")
def start_session(body: StartSessionIn) -> dict:
    if not body.phone and not body.email:
        raise HTTPException(400, "phone or email is required")
    try:
        session = resolve_customer(
            _backend, phone=body.phone, email=body.email, name=body.name
        )
    except NewCustomerNeedsName:
        raise HTTPException(400, "New customer — 'name' is required to create a profile")
    except BackendError as exc:
        raise HTTPException(exc.status_code, exc.detail)

    memory = CustomerMemory(_backend, session.customer_id)
    memory.load()
    sid = uuid.uuid4().hex
    _SESSIONS[sid] = _SessionState(session, memory, Conversation())
    return {
        "session_id": sid,
        "customer": {"id": session.customer_id, "name": session.name},
        "memory": memory.summary(),
    }


@app.post("/sessions/{session_id}/messages")
def send_message(session_id: str, body: MessageIn) -> dict:
    state = _SESSIONS.get(session_id)
    if state is None:
        raise HTTPException(404, "unknown session_id")
    reply, trace = _agent.run_turn(
        body.content, state.conversation, state.session, state.memory
    )
    result = {"reply": reply}
    if body.verbose:
        result["trace"] = trace.render()
    return result
