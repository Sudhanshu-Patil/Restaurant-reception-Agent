"""Best-effort PII / secret redaction for log lines.

Not a security boundary - a safety net so an email, phone number or API token
that slips into a log message or an exception string does not land in plaintext.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\-\s]{7,}\d(?!\w)")
_TOKEN = re.compile(r"(?i)\b(?:bearer\s+|gsk_|sk-|gho_)[A-Za-z0-9_\-]{6,}")


def redact(text: str) -> str:
    text = _TOKEN.sub("<token>", text)
    text = _EMAIL.sub("<email>", text)
    text = _PHONE.sub("<phone>", text)
    return text
