"""Per-request correlation id, propagated via a context variable."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def bind_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()
