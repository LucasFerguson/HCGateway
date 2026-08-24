import datetime as dt
import os
from dataclasses import asdict, dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class AnalyticsContext:
    homeTimeZone: str = "UTC"
    sleepTargetMinutes: int = 480
    birthDate: str | None = None
    heartRateZoneThresholds: list[float] | None = None
    heartRateZoneTestDate: str | None = None

    def as_dict(self):
        return asdict(self)


def validate_context(context: AnalyticsContext) -> AnalyticsContext:
    try:
        ZoneInfo(context.homeTimeZone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError("homeTimeZone must be a valid IANA timezone") from error
    if not isinstance(context.sleepTargetMinutes, int) or not 240 <= context.sleepTargetMinutes <= 720:
        raise ValueError("sleepTargetMinutes must be an integer from 240 to 720")
    if context.birthDate is not None:
        try:
            birth_date = dt.date.fromisoformat(context.birthDate)
        except (TypeError, ValueError) as error:
            raise ValueError("birthDate must be a past date in YYYY-MM-DD format") from error
        if birth_date >= dt.datetime.now(dt.timezone.utc).date():
            raise ValueError("birthDate must be a past date in YYYY-MM-DD format")
    if context.heartRateZoneThresholds is not None:
        thresholds = context.heartRateZoneThresholds
        if not isinstance(thresholds, list) or len(thresholds) != 6:
            raise ValueError("heartRateZoneThresholds must contain six increasing BPM values")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in thresholds):
            raise ValueError("heartRateZoneThresholds must contain six increasing BPM values")
        if any(value < 30 or value > 240 for value in thresholds) or any(
            right <= left for left, right in zip(thresholds, thresholds[1:])
        ):
            raise ValueError("heartRateZoneThresholds must contain six increasing BPM values from 30 to 240")
    if context.heartRateZoneTestDate is not None:
        try:
            test_date = dt.date.fromisoformat(context.heartRateZoneTestDate)
        except (TypeError, ValueError) as error:
            raise ValueError("heartRateZoneTestDate must be YYYY-MM-DD and not in the future") from error
        if test_date > dt.datetime.now(dt.timezone.utc).date():
            raise ValueError("heartRateZoneTestDate must be YYYY-MM-DD and not in the future")
        if context.heartRateZoneThresholds is None:
            raise ValueError("heartRateZoneTestDate requires heartRateZoneThresholds")
    return context


def context_for_user(user) -> AnalyticsContext:
    configured = user.get("analyticsConfig") or {}
    target = configured.get("sleepTargetMinutes", os.environ.get("SLEEP_TARGET_MINUTES", "480"))
    try:
        target = int(target)
    except (TypeError, ValueError) as error:
        raise ValueError("sleepTargetMinutes must be an integer from 240 to 720") from error
    context = AnalyticsContext(
        homeTimeZone=configured.get("homeTimeZone", os.environ.get("HEALTH_HOME_TIME_ZONE", "UTC")),
        sleepTargetMinutes=target,
        birthDate=configured.get("birthDate", os.environ.get("HEALTH_BIRTH_DATE") or None),
        heartRateZoneThresholds=configured.get("heartRateZoneThresholds"),
        heartRateZoneTestDate=configured.get("heartRateZoneTestDate"),
    )
    return validate_context(context)
