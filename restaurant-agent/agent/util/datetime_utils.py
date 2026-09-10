"""Resolve loose natural-language date/times to concrete restaurant slots.

The backend is strict (naive local time, 30-minute slots 12:00–22:30); the LLM is
encouraged to call ``get_current_datetime`` and pass ISO strings, but customers say
things like "tomorrow around 8", so we defensively parse those too.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from dateutil import parser as _dateparser

SLOT_OPEN = (12, 0)
SLOT_CLOSE = (22, 30)


def parse_when(text: str, now: datetime | None = None) -> datetime:
    """Best-effort parse of a date/time expression into a naive ``datetime``."""
    now = now or datetime.now()
    cleaned = text.strip()

    # Fast path: the model did the right thing and sent ISO.
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        pass

    lowered = cleaned.lower()
    base = now
    if "tomorrow" in lowered:
        base = now + timedelta(days=1)
        lowered = lowered.replace("tomorrow", "")
    elif "tonight" in lowered or "today" in lowered:
        lowered = lowered.replace("tonight", "").replace("today", "")

    default = base.replace(hour=19, minute=0, second=0, microsecond=0)
    try:
        parsed = _dateparser.parse(lowered, default=default, fuzzy=True)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Could not understand the date/time {text!r}") from exc
    if parsed is None:
        raise ValueError(f"Could not understand the date/time {text!r}")
    return parsed.replace(second=0, microsecond=0)


def nearest_slot(dt: datetime) -> datetime:
    """Snap a datetime to the closest valid 30-minute reservation slot."""
    snapped = dt.replace(second=0, microsecond=0)
    if snapped.minute < 15:
        snapped = snapped.replace(minute=0)
    elif snapped.minute < 45:
        snapped = snapped.replace(minute=30)
    else:
        snapped = snapped.replace(minute=0) + timedelta(hours=1)

    if (snapped.hour, snapped.minute) < SLOT_OPEN:
        snapped = snapped.replace(hour=SLOT_OPEN[0], minute=SLOT_OPEN[1])
    elif (snapped.hour, snapped.minute) > SLOT_CLOSE:
        snapped = snapped.replace(hour=SLOT_CLOSE[0], minute=SLOT_CLOSE[1])
    return snapped
