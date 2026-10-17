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

    def test_multi_day_sampling_gap_does_not_create_empty_calendar_days(self):
        observations = (
            samples("2026-01-01T08:00:00Z", 60)
            + samples("2026-01-04T08:00:00Z", 60)
        )
        result = calculate_strain(observations, zone_thresholds=THRESHOLDS)
        self.assertEqual([day["date"] for day in result["daily"]], ["2026-01-01", "2026-01-04"])

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

    def test_workout_clipping_matches_a_full_scan(self):
        # The workout loop binary-searches segments; it must clip exactly
        # what scanning every segment would, including workouts that start
        # before, end after, or fall between observations, and day splits.
        import random
        from analytics_engine import strain as strain_module

        rng = random.Random(7)
        base = dt.datetime(2026, 1, 1, 4, tzinfo=dt.timezone.utc)
        observations, offset = [], 0
        for _ in range(3000):
            offset += rng.choice([1, 5, 30, 60, 600, 3600])
            observations.append({
                "observedAt": (base + dt.timedelta(seconds=offset)).isoformat().replace("+00:00", "Z"),
                "bpm": rng.randint(60, 180),
            })
        workouts = []
        for index in range(60):
            start = base + dt.timedelta(seconds=rng.randint(-3600, offset + 3600))
            end = start + dt.timedelta(seconds=rng.randint(60, 4 * 3600))
            workouts.append({"id": str(index), "startAt": start.isoformat().replace("+00:00", "Z"),
                             "endAt": end.isoformat().replace("+00:00", "Z")})

        result = calculate_strain(observations, zone_thresholds=THRESHOLDS,
                                  time_zone="America/Chicago", workouts=workouts)

        normalized, _ = strain_module._normalize_samples(observations)
        _, segments = strain_module._segments(normalized, THRESHOLDS, "America/Chicago")
        for workout, item in zip(workouts, result["workouts"]):
            start, end = strain_module._instant(workout["startAt"]), strain_module._instant(workout["endAt"])
            clipped = []
            for segment in segments:
                clip_start, clip_end = max(start, segment["start"]), min(end, segment["end"])
                if clip_start < clip_end:
                    clipped.append({**segment, "start": clip_start, "end": clip_end,
                                    "seconds": (clip_end - clip_start).total_seconds()})
            expected = strain_module._summarize_segments(
                clipped, strain_module.WORKOUT_MIN_SPAN_SECONDS,
                strain_module.WORKOUT_MIN_COVERAGE_RATIO, bounds=(start, end))
            self.assertEqual({k: v for k, v in item.items() if k not in ("id", "startAt", "endAt")}, expected)

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

    def test_fallback_accepts_a_provisional_low_confidence_empirical_high(self):
        history = []
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        for day in range(14):
            for index in range(150):
                history.append({
                    "observedAt": (start + dt.timedelta(days=day, minutes=index)).isoformat(),
                    "bpm": 55 + (index % 75),
                })
        calibration = calibrate_zone_thresholds(history, 55)
        self.assertTrue(calibration["available"])
        self.assertEqual(calibration["confidence"], "low")
        self.assertTrue(calibration["provisional"])


if __name__ == "__main__":
    unittest.main()
