"""Experimental, non-proprietary cardiovascular strain calculation.

This module does not reproduce WHOOP's proprietary algorithm.  It integrates
time spent at elevated heart rates and maps the resulting load onto a bounded,
logarithmic 0--21 scale.  Scores are withheld when calibration or heart-rate
coverage is insufficient.
"""

import datetime as dt
import math
from collections import defaultdict
from zoneinfo import ZoneInfo


ALGORITHM_VERSION = "experimental-cardio-strain-v1"
METHODOLOGY = (
    "Non-proprietary estimate. Consecutive heart-rate samples are integrated when "
    "they are no more than 5 minutes apart. Effort is linearly interpolated across "
    "personalized heart-rate-zone boundaries, producing 0-1 load-minutes. Total "
    "load is mapped to 0-21 as 21*log(1+load)/log(1+600), capped at 21."
)
MAX_SAMPLE_GAP_SECONDS = 300
DAY_MIN_SPAN_SECONDS = 6 * 60 * 60
DAY_MIN_COVERAGE_RATIO = 0.70
WORKOUT_MIN_SPAN_SECONDS = 10 * 60
WORKOUT_MIN_COVERAGE_RATIO = 0.80
REFERENCE_LOAD_MINUTES = 600.0
TIMELINE_INTERVAL_SECONDS = 15 * 60


def _instant(value):
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _sample_value(sample):
    for key in ("bpm", "beatsPerMinute", "value"):
        if key in sample:
            return float(sample[key])
    raise ValueError("heart-rate sample has no bpm/beatsPerMinute/value")


def _sample_time(sample):
    for key in ("observedAt", "time", "timestamp"):
        if key in sample:
            return _instant(sample[key])
    raise ValueError("heart-rate sample has no observedAt/time/timestamp")


def _normalize_samples(samples):
    """Sort samples and average duplicate timestamps; discard implausible BPM."""
    grouped = defaultdict(list)
    invalid = 0
    for sample in samples:
        try:
            instant, bpm = _sample_time(sample), _sample_value(sample)
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        if not 25 <= bpm <= 240:
            invalid += 1
            continue
        grouped[instant].append(bpm)
    normalized = [(instant, sum(values) / len(values)) for instant, values in grouped.items()]
    return sorted(normalized), invalid


def _percentile(values, percentile):
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calibrate_zone_thresholds(historical_samples, resting_hr):
    """Return empirical Karvonen-style zones only for a substantial history.

    The 99.5th observed percentile is deliberately described as an empirical
    high, not a measured maximum heart rate.  Fourteen dates and 2,000 valid
    samples are required to reduce sensitivity to a short or sedentary window.
    """
    normalized, invalid = _normalize_samples(historical_samples)
    dates = {instant.date() for instant, _ in normalized}
    reasons = []
    if resting_hr is None or not 30 <= float(resting_hr) <= 120:
        reasons.append("valid_resting_hr_required")
    if len(normalized) < 2000:
        reasons.append("at_least_2000_historical_samples_required")
    if len(dates) < 14:
        reasons.append("at_least_14_historical_dates_required")
    empirical_high = _percentile([bpm for _, bpm in normalized], 0.995) if normalized else None
    if empirical_high is not None and resting_hr is not None and empirical_high - float(resting_hr) < 60:
        reasons.append("observed_heart_rate_range_too_narrow")
    if empirical_high is not None and empirical_high < 140:
        reasons.append("empirical_high_too_low_for_reliable_maximum")
    if reasons:
        return {
            "available": False,
            "method": "historical_empirical_high",
            "reasons": reasons,
            "sampleCount": len(normalized),
            "dateCount": len(dates),
            "invalidSampleCount": invalid,
            "empiricalHighBpm": round(empirical_high, 1) if empirical_high is not None else None,
            "thresholds": None,
        }
    resting = float(resting_hr)
    reserve = empirical_high - resting
    thresholds = [resting + reserve * fraction for fraction in (0.50, 0.60, 0.70, 0.80, 0.90)]
    thresholds.append(empirical_high)
    return {
        "available": True,
        "method": "historical_empirical_high",
        "reasons": [],
        "sampleCount": len(normalized),
        "dateCount": len(dates),
        "invalidSampleCount": invalid,
        "empiricalHighBpm": round(empirical_high, 1),
        "thresholds": [round(value, 1) for value in thresholds],
    }


