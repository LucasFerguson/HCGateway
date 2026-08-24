import datetime as dt
import unittest

from analytics_engine.context import AnalyticsContext
from analytics_engine.pipeline import process_health_data
from analytics_engine.repository import empty_raw_health_data


class DayDashboardTests(unittest.TestCase):
    def test_shapes_raw_signals_for_the_frontend_and_assigns_sleep_to_wake_date(self):
        raw = empty_raw_health_data()
        raw["sleepSessions"] = [{
            "id": "sleep",
            "source": "watch",
            "startAt": "2026-01-01T23:00:00Z",
            "endAt": "2026-01-02T07:00:00Z",
            "title": None,
            "notes": None,
            "stages": [
                {"startAt": "2026-01-01T23:00:00Z", "endAt": "2026-01-02T06:50:00Z", "kind": "light"},
                {"startAt": "2026-01-02T06:50:00Z", "endAt": "2026-01-02T07:00:00Z", "kind": "awake"},
            ],
        }]
        raw["steps"] = [{
            "id": "steps", "source": "watch", "startAt": "2026-01-02T12:00:00Z",
            "endAt": "2026-01-02T13:00:00Z", "count": 1200,
        }]
        start = dt.datetime(2026, 1, 2, 8, tzinfo=dt.timezone.utc)
        raw["heartRates"] = [{
            "id": "heart", "source": "watch", "startAt": start.isoformat(),
            "endAt": (start + dt.timedelta(hours=8)).isoformat(),
            "samples": [
                {"observedAt": (start + dt.timedelta(minutes=index)).isoformat(), "bpm": 150}
                for index in range(8 * 60 + 1)
            ],
        }]

        analytics = process_health_data(
            raw,
            AnalyticsContext(heartRateZoneThresholds=[100, 120, 140, 160, 180, 200]),
        )
        day = next(item for item in analytics["dayViews"] if item["date"] == "2026-01-02")

        self.assertEqual(analytics["dailySleep"][0]["date"], "2026-01-02")
        self.assertEqual(day["headlineScores"]["sleepDuration"]["value"], 470)
        self.assertEqual(day["headlineScores"]["sleepDuration"]["stageMinutes"]["awake"], 10)
        self.assertEqual(day["timeline"]["heartRate"]["hours"][8]["mean"], 150)
        self.assertEqual(day["timeline"]["steps"][12]["count"], 1200)
        self.assertEqual(day["headlineScores"]["recovery"]["status"], "not_implemented")
        self.assertEqual(day["headlineScores"]["strain"]["status"], "available")
        self.assertLessEqual(len(day["timeline"]["strain"]), 34)


if __name__ == "__main__":
    unittest.main()
