from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from agent.config import Settings
from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory
from agent.tools.catalog import registry
from agent.tools.context import ToolContext

from tests.fakes import FakeBackend


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def priya_session() -> Session:
    return Session(customer_id=1, name="Priya Sharma", phone="+91-9876543210")


@pytest.fixture
def memory(backend: FakeBackend) -> CustomerMemory:
    mem = CustomerMemory(backend, customer_id=1)
    mem.load()
    return mem


@pytest.fixture
def ctx(backend: FakeBackend, priya_session: Session, memory: CustomerMemory) -> ToolContext:
    return ToolContext(backend=backend, session=priya_session, memory=memory)


@pytest.fixture
def settings() -> Settings:
    return Settings(groq_api_key="test-key", model="test-model", restaurant_api_url="http://x")


@pytest.fixture
def call(ctx: ToolContext) -> Callable[..., dict[str, Any]]:
    """Dispatch a tool by name and return its result as a plain dict."""

    def _call(name: str, **arguments: Any) -> dict[str, Any]:
        return registry.dispatch(name, json.dumps(arguments), ctx).model_dump()

    return _call
