"""Frontend-oriented health day-view contract.

Every requested field is present even when its source or model is not.  The
frontend should render from ``status`` and ``value`` rather than guessing what
an absent JSON key means.
"""

import datetime as dt
import math
from collections import defaultdict
from statistics import median
from zoneinfo import ZoneInfo


CONTRACT_VERSION = "health-day-v1"


def _instant(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _date(value, zone):
    return _instant(value).astimezone(zone).date().isoformat()


def _iso(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _metric(status, value=None, unit=None, note=None, source=None, quality_flags=None, **details):
    result = {"status": status, "value": value, "unit": unit, "source": source, "qualityFlags": quality_flags or []}
    if note:
        result["note"] = note
    result.update(details)
    return result


def _lookup(values):
    return {item["date"]: item for item in values}


def _stage_minutes(event):
    totals = defaultdict(float)
    for stage in event["primary"].get("stages", []):
        minutes = max(0, (_instant(stage["endAt"]) - _instant(stage["startAt"])).total_seconds() / 60)
        totals[stage["kind"]] += minutes
    return {name: round(totals.get(name, 0), 1) for name in ("deep", "light", "rem", "asleep", "awake", "unknown")}


def _hourly_heart_rate(by_source, zone):
    selected_source = max(by_source, key=lambda source: len(by_source[source]), default=None)
    selected = by_source.get(selected_source, [])
    buckets = defaultdict(list)
    for sample in selected:
        buckets[_instant(sample["observedAt"]).astimezone(zone).hour].append(sample["bpm"])
    hours = []
    for hour in range(24):
        values = buckets.get(hour, [])
        hours.append({
            "hour": hour,
            "status": "available" if values else "missing",
            "sampleCount": len(values),
            "min": min(values) if values else None,
            "p25": round(_percentile(values, 0.25), 1) if values else None,
            "mean": round(sum(values) / len(values), 1) if values else None,
            "p75": round(_percentile(values, 0.75), 1) if values else None,
            "max": max(values) if values else None,
        })
    unique_minutes = {
        _instant(sample["observedAt"]).astimezone(zone).replace(second=0, microsecond=0)
        for sample in selected
    }
    return {
        "status": "available" if selected else "missing",
        "source": selected_source,
        "sampleCount": len(selected),
        "observedMinuteCount": len(unique_minutes),
        "hours": hours,
        "note": None if selected else "No heart-rate samples were recorded for this day.",
    }


def _index_hourly_steps(records, zone):
    indexed = defaultdict(lambda: defaultdict(lambda: [0.0] * 24))
    for record in records:
        start, end = _instant(record["startAt"]), _instant(record["endAt"])
        if end <= start:
            continue
        duration = (end - start).total_seconds()
        cursor = start
        while cursor < end:
            local = cursor.astimezone(zone)
            next_hour_local = local.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            boundary = min(end, next_hour_local.astimezone(dt.timezone.utc))
            if boundary <= cursor:
                break
            indexed[local.date().isoformat()][record["source"]][local.hour] += record["count"] * ((boundary - cursor).total_seconds() / duration)
            cursor = boundary
    return indexed


def _hourly_steps(indexed, date, selected_source):
    totals = indexed.get(date, {}).get(selected_source, [0.0] * 24)
    return [{"hour": hour, "count": round(value), "status": "available" if value else "missing"} for hour, value in enumerate(totals)]


def _daily_point(records, date, zone, value_key, unit, label):
    by_source = defaultdict(list)
    for record in records:
        if _date(record["observedAt"], zone) == date:
            by_source[record["source"]].append(record[value_key])
    source = max(by_source, key=lambda item: len(by_source[item]), default=None)
    values = by_source.get(source, [])
    if not values:
        return _metric("missing", unit=unit, note=f"No {label} measurement was recorded for this day.")
    return _metric("available", round(median(values), 1), unit, source=source, sampleCount=len(values))


def _observed_dates(raw, analytics, zone):
    dates = {item["date"] for item in analytics.get("dailySleep", [])}
    for key in ("steps", "activeCalories", "totalCalories", "restingHeartRate", "heartRateVariability", "weight"):
        dates.update(item["date"] for item in analytics.get(key, {}).get("daily", []))
    for record in raw.get("heartRates", []):
        dates.update(_date(sample["observedAt"], zone) for sample in record.get("samples", []))
    for key in ("respiratoryRates", "oxygenSaturations"):
        dates.update(_date(record["observedAt"], zone) for record in raw.get(key, []))
    for key in ("exerciseSessions", "sleepSessions", "steps"):
        dates.update(_date(record["endAt"], zone) for record in raw.get(key, []))
    return sorted(dates)


def empty_day(date, context, processed_at=None):
    zone = ZoneInfo(context.homeTimeZone)
    today = dt.datetime.now(dt.timezone.utc).astimezone(zone).date().isoformat()
    state = "future" if date > today else "recorded"
    return {
        "contractVersion": CONTRACT_VERSION,
        "date": date,
        "timeZone": context.homeTimeZone,
        "dayState": state,
        "generatedAt": processed_at,
        "headlineScores": {
            "sleepDuration": _metric("missing", unit="minutes", note="No sleep ending on this date was recorded."),
            "sleepNeed": _metric("missing", unit="percent", note="No sleep record is available to compare with the configured target."),
            "recovery": _metric("insufficient_data", unit="score_0_100", note="The provisional Recovery model needs sleep plus a calibrated RHR or HRV baseline."),
            "strain": _metric("missing", unit="score_0_21", note="No usable heart-rate strain result is available for this day."),
            "strainTarget": _metric("blocked", unit="score_0_21", note="A strain target depends on a trustworthy Recovery score."),
        },
        "supportingMetrics": {
            "hrv": _metric("missing", unit="ms", note="No heart-rate-variability RMSSD measurement was recorded for this day."),
            "restingHeartRate": _metric("missing", unit="bpm", note="No resting-heart-rate measurement was recorded for this day."),
            "respiratoryRate": _metric("missing", unit="breaths_per_minute", note="No respiratory-rate measurement was recorded for this day."),
            "skinTemperatureDeviation": _metric("missing", unit="celsius_delta", note="Skin temperature is not currently present in the database."),
            "steps": _metric("missing", unit="steps", note="No steps were recorded for this day."),
            "calories": _metric("missing", unit="kcal", note="No calorie total was recorded for this day."),
            "zone3AndAbove": _metric("missing", unit="minutes", note="Heart-rate zones are not calibrated for this day."),
        },
        "timeline": {
            "heartRate": {"status": "missing", "source": None, "sampleCount": 0, "observedMinuteCount": 0, "hours": [], "note": "No heart-rate samples were recorded for this day."},
            "strain": [],
            "sleepStages": [],
            "steps": [],
            "workouts": [],
            "schedule": _metric("not_implemented", note="No calendar, location inference, or user-declared routine source is connected."),
            "targetWakeTime": _metric("missing", unit="local_time", note="No wake-time preference or phone-alarm integration exists."),
            "targetBedTime": _metric("missing", unit="local_time", note="No bedtime preference exists; sleep target duration is stored separately."),
            "now": {"status": "missing", "latestObservedAt": None, "note": "Raw records do not include a server receipt timestamp, so transport lag cannot be measured."},
        },
        "heartRateZones": _metric("missing", note="No personal zone thresholds or lactate-threshold test date are configured."),
        "notes": [{"code": "no_day_data", "message": "No prepared metrics exist for this date."}] if state == "recorded" else [],
    }


def build_day_views(raw, analytics, context):
    zone = ZoneInfo(context.homeTimeZone)
    heart_by_date = defaultdict(lambda: defaultdict(list))
    latest_observed_by_date = {}
    for record in raw.get("heartRates", []):
        for sample in record.get("samples", []):
            date = _date(sample["observedAt"], zone)
            heart_by_date[date][record["source"]].append(sample)
            if date not in latest_observed_by_date or _instant(sample["observedAt"]) > _instant(latest_observed_by_date[date]):
                latest_observed_by_date[date] = sample["observedAt"]
    hourly_steps = _index_hourly_steps(raw.get("steps", []), zone)
    for key in ("restingHeartRates", "respiratoryRates", "oxygenSaturations"):
        for record in raw.get(key, []):
            date = _date(record["observedAt"], zone)
            if date not in latest_observed_by_date or _instant(record["observedAt"]) > _instant(latest_observed_by_date[date]):
                latest_observed_by_date[date] = record["observedAt"]
    for key in ("sleepSessions", "steps", "exerciseSessions"):
        for record in raw.get(key, []):
            date = _date(record["endAt"], zone)
            if date not in latest_observed_by_date or _instant(record["endAt"]) > _instant(latest_observed_by_date[date]):
                latest_observed_by_date[date] = record["endAt"]
    sleep_by_date = defaultdict(list)
    for event in analytics.get("sleepEvents", []):
        sleep_by_date[event["date"]].append(event)
    daily_sleep = _lookup(analytics.get("dailySleep", []))
    debt = _lookup(analytics.get("sleepDebt", {}).get("daily", []))
    steps = _lookup(analytics.get("steps", {}).get("daily", []))
    calories = _lookup(analytics.get("totalCalories", {}).get("daily", []))
    resting = _lookup(analytics.get("restingHeartRate", {}).get("daily", []))
    hrv = _lookup(analytics.get("heartRateVariability", {}).get("daily", []))
    strain = _lookup(analytics.get("strain", {}).get("daily", []))
    recovery = _lookup(analytics.get("recovery", {}).get("daily", []))
    workout_strain = {item.get("id"): item for item in analytics.get("strain", {}).get("workouts", [])}
    views = []
    for date in _observed_dates(raw, analytics, zone):
        view = empty_day(date, context, analytics.get("processedAt"))
        view["notes"] = []
        events = sorted(sleep_by_date.get(date, []), key=lambda item: _instant(item["primary"]["startAt"]))
        if events:
            main = max(events, key=lambda item: (_instant(item["primary"]["endAt"]) - _instant(item["primary"]["startAt"])).total_seconds())
            sleep = daily_sleep[date]
            stages = _stage_minutes(main)
            view["headlineScores"]["sleepDuration"] = _metric(
                "available", round(sleep["sleepMinutes"], 1), "minutes", source=main["primary"]["source"],
                window={"startAt": main["primary"]["startAt"], "endAt": main["primary"]["endAt"]},
                stageMinutes=stages, eventCount=sleep["eventCount"], recordingCount=sleep["recordingCount"],
            )
            debt_day = debt.get(date)
            if debt_day:
                percent = round(min(100, debt_day["sleepMinutes"] / debt_day["targetMinutes"] * 100), 1)
                view["headlineScores"]["sleepNeed"] = _metric(
                    "partial", percent, "percent", source="configured_fixed_target",
                    note="This compares sleep with a fixed target; recent strain and debt do not yet adjust sleep need.",
                    targetMinutes=debt_day["targetMinutes"], debtMinutes=debt_day["debtMinutes"],
                )
            view["timeline"]["sleepStages"] = [
                {**stage, "sessionId": event["id"], "source": event["primary"]["source"]}
                for event in events for stage in event["primary"].get("stages", [])
            ]
        heart = _hourly_heart_rate(heart_by_date.get(date, {}), zone)
        view["timeline"]["heartRate"] = heart
        step_day = steps.get(date)
        if step_day:
            view["supportingMetrics"]["steps"] = _metric("available", step_day["value"], "steps", source=step_day["source"], quality_flags=step_day.get("qualityFlags"))
            view["timeline"]["steps"] = _hourly_steps(hourly_steps, date, step_day["source"])
        calorie_day = calories.get(date)
        if calorie_day:
            view["supportingMetrics"]["calories"] = _metric("available", round(calorie_day["value"], 1), "kcal", source=calorie_day["source"], quality_flags=calorie_day.get("qualityFlags"))
        resting_day = resting.get(date)
        if resting_day:
            view["supportingMetrics"]["restingHeartRate"] = _metric("available", resting_day["value"], "bpm", source=resting_day["source"], quality_flags=resting_day.get("qualityFlags"))
        hrv_day = hrv.get(date)
        if hrv_day:
            view["supportingMetrics"]["hrv"] = _metric(
                "available", hrv_day["value"], "ms", source=hrv_day["source"],
                quality_flags=hrv_day.get("qualityFlags"), metric="RMSSD",
            )
        view["supportingMetrics"]["respiratoryRate"] = _daily_point(raw.get("respiratoryRates", []), date, zone, "breathsPerMinute", "breaths_per_minute", "respiratory-rate")
        view["supportingMetrics"]["oxygenSaturation"] = _daily_point(raw.get("oxygenSaturations", []), date, zone, "percentage", "percent", "oxygen-saturation")
        workouts = []
        for workout in raw.get("exerciseSessions", []):
            if _date(workout["endAt"], zone) != date and _date(workout["startAt"], zone) != date:
                continue
            workout_load = workout_strain.get(workout["id"], {})
            workouts.append({
                "id": workout["id"], "startAt": workout["startAt"], "endAt": workout["endAt"],
                "type": workout["exerciseType"], "typeLabel": workout.get("title") or f"Health Connect type {workout['exerciseType']}",
                "source": workout["source"], "strainContribution": workout_load.get("score"),
                "strainQuality": workout_load.get("quality"),
            })
        view["timeline"]["workouts"] = sorted(workouts, key=lambda item: item["startAt"])
        strain_day = strain.get(date)
        if strain_day:
            quality = strain_day.get("quality", {})
            score_status = "available" if strain_day.get("score") is not None else "insufficient_data"
            view["headlineScores"]["strain"] = _metric(
                score_status, strain_day.get("score"), "score_0_21",
                note="Experimental cardiovascular-only estimate; this is not WHOOP's proprietary Strain score.",
                source=analytics["strain"].get("source"), quality_flags=quality.get("reasons"),
                modelVersion=analytics["strain"].get("algorithmVersion"), quality=quality,
            )
            view["timeline"]["strain"] = strain_day.get("timeline", [])
            zones = strain_day.get("zoneMinutes")
            if zones:
                zone3_plus = sum(zones.get(f"zone{number}", 0) for number in range(3, 6))
                view["supportingMetrics"]["zone3AndAbove"] = _metric("available", round(zone3_plus, 1), "minutes", source=analytics["strain"].get("source"))
                calibration = analytics["strain"].get("calibration", {})
                zone_note = "Personal thresholds supplied by the user."
                if calibration.get("method") != "personalized_thresholds":
                    zone_note = "Estimated from resting heart rate and the historical 99.5th-percentile high; not a lactate-threshold test."
                view["heartRateZones"] = _metric(
                    "available", calibration.get("thresholds"), "bpm", note=zone_note,
                    source=calibration.get("method"), testDate=calibration.get("testDate"),
                )
        recovery_day = recovery.get(date)
        if recovery_day:
            view["headlineScores"]["recovery"] = _metric(
                recovery_day.get("status", "insufficient_data"), recovery_day.get("score"), "score_0_100",
                note="Provisional, non-clinical readiness estimate; incomplete inputs are reweighted. This is not a proprietary wearable Recovery score.",
                source="health-connect-multi-signal",
                quality_flags=recovery_day.get("quality", {}).get("reasons"),
                modelVersion=analytics.get("recovery", {}).get("algorithmVersion"),
                provisional=True, band=recovery_day.get("band"),
                components=recovery_day.get("components", {}), quality=recovery_day.get("quality", {}),
            )
        latest = latest_observed_by_date.get(date)
        if latest:
            view["timeline"]["now"] = {
                "status": "sample_time_only", "latestObservedAt": latest,
                "note": "This is the latest measurement time, not server receipt time; transport lag cannot be measured.",
            }
        views.append(view)
    return views
