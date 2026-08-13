from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def next_interval(from_time: datetime, seconds: int) -> datetime:
    return from_time + timedelta(seconds=seconds)


def next_local_time(from_time: datetime, at: str, timezone_name: str, weekdays: list[int] | None = None) -> datetime:
    zone = ZoneInfo(timezone_name)
    local = from_time.astimezone(zone)
    hour, minute = (int(part) for part in at.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    allowed = set(weekdays if weekdays is not None else range(7))
    while candidate.weekday() not in allowed:
        candidate += timedelta(days=1)
    return candidate.astimezone(UTC)
