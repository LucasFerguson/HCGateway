"""Health analytics v7: the v6 parity port plus frontend day-view signals."""

import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from statistics import median
from zoneinfo import ZoneInfo

from .context import AnalyticsContext, validate_context
from .day_dashboard import build_day_views
from .strain import calculate_strain


ALGORITHM_VERSION = "health-analytics-v7"
HEALTHSPAN_MODEL_VERSION = "experimental-healthspan-v1"
SAME_SLEEP_EVENT_OVERLAP_RATIO = 0.8
DAY_SECONDS = 86_400
YEAR_DAYS = 365.2425


def parse_instant(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def iso_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def date_key(value: str | dt.datetime, time_zone: str) -> str:
    instant = parse_instant(value) if isinstance(value, str) else value
    return instant.astimezone(ZoneInfo(time_zone)).date().isoformat()


def local_minute_of_day(value: str, time_zone: str) -> int:
    local = parse_instant(value).astimezone(ZoneInfo(time_zone))
    return local.hour * 60 + local.minute


def js_round(value: float, digits: int = 0):
    factor = 10**digits
    result = math.floor(value * factor + 0.5) / factor
    return int(result) if digits == 0 else result


def clamp(value, minimum, maximum):
    return min(maximum, max(minimum, value))


def mean(values):
    return sum(values) / len(values) if values else None


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def calendar_window(values, end_date: str, days: int):
    end = parse_date(end_date)
    earliest = end - dt.timedelta(days=days - 1)
    return [value for value in values if earliest <= parse_date(value["date"]) <= end]


def _json_compatible_with_javascript(value):
    if isinstance(value, dict):
        return {key: _json_compatible_with_javascript(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible_with_javascript(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def fingerprint(value) -> str:
    encoded = json.dumps(
        _json_compatible_with_javascript(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def source_fingerprint(raw):
    ordered = {
        metric: sorted(raw.get(metric, []), key=lambda item: item["id"])
        for metric in (
            "sleepSessions",
            "steps",
            "activeCalories",
            "totalCalories",
            "restingHeartRates",
            "weights",
            "heartRates",
            "respiratoryRates",
            "oxygenSaturations",
            "exerciseSessions",
        )
    }
    return fingerprint(ordered)


def session_minutes(session):
    return max(0.0, (parse_instant(session["endAt"]) - parse_instant(session["startAt"])).total_seconds() / 60)


def sleep_minutes(session):
    stages = session.get("stages", [])
    if not stages:
        return session_minutes(session)
    return sum(
        max(0.0, (parse_instant(stage["endAt"]) - parse_instant(stage["startAt"])).total_seconds() / 60)
        for stage in stages
        if stage["kind"] not in ("awake", "unknown")
    )


def reconcile_sleep_events(sessions, context):
    by_date = defaultdict(list)
    for session in sessions:
        # A dashboard day owns the sleep that ended on that date. This also
        # keeps same-day naps intuitive while assigning overnight sleep to its
        # wake date instead of the previous evening.
        by_date[date_key(session["endAt"], context.homeTimeZone)].append(session)
    events = []
    for date, daily in by_date.items():
        parent = list(range(len(daily)))

        def find(index):
            if parent[index] != index:
                parent[index] = find(parent[index])
            return parent[index]

        def union(left, right):
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for left_index, left in enumerate(daily):
            for right_index in range(left_index + 1, len(daily)):
                right = daily[right_index]
                overlap = max(
                    0.0,
                    (
                        min(parse_instant(left["endAt"]), parse_instant(right["endAt"]))
                        - max(parse_instant(left["startAt"]), parse_instant(right["startAt"]))
                    ).total_seconds(),
                )
                shorter = min(session_minutes(left), session_minutes(right)) * 60
                ratio = 0 if shorter == 0 else overlap / shorter
                if ratio >= SAME_SLEEP_EVENT_OVERLAP_RATIO:
                    union(left_index, right_index)
        groups = defaultdict(list)
        for index, session in enumerate(daily):
            groups[find(index)].append(session)
        for recordings in groups.values():
            ranked = sorted(recordings, key=session_minutes, reverse=True)
            primary = ranked[0]
            events.append({"id": primary["id"], "date": date, "primary": primary, "recordings": ranked})
    return sorted(events, key=lambda event: parse_instant(event["primary"]["startAt"]))


def aggregate_daily_sleep(events):
    groups = defaultdict(list)
    for event in events:
        groups[event["date"]].append(event)
    return [
        {
            "date": date,
            "sleepMinutes": sum(sleep_minutes(event["primary"]) for event in daily),
            "eventCount": len(daily),
            "recordingCount": sum(len(event["recordings"]) for event in daily),
        }
        for date, daily in sorted(groups.items())
    ]


def categorize_debt(minutes):
    if minutes <= 0:
        return "none"
    if minutes < 30:
        return "low"
    if minutes <= 45:
        return "moderate"
    return "high"


def calculate_sleep_debt(summaries, target):
    ordered = sorted(summaries, key=lambda item: item["date"])
    daily = []
    for summary in ordered:
        window7 = calendar_window(ordered, summary["date"], 7)
        window30 = calendar_window(ordered, summary["date"], 30)
        debts7 = [max(target - item["sleepMinutes"], 0) for item in window7]
        debts30 = [max(target - item["sleepMinutes"], 0) for item in window30]
        debt = max(target - summary["sleepMinutes"], 0)
        daily.append({
            "date": summary["date"],
            "sleepMinutes": summary["sleepMinutes"],
            "targetMinutes": target,
            "debtMinutes": debt,
            "surplusMinutes": max(summary["sleepMinutes"] - target, 0),
            "category": categorize_debt(debt),
            "rolling7DayAverageMinutes": mean(debts7) or 0,
            "rolling7DayTotalMinutes": sum(debts7),
            "rolling30DayAverageMinutes": mean(debts30) or 0,
        })
    latest = daily[-1] if daily else None
    current30 = calendar_window(daily, latest["date"], 30) if latest else []
    previous_end = (parse_date(latest["date"]) - dt.timedelta(days=30)).isoformat() if latest else None
    previous30 = calendar_window(daily, previous_end, 30) if previous_end else []
    breakdown = {"recordedDays": 0, "none": 0, "low": 0, "moderate": 0, "high": 0}
    for day in current30:
        breakdown["recordedDays"] += 1
        breakdown[day["category"]] += 1
    return {
        "targetMinutes": target,
        "methodology": "Daily debt is the positive difference between target sleep and recorded sleep. Rolling averages exclude days without a sleep record.",
        "daily": daily,
        "latest": latest,
        "average7DayMinutes": latest["rolling7DayAverageMinutes"] if latest else None,
        "average30DayMinutes": latest["rolling30DayAverageMinutes"] if latest else None,
        "previous30DayAverageMinutes": mean([day["debtMinutes"] for day in previous30]),
        "breakdown30Day": breakdown,
    }


def circular_mean(values):
    if not values:
        return 0
    radians = [(value / 1440) * math.pi * 2 for value in values]
    x = sum(math.cos(value) for value in radians) / len(values)
    y = sum(math.sin(value) for value in radians) / len(values)
    angle = math.atan2(y, x)
    return js_round((((angle + math.pi * 2 if angle < 0 else angle) / (math.pi * 2)) * 1440) % 1440)


def circular_distance(left, right):
    distance = abs(left - right)
    return min(distance, 1440 - distance)


def consistency_score(bedtime_deviation, wake_deviation):
    average_deviation = (bedtime_deviation + wake_deviation) / 2
    return js_round(clamp(100 - average_deviation / 3, 0, 100))


def categorize_consistency(score):
    if score >= 80:
        return "optimal"
    if score >= 70:
        return "sufficient"
    return "poor"


def calculate_sleep_consistency(events, context):
    longest = {}
    for event in events:
        current = longest.get(event["date"])
        if current is None or session_minutes(event["primary"]) > session_minutes(current["primary"]):
            longest[event["date"]] = event
    schedules = sorted(({
        "date": event["date"],
        "source": event["primary"]["source"],
        "bedtimeAt": event["primary"]["startAt"],
        "wakeAt": event["primary"]["endAt"],
        "bedtimeMinutesLocal": local_minute_of_day(event["primary"]["startAt"], context.homeTimeZone),
        "wakeMinutesLocal": local_minute_of_day(event["primary"]["endAt"], context.homeTimeZone),
    } for event in longest.values()), key=lambda item: item["date"])
    evaluated = []
    for index, night in enumerate(schedules):
        earliest = parse_date(night["date"]) - dt.timedelta(days=14)
        baseline_nights = [item for item in schedules[:index] if parse_date(item["date"]) >= earliest]
        baseline_bedtime = circular_mean([item["bedtimeMinutesLocal"] for item in baseline_nights])
        baseline_wake = circular_mean([item["wakeMinutesLocal"] for item in baseline_nights])
        has_baseline = len(baseline_nights) >= 3
        bedtime_deviation = circular_distance(night["bedtimeMinutesLocal"], baseline_bedtime) if has_baseline else None
        wake_deviation = circular_distance(night["wakeMinutesLocal"], baseline_wake) if has_baseline else None
        score = consistency_score(bedtime_deviation, wake_deviation) if has_baseline else None
        evaluated.append({
            **night,
            "baselineBedtimeMinutesLocal": baseline_bedtime if has_baseline else None,
            "baselineWakeMinutesLocal": baseline_wake if has_baseline else None,
            "bedtimeDeviationMinutes": bedtime_deviation,
            "wakeDeviationMinutes": wake_deviation,
            "baselineNightCount": len(baseline_nights),
            "score": score,
            "category": categorize_consistency(score) if score is not None else None,
            "rolling7DayAverageScore": None,
            "rolling30DayAverageScore": None,
            "qualityFlags": [] if has_baseline else ["insufficient_baseline"],
        })
    scored = lambda values: [day for day in values if day["score"] is not None]
    daily = []
    for day in evaluated:
        result = dict(day)
        result["rolling7DayAverageScore"] = mean([item["score"] for item in calendar_window(scored(evaluated), day["date"], 7)])
        result["rolling30DayAverageScore"] = mean([item["score"] for item in calendar_window(scored(evaluated), day["date"], 30)])
        daily.append(result)
    scored_daily = scored(daily)
    latest = scored_daily[-1] if scored_daily else None
    current30 = calendar_window(scored_daily, latest["date"], 30) if latest else []
    previous_end = (parse_date(latest["date"]) - dt.timedelta(days=30)).isoformat() if latest else None
    previous30 = calendar_window(scored_daily, previous_end, 30) if previous_end else []
    breakdown = {"scoredDays": 0, "optimal": 0, "sufficient": 0, "poor": 0}
    for day in current30:
        breakdown["scoredDays"] += 1
        breakdown[day["category"]] += 1
    return {
        "baselineWindowDays": 14,
        "minimumBaselineNights": 3,
        "methodology": "The longest sleep event is treated as the main sleep for each day. Bedtime and wake time are compared with a circular 14-day baseline from prior nights. The score starts at 100 and loses one point per three minutes of average schedule deviation.",
        "daily": daily,
        "latest": latest,
        "average7DayScore": latest["rolling7DayAverageScore"] if latest else None,
        "average30DayScore": latest["rolling30DayAverageScore"] if latest else None,
        "previous30DayAverageScore": mean([day["score"] for day in previous30]),
        "breakdown30Day": breakdown,
    }


def split_by_local_day(start, end, time_zone):
    zone = ZoneInfo(time_zone)
    cursor = start
    while cursor < end:
        local_date = cursor.astimezone(zone).date()
        next_midnight = dt.datetime.combine(local_date + dt.timedelta(days=1), dt.time.min, tzinfo=zone).astimezone(dt.timezone.utc)
        boundary = min(end, next_midnight)
        if boundary <= cursor:
            boundary = end
        yield local_date.isoformat(), cursor, boundary
        cursor = boundary


def union_duration(intervals):
    ordered = sorted(intervals)
    if not ordered:
        return 0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
    return total + (current_end - current_start).total_seconds()


def build_metric_analytics(unit, daily):
    ordered = sorted(daily, key=lambda item: item["date"])
    latest = ordered[-1] if ordered else None
    previous = ordered[-2] if len(ordered) > 1 else None
    overview = {
        "latest": latest,
        "previous": previous,
        "average7Day": mean([item["value"] for item in ordered[-7:]]),
        "average30Day": mean([item["value"] for item in ordered[-30:]]),
        "changeFromPrevious": latest["value"] - previous["value"] if latest and previous else None,
        "sampleCount": len(ordered),
    }
    rolling = []
    for index, day in enumerate(ordered):
        window = calendar_window(ordered[: index + 1], day["date"], 7)
        rolling.append({"date": day["date"], "value": mean([item["value"] for item in window]), "sampleCount": len(window)})
    months = defaultdict(list)
    for day in ordered:
        months[day["date"][:7]].append(day["value"])
    monthly = [{"month": month, "value": mean(values), "sampleCount": len(values)} for month, values in months.items()]
    return {"unit": unit, "daily": ordered, "overview": overview, "rolling7Day": rolling, "monthly": monthly}


def aggregate_interval_metric(observations, unit, value_key, context):
    accumulators = {}
    for observation in observations:
        try:
            start, end = parse_instant(observation["startAt"]), parse_instant(observation["endAt"])
        except (KeyError, ValueError):
            continue
        if end <= start:
            continue
        duration = (end - start).total_seconds()
        for date, segment_start, segment_end in split_by_local_day(start, end, context.homeTimeZone):
            key = (date, observation["source"])
            accumulator = accumulators.setdefault(key, {"value": 0.0, "ids": set(), "intervals": [], "flags": set()})
            accumulator["value"] += observation[value_key] * ((segment_end - segment_start).total_seconds() / duration)
            accumulator["ids"].add(observation["id"])
            accumulator["intervals"].append((segment_start, segment_end))
            if duration > 26 * 3600:
                accumulator["flags"].add("long_interval")
    by_day = defaultdict(list)
    for (date, source), accumulator in accumulators.items():
        by_day[date].append({
            "source": source,
            "value": js_round(accumulator["value"]) if unit == "steps" else accumulator["value"],
            "observationCount": len(accumulator["ids"]),
            "coverageMinutes": union_duration(accumulator["intervals"]) / 60,
            "qualityFlags": sorted(accumulator["flags"]),
        })
    daily = []
    for date, sources in by_day.items():
        ranked = sorted(sources, key=lambda source: (-source["coverageMinutes"], -source["observationCount"]))
        selected = ranked[0]
        daily.append({
            "date": date,
            "value": selected["value"],
            "source": selected["source"],
            "bySource": [{key: value for key, value in source.items() if key != "qualityFlags"} for source in ranked],
            "qualityFlags": selected["qualityFlags"],
        })
    return build_metric_analytics(unit, daily)


def aggregate_point_metric(observations, unit, value_key, policy, context):
    grouped = defaultdict(list)
    for observation in observations:
        grouped[(date_key(observation["observedAt"], context.homeTimeZone), observation["source"])].append(observation)
    by_day = defaultdict(list)
    for (date, source), records in grouped.items():
        ordered = sorted(records, key=lambda record: parse_instant(record["observedAt"]))
        values = sorted(record[value_key] for record in ordered)
        value = ordered[-1][value_key] if policy == "latest" else median(values)
        by_day[date].append({
            "source": source,
            "value": value,
            "observationCount": len(records),
            "coverageMinutes": None,
            "latestAt": parse_instant(ordered[-1]["observedAt"]).timestamp(),
        })
    daily = []
    for date, sources in by_day.items():
        ranked = sorted(sources, key=lambda source: (-source["observationCount"], -source["latestAt"]))
        selected = ranked[0]
        daily.append({
            "date": date,
            "value": selected["value"],
            "source": selected["source"],
            "bySource": [{key: value for key, value in source.items() if key != "latestAt"} for source in ranked],
            "qualityFlags": [],
        })
    return build_metric_analytics(unit, daily)


def compare_devices(events):
    observations = {}
    for event in events:
        representatives = {}
        for recording in event["recordings"]:
            existing = representatives.get(recording["source"])
            if existing is None or sleep_minutes(recording) > sleep_minutes(existing):
                representatives[recording["source"]] = recording
        durations = [(source, sleep_minutes(recording)) for source, recording in representatives.items()]
        for source, minutes in durations:
            summary = observations.setdefault(source, {"durations": [], "differences": []})
            summary["durations"].append(minutes)
            peers = [value for peer_source, value in durations if peer_source != source]
            if peers:
                summary["differences"].append(minutes - mean(peers))
    return sorted(({
        "source": source,
        "recordingCount": len(values["durations"]),
        "averageSleepMinutes": mean(values["durations"]),
        "comparisonCount": len(values["differences"]),
        "averageDifferenceMinutes": mean(values["differences"]),
    } for source, values in observations.items()), key=lambda item: item["averageSleepMinutes"], reverse=True)


def healthspan_factor_inputs(values, end_date, minimum_days):
    window = calendar_window(values, end_date, 30)
    return {"value": mean([item["value"] for item in window]), "coverageDays": len(window)} if len(window) >= minimum_days else None


def score_healthspan_factors(inputs, target):
    factors = []
    definitions = [
        ("sleepMinutes", "sleep_duration", "Hours of sleep", "minutes", target, lambda value: clamp(((target - value) / 60) * 0.9, -0.75, 4), 1),
        ("consistencyScore", "sleep_consistency", "Sleep consistency", "percent", 80, lambda value: clamp((80 - value) * 0.03, -0.6, 3), 1),
        ("steps", "steps", "Steps", "steps", 8000, lambda value: clamp((8000 - value) * 0.0004, -1.2, 2.8), 0),
        ("restingHeartRate", "resting_heart_rate", "Resting heart rate", "bpm", 60, lambda value: clamp((value - 60) * 0.1, -2, 3), 1),
    ]
    for input_key, key, label, unit, reference, impact, digits in definitions:
        item = inputs.get(input_key)
        if item:
            factors.append({
                "key": key,
                "label": label,
                "value": js_round(item["value"], digits),
                "unit": unit,
                "referenceValue": reference,
                "ageImpactYears": js_round(impact(item["value"]), 2),
                "coverageDays": item["coverageDays"],
            })
    return factors


def calculate_pace(estimates):
    available = [item for item in estimates if item["healthAgeYears"] is not None]
    if not available:
        return None
    window = calendar_window(available, available[-1]["date"], 180)
    if len(window) < 6 or (parse_date(window[-1]["date"]) - parse_date(window[0]["date"])).days < 30:
        return None
    origin = parse_date(window[0]["date"])
    points = [((parse_date(item["date"]) - origin).days, item["healthAgeYears"]) for item in window]
    mean_x, mean_y = mean([point[0] for point in points]), mean([point[1] for point in points])
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    return None if not denominator else js_round(clamp((numerator / denominator) * YEAR_DAYS, -1, 3), 2)


def calculate_healthspan(daily_sleep, consistency, steps, resting_heart_rate, context):
    dates = sorted({item["date"] for values in (daily_sleep, consistency["daily"], steps["daily"], resting_heart_rate["daily"]) for item in values})
    estimates = []
    scored_consistency = [item for item in consistency["daily"] if item["score"] is not None]
    for date in dates:
        sleep_window = calendar_window(daily_sleep, date, 30)
        sleep_input = {"value": mean([item["sleepMinutes"] for item in sleep_window]), "coverageDays": len(sleep_window)} if len(sleep_window) >= 7 else None
        consistency_window = calendar_window(scored_consistency, date, 30)
        consistency_input = {"value": mean([item["score"] for item in consistency_window]), "coverageDays": len(consistency_window)} if len(consistency_window) >= 5 else None
        factors = score_healthspan_factors({
            "sleepMinutes": sleep_input,
            "consistencyScore": consistency_input,
            "steps": healthspan_factor_inputs(steps["daily"], date, 5),
            "restingHeartRate": healthspan_factor_inputs(resting_heart_rate["daily"], date, 3),
        }, context.sleepTargetMinutes)
        age_delta = js_round(sum(item["ageImpactYears"] for item in factors), 2) if factors else None
        chronological = ((parse_date(date) - parse_date(context.birthDate)).days / YEAR_DAYS) if context.birthDate else None
        health_age = js_round(clamp(chronological + age_delta, chronological - 15, chronological + 15), 2) if chronological is not None and age_delta is not None and len(factors) >= 2 else None
        estimates.append({
            "date": date,
            "chronologicalAgeYears": chronological,
            "healthAgeYears": health_age,
            "ageDeltaYears": age_delta,
            "paceOfAging": None,
            "factors": factors,
            "qualityFlags": ([] if context.birthDate else ["birth_date_required"]) + ([] if len(factors) >= 2 else ["insufficient_factors"]),
        })
    trend = []
    for index, estimate in enumerate(estimates):
        result = dict(estimate)
        result["paceOfAging"] = calculate_pace(estimates[: index + 1])
        trend.append(result)
    latest = trend[-1] if trend else None
    reasons = []
    if not context.birthDate:
        reasons.append("Set HEALTH_BIRTH_DATE to calculate chronological and health age.")
    if not latest or len(latest["factors"]) < 2:
        reasons.append("At least two sufficiently covered health factors are required.")
    if context.birthDate and latest and latest["healthAgeYears"] is not None and not latest["paceOfAging"]:
        reasons.append("Pace of aging needs at least 30 days of health-age estimates.")
    status = "calibrating" if not latest or latest["healthAgeYears"] is None or not context.birthDate else ("partial" if latest["paceOfAging"] is None else "ready")
    return {
        "modelVersion": HEALTHSPAN_MODEL_VERSION,
        "status": status,
        "birthDateConfigured": context.birthDate is not None,
        "methodology": "Experimental estimate, not a medical measurement. Thirty-day sleep duration, sleep consistency, steps, and resting heart rate each contribute an auditable age adjustment. Pace of aging is the annualized regression slope of health age over the latest 180 days.",
        "calibrationReasons": reasons,
        "trend": trend,
        "latest": latest,
        "paceOfAging": latest["paceOfAging"] if latest else None,
        "paceWindowDays": 180,
    }


def process_health_data(raw, context=AnalyticsContext()):
    context = validate_context(context)
    sleep_events = reconcile_sleep_events(raw.get("sleepSessions", []), context)
    daily_sleep = aggregate_daily_sleep(sleep_events)
    consistency = calculate_sleep_consistency(sleep_events, context)
    steps = aggregate_interval_metric(raw.get("steps", []), "steps", "count", context)
    active_calories = aggregate_interval_metric(raw.get("activeCalories", []), "kcal", "energyKcal", context)
    total_calories = aggregate_interval_metric(raw.get("totalCalories", []), "kcal", "energyKcal", context)
    resting_heart_rate = aggregate_point_metric(raw.get("restingHeartRates", []), "bpm", "bpm", "median", context)
    weight = aggregate_point_metric(raw.get("weights", []), "kg", "kilograms", "latest", context)
    heart_rate_records = raw.get("heartRates", [])
    samples_by_source = defaultdict(list)
    for record in heart_rate_records:
        samples_by_source[record["source"]].extend(record.get("samples", []))
    heart_rate_source = max(samples_by_source, key=lambda source: len(samples_by_source[source]), default=None)
    heart_rate_samples = samples_by_source.get(heart_rate_source, [])
    resting_values = sorted(raw.get("restingHeartRates", []), key=lambda item: parse_instant(item["observedAt"]))
    resting_baseline = median([item["bpm"] for item in resting_values[-90:]]) if resting_values else None
    strain = calculate_strain(
        heart_rate_samples,
        time_zone=context.homeTimeZone,
        zone_thresholds=context.heartRateZoneThresholds,
        resting_hr=resting_baseline,
        historical_samples=heart_rate_samples,
        workouts=raw.get("exerciseSessions", []),
    )
    strain["source"] = heart_rate_source
    if context.heartRateZoneTestDate and strain.get("calibration"):
        strain["calibration"]["testDate"] = context.heartRateZoneTestDate
    analytics = {
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceFingerprint": source_fingerprint(raw),
        "configurationFingerprint": fingerprint(context.as_dict()),
        "processedAt": iso_now(),
        "sleepEvents": sleep_events,
        "dailySleep": daily_sleep,
        "sleepDebt": calculate_sleep_debt(daily_sleep, context.sleepTargetMinutes),
        "sleepConsistency": consistency,
        "healthspan": calculate_healthspan(daily_sleep, consistency, steps, resting_heart_rate, context),
        "deviceSleep": compare_devices(sleep_events),
        "steps": steps,
        "activeCalories": active_calories,
        "totalCalories": total_calories,
        "restingHeartRate": resting_heart_rate,
        "weight": weight,
        "strain": strain,
    }
    analytics["dayViews"] = build_day_views(raw, analytics, context)
    return analytics
