"""Error types for the backend HTTP layer."""

from __future__ import annotations


class BackendError(Exception):
    """Raised when the restaurant API returns a non-2xx response, or is unreachable.

    Carries the HTTP status code and the human-readable ``detail`` string so the
    tool layer can hand a clean message back to the LLM instead of a stack trace.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"[{status_code}] {detail}")
        self.status_code = status_code
        self.detail = detail
