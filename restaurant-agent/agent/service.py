"""Composition root - wires the agent to a backend, an LLM, and a session store,
and owns the one-turn orchestration (identity, idempotency, persistence, memory
refresh) that both the REST and SSE endpoints share.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agent.agent import ReceptionAgent
from agent.backend.client import RestaurantClient
from agent.backend.protocol import BackendClient
from agent.config import Settings
from agent.events import TurnEvent
from agent.identity import resolve_customer
from agent.llm.base import LLMClient
from agent.llm.groq import GroqClient
from agent.memory.customer_memory import CustomerMemory
from agent.observability import get_logger
from agent.store import SessionStore
from agent.tools.catalog import registry
from agent.trace import TurnTrace

_log = get_logger("agent.service")

_PREFERENCE_TOOLS = {"remember_preference"}


class SessionNotFound(KeyError):
    pass


class IdempotencyConflict(Exception):
    """Same client_message_id replayed with a different message body."""


@dataclass
class AgentService:
    settings: Settings
    backend: BackendClient
    llm: LLMClient
    agent: ReceptionAgent
    store: SessionStore
    _memory_cache: dict[int, CustomerMemory] = field(default_factory=dict, repr=False)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        backend: BackendClient | None = None,
        llm: LLMClient | None = None,
    ) -> AgentService:
        backend = backend or RestaurantClient(
            settings.restaurant_api_url, timeout=settings.request_timeout
        )
        llm = llm or GroqClient(
            settings.groq_api_key,
            settings.model,
            settings.groq_base_url,
            settings.request_timeout,
        )
        agent = ReceptionAgent(llm, registry, backend, settings)
        store = SessionStore(settings.db_path)
        store.purge_expired(settings.session_ttl_days)
        return cls(settings, backend, llm, agent, store)

    # -- sessions ------------------------------------------------------
    def start_session(
        self, *, phone: str | None, email: str | None, name: str | None
    ) -> dict[str, Any]:
        session = resolve_customer(self.backend, phone=phone, email=email, name=name)
        session_id = self.store.create(session)
        memory = self._memory(session.customer_id, refresh=True)
        _log.info(
            "session.start", extra={"session_id": session_id, "customer_id": session.customer_id}
        )
        return {
            "session_id": session_id,
            "customer": {"id": session.customer_id, "name": session.name},
            "memory": memory.summary(),
        }

    # -- one turn (blocking, idempotent) --------------------------------
    def run_message(
        self, session_id: str, message: str, client_message_id: str | None = None
    ) -> dict[str, Any]:
        loaded = self.store.load(session_id)
        if loaded is None:
            raise SessionNotFound(session_id)
        session, conversation = loaded

        request_hash = _hash(message)
        if client_message_id:
            prior = self.store.get_processed(session_id, client_message_id)
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflict(client_message_id)
                _log.info("message.replay", extra={"session_id": session_id})
                return dict(prior["response"])

        memory = self._memory(session.customer_id)
        result = self.agent.run_turn(message, conversation, session, memory)
        self.store.save(session_id, session, conversation)
        self._maybe_refresh_memory(session.customer_id, result.trace)

        response: dict[str, Any] = {
            "reply": result.reply,
            "stopped_reason": result.stopped_reason,
            "tool_calls": [s.name for s in result.trace.steps],
            "trace": result.trace.render(),
        }
        if client_message_id:
            self.store.record_processed(session_id, client_message_id, request_hash, response)
        _log.info(
            "message.done",
            extra={
                "session_id": session_id,
                "stopped_reason": result.stopped_reason,
                "llm_rounds": result.trace.llm_rounds,
                "tool_calls": result.trace.tool_calls,
                "mutations": result.trace.mutations,
            },
        )
        return response

    # -- one turn (streamed) -------------------------------------------
    def stream_message(self, session_id: str, message: str) -> Iterator[TurnEvent]:
        loaded = self.store.load(session_id)
        if loaded is None:
            raise SessionNotFound(session_id)
        session, conversation = loaded
        memory = self._memory(session.customer_id)

        runner = self.agent.turn(message, conversation, session, memory)
        yield from runner.events()

        self.store.save(session_id, session, conversation)
        self._maybe_refresh_memory(session.customer_id, runner.trace)
        _log.info(
            "message.stream_done",
            extra={"session_id": session_id, "stopped_reason": runner.trace.stopped_reason},
        )

    # -- readiness ----------------------------------------------
    def ready(self) -> dict[str, bool]:
        checks = {"database": self.store.ready(), "backend": self._backend_ready()}
        return checks

    def _backend_ready(self) -> bool:
        try:
            self.backend.list_tables()
            return True
        except Exception:
            return False

    # -- memory cache -------------------------------------------
    def _memory(self, customer_id: int, *, refresh: bool = False) -> CustomerMemory:
        if refresh or customer_id not in self._memory_cache:
            memory = CustomerMemory(self.backend, customer_id)
            memory.load()
            self._memory_cache[customer_id] = memory
        return self._memory_cache[customer_id]

    def _maybe_refresh_memory(self, customer_id: int, trace: TurnTrace) -> None:
        if any(step.name in _PREFERENCE_TOOLS for step in trace.steps):
            self._memory(customer_id, refresh=True)


def _hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()
