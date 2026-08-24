import datetime as dt
import unittest

from analytics_engine.strain import ALGORITHM_VERSION, calculate_strain, calibrate_zone_thresholds


THRESHOLDS = [100, 120, 140, 160, 180, 200]


def samples(start, minutes, bpm=150, every_minutes=1):
    start_at = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    return [
        {"time": (start_at + dt.timedelta(minutes=index)).isoformat(), "beatsPerMinute": bpm}
        for index in range(0, minutes + 1, every_minutes)
    ]


class StrainTests(unittest.TestCase):
    def test_complete_day_has_bounded_nonlinear_score_timeline_and_zones(self):
        result = calculate_strain(samples("2026-01-01T08:00:00Z", 8 * 60), zone_thresholds=THRESHOLDS)
        day = result["daily"][0]
        self.assertEqual(result["algorithmVersion"], ALGORITHM_VERSION)
        self.assertTrue(day["quality"]["publishable"])
        self.assertGreater(day["score"], 0)
        self.assertLessEqual(day["score"], 21)
        self.assertEqual(day["zoneMinutes"]["zone3"], 480)
        self.assertLess(day["timeline"][1]["strain"], day["timeline"][-1]["strain"])
        first_gain = day["timeline"][4]["strain"]
        late_gain = day["timeline"][-1]["strain"] - day["timeline"][-5]["strain"]
        self.assertGreater(first_gain, late_gain)

    def test_sparse_day_reports_load_but_withholds_all_scores(self):
        sparse = samples("2026-01-01T08:00:00Z", 8 * 60, every_minutes=10)
        day = calculate_strain(sparse, zone_thresholds=THRESHOLDS)["daily"][0]
        self.assertIsNone(day["score"])
        self.assertIn("heart_rate_coverage_too_low", day["quality"]["reasons"])
        self.assertTrue(all(point["strain"] is None for point in day["timeline"]))

    def test_local_days_split_at_timezone_midnight(self):
        observations = samples("2026-01-02T05:30:00Z", 7 * 60)
        result = calculate_strain(observations, time_zone="America/Chicago", zone_thresholds=THRESHOLDS)
        self.assertEqual([day["date"] for day in result["daily"]], ["2026-01-01", "2026-01-02"])

    def test_workout_score_is_independent_and_quality_gated(self):
        observations = samples("2026-01-01T08:00:00Z", 8 * 60)
        workouts = [
            {"id": "long", "startAt": "2026-01-01T09:00:00Z", "endAt": "2026-01-01T10:00:00Z"},
            {"id": "short", "startAt": "2026-01-01T09:00:00Z", "endAt": "2026-01-01T09:05:00Z"},
        ]
        result = calculate_strain(observations, zone_thresholds=THRESHOLDS, workouts=workouts)
        self.assertIsNotNone(result["workouts"][0]["score"])
        self.assertIsNone(result["workouts"][1]["score"])
        self.assertIn("observation_span_too_short", result["workouts"][1]["quality"]["reasons"])

    def test_workout_coverage_includes_missing_edges(self):
        observations = samples("2026-01-01T09:20:00Z", 20)
        workout = {"id": "partial", "startAt": "2026-01-01T09:00:00Z", "endAt": "2026-01-01T10:00:00Z"}
        item = calculate_strain(observations, zone_thresholds=THRESHOLDS, workouts=[workout])["workouts"][0]
        self.assertIsNone(item["score"])
        self.assertAlmostEqual(item["quality"]["coverageRatio"], 0.333, places=3)
        self.assertIn("heart_rate_coverage_too_low", item["quality"]["reasons"])

    def test_fallback_calibration_requires_substantial_history(self):
        inadequate = calibrate_zone_thresholds(samples("2026-01-01T00:00:00Z", 100), 55)
        self.assertFalse(inadequate["available"])
        history = []
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for day in range(14):
            for index in range(150):
                history.append({
                    "observedAt": (start + dt.timedelta(days=day, minutes=index)).isoformat(),
                    "bpm": 55 + (index % 120),
                })
        calibrated = calibrate_zone_thresholds(history, 55)
        self.assertTrue(calibrated["available"])
        self.assertEqual(len(calibrated["thresholds"]), 6)
        self.assertEqual(calibrated["method"], "historical_empirical_high")

    def test_missing_calibration_is_explicitly_unavailable(self):
        result = calculate_strain([], resting_hr=55, historical_samples=[])
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["availability"]["available"])
        self.assertIn("at_least_2000_historical_samples_required", result["availability"]["reasons"])

    def test_fallback_rejects_a_low_empirical_high_as_a_personal_maximum(self):
        history = []
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for day in range(14):
            for index in range(150):
                history.append({
                    "observedAt": (start + dt.timedelta(days=day, minutes=index)).isoformat(),
                    "bpm": 55 + (index % 75),
                })
        calibration = calibrate_zone_thresholds(history, 55)
        self.assertFalse(calibration["available"])
        self.assertIn("empirical_high_too_low_for_reliable_maximum", calibration["reasons"])


if __name__ == "__main__":
    unittest.main()
