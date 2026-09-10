"""Groq implementation of :class:`~agent.llm.base.LLMClient`.

Groq exposes an OpenAI-compatible ``/chat/completions`` endpoint with native tool
calling, so this is a straight HTTP wrapper.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from agent.llm.base import LLMError, LLMMessage, MalformedToolCall, ToolCall

_RETRY_STATUS = {429, 500, 502, 503, 529}
_MAX_BACKOFF_SECONDS = 20.0


class GroqError(LLMError):
    """Raised when Groq returns an error or cannot be reached."""


class GroqClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise GroqError("GROQ_API_KEY is not set — put it in .env")
        self._model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = client or httpx.Client(timeout=timeout)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMMessage:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        message = self._post(body)["choices"][0]["message"]
        return LLMMessage(
            role=message.get("role", "assistant"),
            content=message.get("content"),
            tool_calls=[
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc["function"]["name"],
                    arguments=tc["function"].get("arguments") or "{}",
                )
                for i, tc in enumerate(message.get("tool_calls") or [])
            ],
        )

    def _post(self, body: dict[str, Any], max_retries: int = 3) -> dict[str, Any]:
        last_error = "unknown error"
        for attempt in range(max_retries + 1):
            try:
                resp = self._client.post(self._url, headers=self._headers, json=body)
            except httpx.HTTPError as exc:  # transient network issue
                last_error = f"unreachable: {exc}"
            else:
                if resp.status_code < 400:
                    return resp.json()
                last_error = f"error {resp.status_code}: {resp.text}"
                if _is_tool_use_failure(resp):
                    raise MalformedToolCall(_tool_failure_detail(resp))
                if resp.status_code not in _RETRY_STATUS:
                    raise GroqError(f"Groq API {last_error}")
                if attempt < max_retries:
                    time.sleep(_retry_after(resp, attempt))
                    continue
            if attempt < max_retries:
                time.sleep(min(2.0 * (attempt + 1), _MAX_BACKOFF_SECONDS))
        raise GroqError(f"Groq API {last_error}")


def _is_tool_use_failure(resp: httpx.Response) -> bool:
    if resp.status_code != 400:
        return False
    try:
        return resp.json().get("error", {}).get("code") == "tool_use_failed"
    except ValueError:
        return False


def _tool_failure_detail(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError):
        return resp.text


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, honouring Groq's hint when it's reasonable."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return min(float(header), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    match = re.search(r"try again in ([\d.]+)s", resp.text)
    if match:
        return min(float(match.group(1)) + 0.5, _MAX_BACKOFF_SECONDS)
    return min(2.0 * (attempt + 1), _MAX_BACKOFF_SECONDS)
