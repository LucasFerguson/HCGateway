import datetime as dt
import unittest

from analytics_engine.context import AnalyticsContext
from analytics_engine.pipeline import (
    ALGORITHM_VERSION,
    aggregate_interval_metric,
    aggregate_point_metric,
    calculate_pace,
    calculate_sleep_consistency,
    calculate_sleep_debt,
    categorize_consistency,
    categorize_debt,
    consistency_score,
    process_health_data,
    reconcile_sleep_events,
    score_healthspan_factors,
)
from analytics_engine.repository import empty_raw_health_data


UTC_CONTEXT = AnalyticsContext()


def session(record_id, source, start, end, stages=None):
    return {
        "id": record_id,
        "source": source,
        "startAt": start,
        "endAt": end,
        "title": None,
        "notes": None,
        "stages": stages if stages is not None else [{"startAt": start, "endAt": end, "kind": "asleep"}],
    }


def event(record_id, date, bedtime, wake):
    date_value = dt.date.fromisoformat(date)
    bedtime_date = date_value if bedtime.startswith("23") else date_value + dt.timedelta(days=1)
    wake_date = date_value + dt.timedelta(days=1)
    primary = session(record_id, "watch", f"{bedtime_date.isoformat()}T{bedtime}:00Z", f"{wake_date.isoformat()}T{wake}:00Z", [])
    return {"id": record_id, "date": date, "primary": primary, "recordings": [primary]}


class SleepParityTests(unittest.TestCase):
    def test_reconciles_eighty_percent_overlap_and_preserves_sources(self):
        fitbit = session("fitbit", "Fitbit", "2026-07-27T04:43:00Z", "2026-07-27T09:39:00Z")
        whoop = session("whoop", "WHOOP", "2026-07-27T04:43:20Z", "2026-07-27T09:38:21Z")
        events = reconcile_sleep_events([fitbit, whoop], UTC_CONTEXT)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["primary"]["id"], "fitbit")
        self.assertEqual([item["id"] for item in events[0]["recordings"]], ["fitbit", "whoop"])

    def test_keeps_nap_separate(self):
        overnight = session("overnight", "Fitbit", "2026-07-27T04:43:00Z", "2026-07-27T09:39:00Z")
        nap = session("nap", "Fitbit", "2026-07-27T18:00:00Z", "2026-07-27T18:45:00Z")
        self.assertEqual(len(reconcile_sleep_events([overnight, nap], UTC_CONTEXT)), 2)

    def test_sleep_debt_thresholds_and_missing_days(self):
        summaries = [
            {"date": "2026-01-01", "sleepMinutes": 420, "eventCount": 1, "recordingCount": 1},
            {"date": "2026-01-02", "sleepMinutes": 450, "eventCount": 1, "recordingCount": 1},
            {"date": "2026-01-10", "sleepMinutes": 360, "eventCount": 1, "recordingCount": 1},
        ]
        result = calculate_sleep_debt(summaries, 480)
        self.assertEqual(result["daily"][1]["rolling7DayAverageMinutes"], 45)
        self.assertEqual(result["daily"][2]["rolling7DayAverageMinutes"], 120)
        self.assertEqual([categorize_debt(value) for value in (0, 29, 30, 45, 46)], ["none", "low", "moderate", "moderate", "high"])

    def test_sleep_consistency_circular_midnight_baseline(self):
        result = calculate_sleep_consistency(
            [
                event("one", "2026-01-01", "23:45", "07:00"),
                event("two", "2026-01-02", "00:00", "07:00"),
                event("three", "2026-01-03", "00:15", "07:00"),
                event("four", "2026-01-04", "00:00", "08:00"),
            ],
            UTC_CONTEXT,
        )
        self.assertEqual([item["score"] for item in result["daily"][:3]], [None, None, None])
        scored = result["daily"][3]
        self.assertEqual(scored["baselineBedtimeMinutesLocal"], 0)
        self.assertEqual(scored["bedtimeDeviationMinutes"], 0)
        self.assertEqual(scored["wakeDeviationMinutes"], 60)
        self.assertEqual(scored["score"], 90)
        self.assertEqual([consistency_score(0, 0), consistency_score(60, 60), consistency_score(90, 90)], [100, 80, 70])
        self.assertEqual([categorize_consistency(value) for value in (80, 70, 69)], ["optimal", "sufficient", "poor"])


