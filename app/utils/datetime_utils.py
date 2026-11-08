"""Timezone-aware helpers. Instants are stored as UTC 'YYYY-MM-DDTHH:MM:SSZ' strings."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.errors import ValidationError

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).strftime(UTC_FMT)


def from_iso_utc(value: str) -> datetime:
    return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc)


def get_zone(name: str | None) -> ZoneInfo:
    if not name:
        raise ValidationError("No timezone is set for this user.", code="timezone_not_set")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValidationError(f"'{name}' is not a valid IANA timezone (e.g. 'Asia/Karachi').", code="invalid_timezone")


def local_today(tz_name: str | None, now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(get_zone(tz_name)).date()


def parse_date(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an ISO date like 2026-11-01, got {value!r}.")


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def parse_local_datetime(value: str, tz_name: str | None) -> datetime:
    """Parse an ISO datetime to an aware UTC datetime.

    A value without an offset is interpreted in the user's timezone. A value with an
    offset is honoured as given. Wall-clock times that don't exist (DST gap) are rejected.
    """
    zone = get_zone(tz_name)
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ValidationError(f"scheduled_at must be an ISO datetime like 2026-11-02T10:00:00, got {value!r}.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
        # Round-trip check detects times skipped by a DST transition.
        if dt.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt.replace(tzinfo=None):
            raise ValidationError("That local time does not exist because of a daylight-saving change. Please pick another time.")
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def format_local(utc_value: str | datetime, tz_name: str) -> str:
    dt = from_iso_utc(utc_value) if isinstance(utc_value, str) else utc_value
    local = dt.astimezone(get_zone(tz_name))
    return f"{local:%a %Y-%m-%d %H:%M} ({tz_name})"
