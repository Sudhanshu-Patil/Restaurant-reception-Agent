"""The single shape every tool returns.

A tool never raises to the agent loop and never returns a bare dict. It returns a
:class:`ToolResult`, which the registry serialises to JSON for the model. ``ok``
tells the model whether to continue or recover; ``error_code`` lets the agent loop
and tests branch on *why* without string-matching.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ErrorCode(str, Enum):
    # --- model / client misbehaviour (caught in the registry) ---
    UNKNOWN_TOOL = "unknown_tool"
    BAD_JSON = "bad_json"
    BAD_ARGUMENTS = "bad_arguments"

    # --- preconditions the tool itself enforces ---
    NO_ACTIVE_RESERVATION = "no_active_reservation"
    OWNERSHIP_DENIED = "ownership_denied"
    NOT_MODIFIABLE = "not_modifiable"
    BAD_DATETIME = "bad_datetime"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_CLARIFICATION = "needs_clarification"

    # --- domain outcomes ---
    NO_TABLE_AVAILABLE = "no_table_available"
    ALLERGEN_CONFLICT = "allergen_conflict"
    ITEM_UNAVAILABLE = "item_unavailable"
    ITEM_NOT_ON_MENU = "item_not_on_menu"

    # --- upstream / infrastructure ---
    UPSTREAM_ERROR = "upstream_error"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    INTERNAL_ERROR = "internal_error"


class ToolResult(BaseModel):
    """Uniform envelope for every tool call outcome."""

    ok: bool
    message: str = ""
    data: Any = None
    error_code: ErrorCode | None = None
    http_status: int | None = None

    model_config = {"use_enum_values": True}

    @classmethod
    def success(cls, message: str = "", **data: Any) -> ToolResult:
        return cls(ok=True, message=message, data=data or None)

    @classmethod
    def failure(
        cls,
        error_code: ErrorCode,
        message: str,
        *,
        http_status: int | None = None,
        **data: Any,
    ) -> ToolResult:
        return cls(
            ok=False,
            error_code=error_code,
            message=message,
            http_status=http_status,
            data=data or None,
        )

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)
