"""Experimental, non-clinical daily recovery readiness estimate.

This deliberately transparent first version is not a reproduction of WHOOP or
any other proprietary score.  It is marked provisional because the available
Health Connect history may not include HRV and because its weights and response
curves have not been validated against outcomes.
"""

import datetime as dt
from statistics import median


ALGORITHM_VERSION = "experimental-recovery-v1"
BASELINE_WINDOW_DAYS = 28
MINIMUM_BASELINE_DAYS = 7
METHODOLOGY = (
    "Provisional, non-clinical readiness estimate. Sleep duration contributes 30%, "
    "HRV deviation from a trailing personal median contributes 35%, resting-heart-rate "
    "deviation contributes 25%, and sleep consistency contributes 10%. Missing optional "
    "components are reweighted, and scores without HRV remain partial."
)
LIMITATIONS = [
    "The model weights and response curves are heuristic and have not been clinically validated.",
    "A partial score without HRV is less sensitive to autonomic recovery and illness.",
    "Device changes, alcohol, medication, travel, and recording conditions can shift personal baselines.",
    "TODO: validate weights, minimum baselines, and outcome calibration on longitudinal personal data.",
]
WEIGHTS = {"sleep": 0.30, "hrv": 0.35, "restingHeartRate": 0.25, "sleepConsistency": 0.10}


def _clamp(value, minimum=0.0, maximum=100.0):
    return min(maximum, max(minimum, value))


def _date(value):
    return dt.date.fromisoformat(value)


def _prior_baseline(values, date, window_days=BASELINE_WINDOW_DAYS):
    current = _date(date)
    earliest = current - dt.timedelta(days=window_days)
    eligible = [
        item["value"] for item in values
        if earliest <= _date(item["date"]) < current
    ]
    return median(eligible) if len(eligible) >= MINIMUM_BASELINE_DAYS else None, len(eligible)


def _sleep_score(minutes, target):
    # Extra sleep is not rewarded beyond the configured target in this first model.
    return _clamp(minutes / target * 100) if target else None


def _rhr_score(value, baseline):
    # Five bpm above/below baseline moves the component by 25 points.
    return _clamp(75 - (value - baseline) * 5)


def _hrv_score(value, baseline):
    # A 25% change from baseline moves the component by 50 points.
    return _clamp(50 + ((value / baseline) - 1) * 200) if baseline > 0 else None


def _band(score):
    if score < 34:
        return "low"
    if score < 67:
        return "moderate"
    return "high"


def calculate_recovery(daily_sleep, resting_heart_rate, sleep_consistency,
                       sleep_target_minutes, hrv=None):
    """Calculate quality-explicit recovery scores assigned to the sleep end date."""
    resting = sorted(resting_heart_rate or [], key=lambda item: item["date"])
    hrv_values = sorted(hrv or [], key=lambda item: item["date"])
    sleep_by_date = {item["date"]: item for item in daily_sleep or []}
    resting_by_date = {item["date"]: item for item in resting}
    hrv_by_date = {item["date"]: item for item in hrv_values}
    consistency_by_date = {item["date"]: item for item in sleep_consistency or []}
    dates = sorted(set(sleep_by_date) | set(resting_by_date) | set(hrv_by_date))
    daily = []

    for date in dates:
        components = {}
        reasons = []
        sleep = sleep_by_date.get(date)
        if sleep:
            components["sleep"] = {
                "score": round(_sleep_score(sleep["sleepMinutes"], sleep_target_minutes), 1),
                "value": sleep["sleepMinutes"], "baseline": sleep_target_minutes, "unit": "minutes",
            }
        else:
            reasons.append("sleep_required")

        rhr = resting_by_date.get(date)
        rhr_baseline, rhr_days = _prior_baseline(resting, date)
        if rhr and rhr_baseline is not None:
            components["restingHeartRate"] = {
                "score": round(_rhr_score(rhr["value"], rhr_baseline), 1),
                "value": rhr["value"], "baseline": round(rhr_baseline, 1), "unit": "bpm",
                "baselineDays": rhr_days,
            }
        elif rhr:
            reasons.append("resting_heart_rate_baseline_calibrating")
        else:
            reasons.append("resting_heart_rate_missing")

        hrv_day = hrv_by_date.get(date)
        hrv_baseline, hrv_days = _prior_baseline(hrv_values, date)
        if hrv_day and hrv_baseline is not None:
            components["hrv"] = {
                "score": round(_hrv_score(hrv_day["value"], hrv_baseline), 1),
                "value": hrv_day["value"], "baseline": round(hrv_baseline, 1), "unit": "ms",
                "baselineDays": hrv_days,
            }
        elif hrv_day:
            reasons.append("hrv_baseline_calibrating")
        else:
            reasons.append("hrv_missing")

        consistency = consistency_by_date.get(date)
        if consistency and consistency.get("score") is not None:
            components["sleepConsistency"] = {
                "score": consistency["score"], "value": consistency["score"],
                "baseline": None, "unit": "score_0_100",
            }
        else:
            reasons.append("sleep_consistency_baseline_calibrating")

        available_weight = sum(WEIGHTS[name] for name in components)
        has_sleep = "sleep" in components
        has_physiology = "hrv" in components or "restingHeartRate" in components
        publishable = has_sleep and has_physiology and available_weight >= 0.50
        score = None
        if publishable:
            score = round(sum(WEIGHTS[name] * item["score"] for name, item in components.items()) / available_weight)
        complete = publishable and set(components) == set(WEIGHTS)
        daily.append({
            "date": date,
            "score": score,
            "band": _band(score) if score is not None else None,
            "status": "available" if complete else ("partial" if publishable else "insufficient_data"),
            "provisional": True,
            "components": components,
            "quality": {
                "publishable": publishable,
                "complete": complete,
                "availableWeight": round(available_weight, 2),
                "baselineWindowDays": BASELINE_WINDOW_DAYS,
                "minimumBaselineDays": MINIMUM_BASELINE_DAYS,
                "reasons": reasons,
            },
        })

    publishable_days = [item for item in daily if item["score"] is not None]
    return {
        "algorithmVersion": ALGORITHM_VERSION,
        "status": "available" if any(item["status"] == "available" for item in daily) else (
            "partial" if publishable_days else "insufficient_data"
        ),
        "provisional": True,
        "methodology": METHODOLOGY,
        "limitations": LIMITATIONS,
        "weights": WEIGHTS,
        "daily": daily,
        "availability": {
            "available": bool(publishable_days),
            "publishableDayCount": len(publishable_days),
            "completeDayCount": sum(item["status"] == "available" for item in daily),
            "reasons": [] if publishable_days else ["no_day_has_sleep_and_a_calibrated_physiological_baseline"],
        },
    }
