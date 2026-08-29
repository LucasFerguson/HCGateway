"""Shared timezone-aware timestamp helpers for analytics preparation."""

import datetime as dt
from zoneinfo import ZoneInfo


def zone_info(time_zone: str | ZoneInfo) -> ZoneInfo:
    return time_zone if isinstance(time_zone, ZoneInfo) else ZoneInfo(time_zone)


def parse_instant(value: str | dt.datetime) -> dt.datetime:
    parsed = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include UTC Z or an explicit offset")
    return parsed.astimezone(dt.timezone.utc)


def date_key(value: str | dt.datetime, time_zone: str | ZoneInfo) -> str:
    return parse_instant(value).astimezone(zone_info(time_zone)).date().isoformat()


def local_minute_of_day(value: str | dt.datetime, time_zone: str | ZoneInfo) -> int:
    local = parse_instant(value).astimezone(zone_info(time_zone))
    return local.hour * 60 + local.minute


def local_iso(value: str | dt.datetime, time_zone: str | ZoneInfo) -> str:
    """Render an instant with the offset applicable in the requested IANA zone."""
    return parse_instant(value).astimezone(zone_info(time_zone)).isoformat(timespec="milliseconds")


def utc_iso(value: str | dt.datetime) -> str:
    return parse_instant(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def local_today(time_zone: str | ZoneInfo, now: dt.datetime | None = None) -> str:
    instant = parse_instant(now or dt.datetime.now(dt.timezone.utc))
    return instant.astimezone(zone_info(time_zone)).date().isoformat()


def split_by_local_day(start: str | dt.datetime, end: str | dt.datetime, time_zone: str | ZoneInfo):
    """Yield absolute interval pieces split at real local midnights, including DST days."""
    zone = zone_info(time_zone)
    cursor = parse_instant(start)
    finish = parse_instant(end)
    while cursor < finish:
        local_date = cursor.astimezone(zone).date()
        next_midnight = dt.datetime.combine(
            local_date + dt.timedelta(days=1), dt.time.min, tzinfo=zone
        ).astimezone(dt.timezone.utc)
        boundary = min(finish, next_midnight)
        if boundary <= cursor:
            boundary = finish
        yield local_date.isoformat(), cursor, boundary
        cursor = boundary
