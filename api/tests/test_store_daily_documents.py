import unittest

from analytics_engine.store import _daily_documents, _strain_workouts_by_date


def _empty_analytics(time_zone="America/Chicago", workouts=None):
    return {
        "timeZone": time_zone,
        "dailySleep": [],
        "sleepDebt": {"daily": []},
        "sleepConsistency": {"daily": []},
        "healthspan": {"trend": []},
        "steps": {"daily": []},
        "activeCalories": {"daily": []},
        "totalCalories": {"daily": []},
        "restingHeartRate": {"daily": []},
        "heartRateVariability": {"daily": []},
        "weight": {"daily": []},
        "strain": {"daily": [], "workouts": workouts or []},
        "recovery": {"daily": []},
        "dayViews": [],
    }


class StrainWorkoutsByDateTests(unittest.TestCase):
    def test_workout_within_one_day_is_grouped_once(self):
        workout = {"id": "run-1", "startAt": "2026-01-01T15:00:00Z", "endAt": "2026-01-01T16:00:00Z", "score": 4.2}
        by_date = _strain_workouts_by_date(_empty_analytics(workouts=[workout]))
        self.assertEqual(set(by_date), {"2026-01-01"})
        self.assertEqual(by_date["2026-01-01"], [workout])

    def test_workout_spanning_local_midnight_is_grouped_on_both_dates(self):
        # America/Chicago is UTC-6 in January (no DST): 2026-01-01T23:30 local
        # is 2026-01-02T05:30Z, and the workout ends 2026-01-02T00:30 local
        # (2026-01-02T06:30Z) - it should appear on both local dates.
        workout = {
            "id": "midnight-run",
            "startAt": "2026-01-02T05:30:00Z",
            "endAt": "2026-01-02T06:30:00Z",
            "score": 3.1,
        }
        by_date = _strain_workouts_by_date(_empty_analytics(workouts=[workout]))
        self.assertEqual(set(by_date), {"2026-01-01", "2026-01-02"})
        self.assertEqual(by_date["2026-01-01"], [workout])
        self.assertEqual(by_date["2026-01-02"], [workout])

    def test_no_workouts_yields_empty_grouping(self):
        self.assertEqual(_strain_workouts_by_date(_empty_analytics()), {})


class DailyDocumentsStrainWorkoutsTests(unittest.TestCase):
    def test_daily_document_carries_full_strain_workout_detail(self):
        workout = {
            "id": "run-1",
            "startAt": "2026-01-01T15:00:00Z",
            "endAt": "2026-01-01T16:00:00Z",
            "score": 4.2,
            "loadMinutes": 12.5,
            "zoneMinutes": {"belowZone1": 0, "zone1": 5, "zone2": 5, "zone3": 2.5, "zone4": 0, "zone5": 0},
            "timeline": [{"at": "2026-01-01T15:15:00Z", "loadMinutes": 3.75, "strain": 1.1}],
            "quality": {"publishable": True, "coverageRatio": 1.0, "observedMinutes": 60,
                        "spanMinutes": 60, "maxGapMinutes": None, "invalidSampleCount": 0, "reasons": []},
        }
        documents = _daily_documents(_empty_analytics(workouts=[workout]))
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["date"], "2026-01-01")
        self.assertEqual(documents[0]["strainWorkouts"], [workout])
        # Full detail survives - not just the score/quality subset day_dashboard.py projects.
        self.assertEqual(documents[0]["strainWorkouts"][0]["loadMinutes"], 12.5)
        self.assertEqual(documents[0]["strainWorkouts"][0]["zoneMinutes"]["zone2"], 5)

    def test_date_with_no_workouts_has_empty_strain_workouts_list(self):
        analytics = _empty_analytics()
        analytics["steps"]["daily"] = [{"date": "2026-01-01", "value": 500}]
        documents = _daily_documents(analytics)
        self.assertEqual(documents[0]["strainWorkouts"], [])

    def test_workout_only_date_still_produces_a_daily_document(self):
        # A date with only a spanning workout and no other metric data must
        # still surface as its own _analytics_daily document.
        workout = {"id": "run-1", "startAt": "2026-01-01T15:00:00Z", "endAt": "2026-01-01T16:00:00Z"}
        documents = _daily_documents(_empty_analytics(workouts=[workout]))
        self.assertEqual([doc["date"] for doc in documents], ["2026-01-01"])
        self.assertIsNone(documents[0]["steps"])


if __name__ == "__main__":
    unittest.main()
