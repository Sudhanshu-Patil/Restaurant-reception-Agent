"""JSON structured logging with request-id binding and PII redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from agent.observability.context import get_request_id
from agent.observability.redaction import redact

_RESERVED = set(logging.makeLogRecord({}).__dict__)
_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "msg": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def _safe(value: Any) -> Any:
    return redact(value) if isinstance(value, str) else value


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