class MetricParityTests(unittest.TestCase):
    def test_splits_interval_across_local_midnight(self):
        observation = {
            "id": "steps",
            "source": "watch",
            "startAt": "2026-01-02T05:30:00Z",
            "endAt": "2026-01-02T06:30:00Z",
            "count": 600,
        }
        result = aggregate_interval_metric(
            [observation], "steps", "count", AnalyticsContext(homeTimeZone="America/Chicago")
        )
        self.assertEqual([(item["date"], item["value"]) for item in result["daily"]], [("2026-01-01", 300), ("2026-01-02", 300)])

    def test_selects_source_by_coverage(self):
        records = [
            {"id": "phone", "source": "phone", "startAt": "2026-01-02T12:00:00Z", "endAt": "2026-01-02T13:00:00Z", "count": 1000},
            {"id": "watch", "source": "watch", "startAt": "2026-01-02T12:00:00Z", "endAt": "2026-01-02T14:00:00Z", "count": 900},
        ]
        day = aggregate_interval_metric(records, "steps", "count", UTC_CONTEXT)["daily"][0]
        self.assertEqual(day["source"], "watch")
        self.assertEqual(len(day["bySource"]), 2)

    def test_point_policies_match_reference(self):
        heart = [
            {"id": str(index), "source": "watch", "observedAt": f"2026-01-02T1{index}:00:00Z", "bpm": bpm}
            for index, bpm in enumerate((50, 60, 100))
        ]
        weights = [
            {"id": "early", "source": "scale", "observedAt": "2026-01-02T12:00:00Z", "kilograms": 70},
            {"id": "late", "source": "scale", "observedAt": "2026-01-02T14:00:00Z", "kilograms": 69.5},
        ]
        self.assertEqual(aggregate_point_metric(heart, "bpm", "bpm", "median", UTC_CONTEXT)["daily"][0]["value"], 60)
        self.assertEqual(aggregate_point_metric(weights, "kg", "kilograms", "latest", UTC_CONTEXT)["daily"][0]["value"], 69.5)

    def test_calendar_rolling_and_monthly_trends(self):
        values = [("2026-01-01", 100), ("2026-01-02", 300), ("2026-01-10", 900), ("2026-02-01", 500)]
        records = [
            {"id": str(index), "source": "watch", "startAt": f"{date}T12:00:00Z", "endAt": f"{date}T13:00:00Z", "count": value}
            for index, (date, value) in enumerate(values)
        ]
        result = aggregate_interval_metric(records, "steps", "count", UTC_CONTEXT)
        self.assertEqual([(item["date"], item["value"], item["sampleCount"]) for item in result["rolling7Day"]], [
            ("2026-01-01", 100, 1), ("2026-01-02", 200, 2), ("2026-01-10", 900, 1), ("2026-02-01", 500, 1)
        ])
        self.assertAlmostEqual(result["monthly"][0]["value"], 433.3333333333333)


class HealthspanAndPipelineTests(unittest.TestCase):
    def test_factor_impacts(self):
        factors = score_healthspan_factors(
            {
                "sleepMinutes": {"value": 310, "coverageDays": 20},
                "consistencyScore": {"value": 48, "coverageDays": 18},
                "steps": {"value": 6230, "coverageDays": 22},
                "restingHeartRate": {"value": 55, "coverageDays": 12},
            },
            480,
        )
        self.assertEqual([(item["key"], item["ageImpactYears"]) for item in factors], [
            ("sleep_duration", 2.55), ("sleep_consistency", 0.96), ("steps", 0.71), ("resting_heart_rate", -0.5)
        ])

    def test_pace_of_aging(self):
        start = dt.date(2026, 1, 1)
        estimates = []
        for index in range(7):
            days = index * 10
            estimates.append({"date": (start + dt.timedelta(days=days)).isoformat(), "healthAgeYears": 30 + days / 365.2425})
        self.assertEqual(calculate_pace(estimates), 1)
        self.assertIsNone(calculate_pace(estimates[:2]))

    def test_empty_shape_and_stable_fingerprint(self):
        raw = empty_raw_health_data()
        first = process_health_data(raw)
        second = process_health_data({key: list(reversed(value)) for key, value in raw.items()})
        self.assertEqual(first["algorithmVersion"], ALGORITHM_VERSION)
        self.assertEqual(first["sourceFingerprint"], second["sourceFingerprint"])
        self.assertEqual(first["healthspan"]["status"], "calibrating")
        self.assertEqual(set(first), {
            "algorithmVersion", "sourceFingerprint", "configurationFingerprint", "processedAt", "sleepEvents",
            "dailySleep", "sleepDebt", "sleepConsistency", "healthspan", "deviceSleep", "steps",
            "activeCalories", "totalCalories", "restingHeartRate", "weight"
        })


if __name__ == "__main__":
    unittest.main()