def _validated_thresholds(zone_thresholds):
    if zone_thresholds is None:
        return None
    if isinstance(zone_thresholds, dict):
        values = [zone_thresholds.get(key) for key in ("zone1", "zone2", "zone3", "zone4", "zone5", "max")]
    else:
        values = list(zone_thresholds)
    if len(values) != 6 or any(value is None for value in values):
        raise ValueError("zone_thresholds must contain zone1..zone5 and max (six values)")
    values = [float(value) for value in values]
    if any(right <= left for left, right in zip(values, values[1:])):
        raise ValueError("zone thresholds must be strictly increasing")
    return values


def _zone(bpm, thresholds):
    return sum(bpm >= boundary for boundary in thresholds[:-1])


def _effort(bpm, thresholds):
    # Effort anchors intentionally rise nonlinearly across zones; interpolation
    # avoids discontinuities when a sample lies close to a boundary.
    bpm_anchors = [thresholds[0] - (thresholds[1] - thresholds[0]), *thresholds]
    effort_anchors = [0.0, 0.05, 0.12, 0.25, 0.50, 0.80, 1.0]
    if bpm <= bpm_anchors[0]:
        return 0.0
    if bpm >= bpm_anchors[-1]:
        return 1.0
    for index in range(len(bpm_anchors) - 1):
        if bpm < bpm_anchors[index + 1]:
            ratio = (bpm - bpm_anchors[index]) / (bpm_anchors[index + 1] - bpm_anchors[index])
            return effort_anchors[index] + ratio * (effort_anchors[index + 1] - effort_anchors[index])
    return 1.0


def _score(load_minutes):
    return round(min(21.0, 21.0 * math.log1p(load_minutes) / math.log1p(REFERENCE_LOAD_MINUTES)), 2)


def _split_at_local_midnight(start, end, time_zone):
    zone = ZoneInfo(time_zone)
    cursor = start
    while cursor < end:
        local = cursor.astimezone(zone)
        next_date = local.date() + dt.timedelta(days=1)
        boundary = dt.datetime.combine(next_date, dt.time(), tzinfo=zone).astimezone(dt.timezone.utc)
        piece_end = min(end, boundary)
        yield local.date().isoformat(), cursor, piece_end
        cursor = piece_end


def _segments(samples, thresholds, time_zone):
    by_day = defaultdict(list)
    all_segments = []
    for (start, start_bpm), (end, end_bpm) in zip(samples, samples[1:]):
        elapsed = (end - start).total_seconds()
        if elapsed <= 0:
            continue
        accepted = elapsed <= MAX_SAMPLE_GAP_SECONDS
        average_bpm = (start_bpm + end_bpm) / 2
        for date, piece_start, piece_end in _split_at_local_midnight(start, end, time_zone):
            segment = {
                "date": date,
                "start": piece_start,
                "end": piece_end,
                "seconds": (piece_end - piece_start).total_seconds(),
                "accepted": accepted,
                "bpm": average_bpm,
                "zone": _zone(average_bpm, thresholds),
                "effort": _effort(average_bpm, thresholds),
            }
            by_day[date].append(segment)
            all_segments.append(segment)
    return by_day, all_segments


