"""The handle every tool receives: backend access, session identity, memory."""

from __future__ import annotations

from dataclasses import dataclass

from agent.backend.client import RestaurantClient
from agent.conversation import Session
from agent.memory.customer_memory import CustomerMemory


@dataclass
class ToolContext:
    backend: RestaurantClient
    session: Session
    memory: CustomerMemory
