from __future__ import annotations

import pytest

from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory
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
def ctx(backend, priya_session, memory) -> ToolContext:
    return ToolContext(backend=backend, session=priya_session, memory=memory)
