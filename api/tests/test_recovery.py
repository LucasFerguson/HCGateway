import datetime as dt
import unittest

from analytics_engine.recovery import ALGORITHM_VERSION, calculate_recovery


def days(count=10, include_hrv=False):
    start = dt.date(2026, 1, 1)
    sleep = []
    resting = []
    consistency = []
    hrv = []
    for index in range(count):
        date = (start + dt.timedelta(days=index)).isoformat()
        sleep.append({"date": date, "sleepMinutes": 480})
        resting.append({"date": date, "value": 55, "source": "watch"})
        consistency.append({"date": date, "score": 80})
        if include_hrv:
            hrv.append({"date": date, "value": 50, "source": "watch"})
    return sleep, resting, consistency, hrv


class RecoveryTests(unittest.TestCase):
    def test_without_hrv_publishes_only_partial_score_after_rhr_baseline(self):
        sleep, resting, consistency, _ = days()
        result = calculate_recovery(sleep, resting, consistency, 480)
        self.assertEqual(result["algorithmVersion"], ALGORITHM_VERSION)
        self.assertEqual(result["status"], "partial")
        fallback = result["daily"][6]
        self.assertEqual(fallback["status"], "partial")
        self.assertEqual(fallback["quality"]["estimateBasis"], "sleep_consistency_partial")
        scored = result["daily"][7]
        self.assertEqual(scored["status"], "partial")
        self.assertIsNotNone(scored["score"])
        self.assertEqual(scored["quality"]["estimateBasis"], "physiology_partial")
        self.assertIn("hrv_missing", scored["quality"]["reasons"])
        self.assertTrue(scored["provisional"])

    def test_hrv_and_rhr_create_complete_score_against_prior_only_baselines(self):
        sleep, resting, consistency, hrv = days(include_hrv=True)
        resting[-1]["value"] = 60
        hrv[-1]["value"] = 40
        result = calculate_recovery(sleep, resting, consistency, 480, hrv)
        scored = result["daily"][-1]
        self.assertEqual(scored["status"], "available")
        self.assertTrue(scored["quality"]["complete"])
        self.assertEqual(scored["components"]["restingHeartRate"]["baseline"], 55)
        self.assertEqual(scored["components"]["hrv"]["baseline"], 50)
        self.assertLess(scored["score"], 80)

    def test_sleep_and_consistency_publish_a_clearly_labeled_fallback(self):
        sleep, _, consistency, _ = days()
        result = calculate_recovery(sleep, [], consistency, 480)
        scored = result["daily"][-1]
        self.assertEqual(scored["status"], "partial")
        self.assertEqual(scored["score"], 95)
        self.assertEqual(scored["quality"]["estimateBasis"], "sleep_consistency_partial")
        self.assertEqual(scored["quality"]["availableWeight"], 0.4)
        self.assertIn("physiology_missing_sleep_consistency_estimate", scored["quality"]["reasons"])

    def test_sleep_alone_still_does_not_claim_recovery(self):
        sleep, _, _, _ = days()
        result = calculate_recovery(sleep, [], [], 480)
        self.assertTrue(all(item["score"] is None for item in result["daily"]))
        self.assertTrue(all(item["quality"]["estimateBasis"] == "insufficient_data" for item in result["daily"]))

    def test_sleep_and_physiology_are_both_required(self):
        _, resting, consistency, _ = days()
        result = calculate_recovery([], resting, consistency, 480)
        self.assertFalse(result["availability"]["available"])
        self.assertTrue(all(item["score"] is None for item in result["daily"]))


if __name__ == "__main__":
    unittest.main()
