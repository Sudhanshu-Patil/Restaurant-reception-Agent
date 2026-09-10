"""REST + SSE interface, and a static browser chat UI.

uvicorn agent.api:app --port 8100

GET  /                                   single-page chat UI
GET  /health                             liveness
GET  /ready                              DB + upstream readiness
POST /sessions                           {phone|email, name?}
POST /sessions/{id}/messages             {content, client_message_id?, verbose?}
POST /sessions/{id}/messages/stream      {content}  -> Server-Sent Events
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agent.backend.errors import BackendError
from agent.config import Settings
from agent.identity import NewCustomerNeedsName
from agent.observability import (
    bind_request_id,
    configure_logging,
    get_logger,
    new_request_id,
)
from agent.service import AgentService, IdempotencyConflict, SessionNotFound

configure_logging()
_log = get_logger("agent.api")
_WEB_DIR = Path(__file__).parent / "web"

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'"
    ),
}

_service: AgentService | None = None


def get_service() -> AgentService:
    """Lazily built composition root; overridden in tests via dependency_overrides."""
    global _service
    if _service is None:
        _service = AgentService.from_settings(Settings.from_env())
    return _service


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or new_request_id()
        bind_request_id(request_id)
        started = time.perf_counter()
        response = await call_next(request)
        _log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        response.headers["X-Request-ID"] = request_id
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response


app = FastAPI(title="Restaurant Reception Agent", version="1.0.0")
app.add_middleware(RequestContextMiddleware)


# -- schemas -----------------------------------------------------------
class StartSessionIn(BaseModel):
    phone: str | None = None
    email: str | None = None
    name: str | None = None


class MessageIn(BaseModel):
    content: str
    client_message_id: str | None = None
    verbose: bool = False


class StreamMessageIn(BaseModel):
    content: str


# -- routes ------------------------------------------------------
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready(service: AgentService = Depends(get_service)) -> dict[str, Any]:
    checks = service.ready()
    if not all(checks.values()):
        raise HTTPException(503, {"ready": False, "checks": checks})
    return {"ready": True, "checks": checks}


@app.post("/sessions")
def start_session(
    body: StartSessionIn, service: AgentService = Depends(get_service)
) -> dict[str, Any]:
    if not body.phone and not body.email:
        raise HTTPException(400, "phone or email is required")
    try:
        return service.start_session(phone=body.phone, email=body.email, name=body.name)
    except NewCustomerNeedsName as exc:
        raise HTTPException(400, "New customer - 'name' is required") from exc
    except BackendError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@app.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str, body: MessageIn, service: AgentService = Depends(get_service)
) -> dict[str, Any]:
    try:
        result = service.run_message(session_id, body.content, body.client_message_id)
    except SessionNotFound as exc:
        raise HTTPException(404, "unknown session_id") from exc
    except IdempotencyConflict as exc:
        raise HTTPException(409, "client_message_id reused with a different message") from exc
    except BackendError as exc:
        raise HTTPException(502, f"upstream error: {exc.detail}") from exc
    if not body.verbose:
        result.pop("trace", None)
    return result


@app.post("/sessions/{session_id}/messages/stream")
def stream_message(
    session_id: str, body: StreamMessageIn, service: AgentService = Depends(get_service)
) -> StreamingResponse:
    def emit() -> Iterator[str]:
        try:
            for event in service.stream_message(session_id, body.content):
                yield event.to_sse()
        except SessionNotFound:
            yield _sse_error("unknown session_id")
        except BackendError as exc:
            yield _sse_error(f"upstream error: {exc.detail}")

    return StreamingResponse(emit(), media_type="text/event-stream")


def _sse_error(message: str) -> str:
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n"
