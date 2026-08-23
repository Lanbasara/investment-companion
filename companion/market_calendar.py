from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .foundation import CompanionError, canonical
from .timeutil import parse


class MarketCalendar:
    """Single deterministic interpretation of China-market sessions and phases."""

    MARKET = "CN"
    TIMEZONE = "Asia/Shanghai"
    OPEN = time(9, 30)
    MORNING_CLOSE = time(11, 30)
    AFTERNOON_OPEN = time(13, 0)
    CLOSE = time(15, 0)

    def normalize(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sessions: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise CompanionError("market calendar rows must be objects")
            raw_date = row.get("date") or row.get("cal_date")
            if not isinstance(raw_date, str):
                raise CompanionError("market calendar row requires date or cal_date")
            compact = raw_date.replace("-", "")
            if len(compact) != 8 or not compact.isdigit():
                raise CompanionError(f"invalid market calendar date: {raw_date}")
            session_date = date.fromisoformat(
                f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
            ).isoformat()
            is_open_value = row.get("is_open")
            if is_open_value in {1, "1", True}:
                is_open = True
            elif is_open_value in {0, "0", False}:
                is_open = False
            else:
                raise CompanionError("market calendar is_open must be boolean or 0/1")
            normalized = {
                "market": self.MARKET,
                "session_date": session_date,
                "session_id": f"{self.MARKET}:{session_date}",
                "is_open": is_open,
            }
            existing = sessions.get(session_date)
            if existing and canonical(existing) != canonical(normalized):
                raise CompanionError(f"conflicting market calendar session: {session_date}")
            sessions[session_date] = normalized
        return [sessions[key] for key in sorted(sessions)]

    def open_sessions(
        self,
        rows: list[dict[str, Any]],
        *,
        start: str | None = None,
        end: str | None = None,
    ) -> list[str]:
        result = []
        for item in self.normalize(rows):
            day = item["session_date"]
            if item["is_open"] and (start is None or day >= start) and (end is None or day <= end):
                result.append(day)
        return result

    def sessions_through(
        self, rows: list[dict[str, Any]], *, through: str, count: int
    ) -> list[str]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise CompanionError("market session count must be a positive integer")
        sessions = self.open_sessions(rows, end=through)
        selected = sessions[-count:]
        if len(selected) < count:
            raise CompanionError(
                f"calendar has only {len(selected)} open sessions through {through}"
            )
        return selected

    def next_open_session(self, rows: list[dict[str, Any]], *, after: str) -> str | None:
        return next((day for day in self.open_sessions(rows) if day > after), None)

    def shift_open_session(
        self, rows: list[dict[str, Any]], *, session_date: str, offset: int
    ) -> str:
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise CompanionError("market session offset must be an integer")
        sessions = self.open_sessions(rows)
        try:
            index = sessions.index(session_date)
        except ValueError as exc:
            raise CompanionError(f"not an open market session: {session_date}") from exc
        target = index + offset
        if target < 0 or target >= len(sessions):
            raise CompanionError("market calendar does not cover the shifted session")
        return sessions[target]

    def generated_before_open(self, knowledge_cutoff: str, session_date: str) -> bool:
        market_open = datetime.combine(
            date.fromisoformat(session_date), self.OPEN, ZoneInfo(self.TIMEZONE)
        )
        return parse(knowledge_cutoff) < market_open

    def phase(self, at: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        local = parse(at).astimezone(ZoneInfo(self.TIMEZONE))
        session_date = local.date().isoformat()
        session = next(
            (item for item in self.normalize(rows) if item["session_date"] == session_date),
            None,
        )
        if session is None:
            phase = "unknown_calendar"
        elif not session["is_open"]:
            phase = "closed"
        elif local.time() < self.OPEN:
            phase = "pre_open"
        elif local.time() < self.MORNING_CLOSE:
            phase = "morning_session"
        elif local.time() < self.AFTERNOON_OPEN:
            phase = "midday_break"
        elif local.time() < self.CLOSE:
            phase = "afternoon_session"
        else:
            phase = "post_close"
        return {
            "market": self.MARKET,
            "timezone": self.TIMEZONE,
            "session_date": session_date,
            "session_id": f"{self.MARKET}:{session_date}",
            "phase": phase,
            "calendar_covered": session is not None,
            "is_open_session": bool(session and session["is_open"]),
        }
