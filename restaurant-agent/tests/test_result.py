"""ToolResult envelope."""

from __future__ import annotations

import json

from agent.tools.result import ErrorCode, ToolResult


def test_success_wraps_kwargs_as_data():
    r = ToolResult.success("done", reservation={"id": 7}, table=3)
    assert r.ok
    assert r.data == {"reservation": {"id": 7}, "table": 3}
    assert r.error_code is None


def test_failure_carries_code_and_status():
    r = ToolResult.failure(ErrorCode.NO_TABLE_AVAILABLE, "full", http_status=409, slot="8pm")
    assert r.ok is False
    assert r.error_code == "no_table_available"
    assert r.http_status == 409
    assert r.data == {"slot": "8pm"}


def test_to_json_omits_none_fields():
    payload = json.loads(ToolResult.success("hi").to_json())
    assert payload == {"ok": True, "message": "hi"}


def test_enum_values_are_serialised_as_strings():
    payload = json.loads(ToolResult.failure(ErrorCode.BAD_JSON, "x").to_json())
    assert payload["error_code"] == "bad_json"
