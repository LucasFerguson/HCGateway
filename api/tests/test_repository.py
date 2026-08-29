import unittest

from analytics_engine.repository import _exercise_session, _sleep


class RepositoryTimezoneTests(unittest.TestCase):
    def test_sleep_normalization_emits_canonical_utc_and_preserves_source_offsets(self):
        document = {
            "_id": "sleep", "id": "sleep", "app": "watch",
            "start": "2026-01-01T23:00:00-06:00", "end": "2026-01-02T07:00:00-06:00",
        }
        data = {
            "startZoneOffset": "-06:00", "endZoneOffset": "-06:00",
            "stages": [{
                "startTime": "2026-01-01T23:00:00-06:00",
                "endTime": "2026-01-02T07:00:00-06:00",
                "stage": 4,
            }],
        }
        normalized = _sleep(document, data)
        self.assertEqual(normalized["startAt"], "2026-01-02T05:00:00.000Z")
        self.assertEqual(normalized["endAt"], "2026-01-02T13:00:00.000Z")
        self.assertEqual(normalized["stages"][0]["startAt"], "2026-01-02T05:00:00.000Z")
        self.assertEqual(normalized["sourceZoneOffsets"], {"start": "-06:00", "end": "-06:00"})

    def test_exercise_normalization_retains_travel_offset_provenance(self):
        document = {
            "_id": "exercise", "id": "exercise", "app": "watch",
            "start": "2026-07-01T08:00:00-07:00", "end": "2026-07-01T09:00:00-07:00",
        }
        normalized = _exercise_session(document, {
            "exerciseType": 8, "startZoneOffset": "-07:00", "endZoneOffset": "-07:00",
        })
        self.assertEqual(normalized["startAt"], "2026-07-01T15:00:00.000Z")
        self.assertEqual(normalized["sourceZoneOffsets"], {"start": "-07:00", "end": "-07:00"})


if __name__ == "__main__":
    unittest.main()
