"""PII redaction and structured logging."""

from __future__ import annotations

import json
import logging

from agent.observability import get_request_id, new_request_id, redact
from agent.observability.context import bind_request_id
from agent.observability.logging import JsonFormatter


def test_redacts_email_phone_and_token():
    text = "reach priya at priya.sharma@example.com or +91-9876543210 with Bearer gsk_abcdef123456"
    out = redact(text)
    assert "priya.sharma@example.com" not in out
    assert "9876543210" not in out
    assert "gsk_abcdef123456" not in out
    assert "<email>" in out and "<phone>" in out and "<token>" in out


def test_redact_is_a_noop_on_clean_text():
    assert redact("booked table 7 outdoors for 3") == "booked table 7 outdoors for 3"


def test_json_formatter_emits_structured_record_with_request_id():
    bind_request_id("req-123")
    record = logging.LogRecord(
        "agent.test",
        logging.INFO,
        __file__,
        1,
        "customer +91-9876543210 booked",
        None,
        None,
    )
    record.session_id = "s-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-123"
    assert payload["session_id"] == "s-1"
    assert "9876543210" not in payload["msg"]


def test_request_id_defaults_and_binds():
    assert get_request_id()  # never empty
    rid = new_request_id()
    bind_request_id(rid)
    assert get_request_id() == rid
