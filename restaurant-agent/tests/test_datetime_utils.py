"""Loose date/time parsing and slot snapping."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from agent.util.datetime_utils import nearest_slot, parse_when

_NOW = datetime(2026, 9, 7, 15, 0)  # a Monday afternoon


def test_iso_is_passed_through():
    assert parse_when("2026-09-08T19:30:00") == datetime(2026, 9, 8, 19, 30)


def test_tonight_resolves_to_today():
    got = parse_when("tonight at 8pm", now=_NOW)
    assert got.date() == _NOW.date()
    assert got.hour == 20


def test_tomorrow_advances_one_day():
    got = parse_when("tomorrow at 7:30pm", now=_NOW)
    assert got.date() == (_NOW + timedelta(days=1)).date()
    assert (got.hour, got.minute) == (19, 30)


def test_unparseable_raises_valueerror():
    with pytest.raises(ValueError):
        parse_when("whenever the vibes are right")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (datetime(2026, 9, 8, 19, 8), datetime(2026, 9, 8, 19, 0)),
        (datetime(2026, 9, 8, 19, 20), datetime(2026, 9, 8, 19, 30)),
        (datetime(2026, 9, 8, 19, 50), datetime(2026, 9, 8, 20, 0)),
    ],
)
def test_nearest_slot_snaps_to_30_minutes(raw, expected):
    assert nearest_slot(raw) == expected


def test_nearest_slot_clamps_to_service_window():
    assert nearest_slot(datetime(2026, 9, 8, 9, 0)).hour == 12
    late = nearest_slot(datetime(2026, 9, 8, 23, 45))
    assert (late.hour, late.minute) == (22, 30)