def _summarize_segments(segments, minimum_span, minimum_coverage, invalid_count=0, bounds=None):
    if not segments:
        return {
            "score": None,
            "loadMinutes": 0.0,
            "zoneMinutes": {"belowZone1": 0.0, **{f"zone{i}": 0.0 for i in range(1, 6)}},
            "timeline": [],
            "quality": {"publishable": False, "coverageRatio": 0.0, "observedMinutes": 0.0,
                        "spanMinutes": 0.0, "maxGapMinutes": None, "invalidSampleCount": invalid_count,
                        "reasons": ["insufficient_samples"]},
        }
    start = bounds[0] if bounds else min(item["start"] for item in segments)
    end = bounds[1] if bounds else max(item["end"] for item in segments)
    span = (end - start).total_seconds()
    observed = sum(item["seconds"] for item in segments if item["accepted"])
    gaps = [item["seconds"] for item in segments if not item["accepted"]]
    coverage = observed / span if span else 0.0
    reasons = []
    if span < minimum_span:
        reasons.append("observation_span_too_short")
    if coverage < minimum_coverage:
        reasons.append("heart_rate_coverage_too_low")
    accepted = [item for item in segments if item["accepted"]]
    load = sum(item["seconds"] / 60 * item["effort"] for item in accepted)
    zones = {"belowZone1": 0.0, **{f"zone{i}": 0.0 for i in range(1, 6)}}
    for item in accepted:
        key = "belowZone1" if item["zone"] == 0 else f"zone{item['zone']}"
        zones[key] += item["seconds"] / 60
    publishable = not reasons
    cumulative = 0.0
    timeline = []
    last_timeline_at = None
    latest_point = None
    for item in accepted:
        cumulative += item["seconds"] / 60 * item["effort"]
        latest_point = {
            "at": item["end"].isoformat().replace("+00:00", "Z"),
            "loadMinutes": round(cumulative, 3),
            "strain": _score(cumulative) if publishable else None,
        }
        if last_timeline_at is None or (item["end"] - last_timeline_at).total_seconds() >= TIMELINE_INTERVAL_SECONDS:
            timeline.append(latest_point)
            last_timeline_at = item["end"]
    if latest_point and (not timeline or timeline[-1]["at"] != latest_point["at"]):
        timeline.append(latest_point)
    return {
        "score": _score(load) if publishable else None,
        "loadMinutes": round(load, 3),
        "zoneMinutes": {key: round(value, 2) for key, value in zones.items()},
        "timeline": timeline,
        "quality": {
            "publishable": publishable,
            "coverageRatio": round(coverage, 3),
            "observedMinutes": round(observed / 60, 2),
            "spanMinutes": round(span / 60, 2),
            "maxGapMinutes": round(max(gaps) / 60, 2) if gaps else 0.0,
            "invalidSampleCount": invalid_count,
            "reasons": reasons,
        },
    }


def calculate_strain(samples, time_zone="UTC", zone_thresholds=None, resting_hr=None,
                     historical_samples=None, workouts=None):
    """Calculate quality-gated daily and workout strain from timestamped HR.

    ``zone_thresholds`` is either ``[zone1, ..., zone5, max]`` or a mapping with
    those six keys.  If absent, a conservative empirical calibration is attempted
    from ``historical_samples`` and ``resting_hr``.
    """
    ZoneInfo(time_zone)  # validate eagerly
    thresholds = _validated_thresholds(zone_thresholds)
    if thresholds is not None:
        calibration = {"available": True, "method": "personalized_thresholds", "reasons": [],
                       "thresholds": thresholds}
    else:
        calibration = calibrate_zone_thresholds(historical_samples or [], resting_hr)
        thresholds = calibration["thresholds"]
    result = {
        "algorithmVersion": ALGORITHM_VERSION,
        "status": "available" if thresholds else "unavailable",
        "methodology": METHODOLOGY,
        "timeZone": time_zone,
        "calibration": calibration,
        "daily": [],
        "workouts": [],
    }
    if not thresholds:
        result["availability"] = {"available": False, "reasons": calibration["reasons"]}
        return result
    normalized, invalid = _normalize_samples(samples)
    by_day, all_segments = _segments(normalized, thresholds, time_zone)
    for date in sorted(by_day):
        summary = _summarize_segments(by_day[date], DAY_MIN_SPAN_SECONDS, DAY_MIN_COVERAGE_RATIO, invalid)
        result["daily"].append({"date": date, **summary})
    for workout in workouts or []:
        start, end = _instant(workout["startAt"]), _instant(workout["endAt"])
        clipped = []
        for segment in all_segments:
            clip_start, clip_end = max(start, segment["start"]), min(end, segment["end"])
            if clip_start < clip_end:
                clipped.append({**segment, "start": clip_start, "end": clip_end,
                                "seconds": (clip_end - clip_start).total_seconds()})
        summary = _summarize_segments(
            clipped, WORKOUT_MIN_SPAN_SECONDS, WORKOUT_MIN_COVERAGE_RATIO, bounds=(start, end)
        )
        result["workouts"].append({
            "id": workout.get("id"), "startAt": workout["startAt"], "endAt": workout["endAt"], **summary
        })
    any_publishable = any(day["quality"]["publishable"] for day in result["daily"])
    result["availability"] = {
        "available": any_publishable,
        "reasons": [] if any_publishable else (["no_heart_rate_samples"] if not normalized else ["no_complete_day"]),
    }
    if not any_publishable:
        result["status"] = "insufficient_data"
    return result
