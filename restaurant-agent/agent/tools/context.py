"""The handle every tool receives: backend access, session identity, memory."""

from __future__ import annotations

from dataclasses import dataclass

from agent.backend.protocol import BackendClient
from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory


@dataclass
class ToolContext:
    backend: BackendClient
    session: Session
    memory: CustomerMemory
