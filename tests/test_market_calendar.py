from __future__ import annotations

import pytest

from companion.foundation import CompanionError
from companion.market_calendar import MarketCalendar


ROWS = [
    {"date": "2026-08-21", "is_open": True},
    {"cal_date": "20260822", "is_open": "0"},
    {"cal_date": "20260823", "is_open": 0},
    {"date": "2026-08-24", "is_open": 1},
    {"date": "2026-08-25", "is_open": True},
]


def test_market_calendar_uses_frozen_sessions_without_guessing_weekends():
    calendar = MarketCalendar()
    assert calendar.open_sessions(ROWS) == ["2026-08-21", "2026-08-24", "2026-08-25"]
    assert calendar.next_open_session(ROWS, after="2026-08-21") == "2026-08-24"
    assert calendar.shift_open_session(ROWS, session_date="2026-08-24", offset=-1) == "2026-08-21"
    assert calendar.sessions_through(ROWS, through="2026-08-25", count=2) == [
        "2026-08-24",
        "2026-08-25",
    ]


def test_market_calendar_exposes_one_session_identity_and_market_phase():
    calendar = MarketCalendar()
    phase = calendar.phase("2026-08-24T10:00:00+08:00", ROWS)
    assert phase == {
        "market": "CN",
        "timezone": "Asia/Shanghai",
        "session_date": "2026-08-24",
        "session_id": "CN:2026-08-24",
        "phase": "morning_session",
        "calendar_covered": True,
        "is_open_session": True,
    }
    assert calendar.generated_before_open(
        "2026-08-24T01:29:59Z", "2026-08-24"
    ) is True
    assert calendar.generated_before_open(
        "2026-08-24T01:30:00Z", "2026-08-24"
    ) is False


def test_market_calendar_rejects_conflicting_or_incomplete_rows():
    calendar = MarketCalendar()
    with pytest.raises(CompanionError, match="conflicting"):
        calendar.normalize(
            [
                {"date": "2026-08-24", "is_open": True},
                {"date": "2026-08-24", "is_open": False},
            ]
        )
    with pytest.raises(CompanionError, match="only 1 open sessions"):
        calendar.sessions_through(ROWS, through="2026-08-21", count=2)
