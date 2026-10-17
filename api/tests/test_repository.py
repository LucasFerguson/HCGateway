import unittest

from analytics_engine.repository import MAPPERS, _exercise_session, _skin_temperature, _sleep, empty_raw_health_data


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


SKIN_DOCUMENT = {
    "_id": "skin", "id": "skin", "app": "com.google.android.apps.fitness",
    "start": "2026-10-16T05:00:00Z", "end": "2026-10-16T12:30:00Z",
}


def skin_data(**overrides):
    data = {
        "baseline": {"inCelsius": 33.9, "inFahrenheit": 93.02},
        "deltas": [
            {"time": "2026-10-16T06:00:00Z", "delta": {"inCelsius": -0.3, "inFahrenheit": -0.54}},
            {"time": "2026-10-16T07:00:00Z", "delta": {"inCelsius": 0.1, "inFahrenheit": 0.18}},
            {"time": "2026-10-16T08:00:00Z", "delta": {"inCelsius": 0.5, "inFahrenheit": 0.9}},
        ],
        "measurementLocation": 0,
    }
    data.update(overrides)
    return data


class SkinTemperatureMapperTests(unittest.TestCase):
    def test_registered_with_its_own_target(self):
        self.assertEqual(MAPPERS["skinTemperature"], ("skinTemperatures", _skin_temperature))
        self.assertEqual(empty_raw_health_data()["skinTemperatures"], [])

    def test_maps_mean_delta_on_the_wake_instant(self):
        normalized = _skin_temperature(SKIN_DOCUMENT, skin_data())
        self.assertEqual(normalized["id"], "skin")
        self.assertEqual(normalized["source"], "com.google.android.apps.fitness")
        self.assertEqual(normalized["observedAt"], "2026-10-16T12:30:00.000Z")
        self.assertAlmostEqual(normalized["celsiusDelta"], 0.1)
        self.assertEqual(normalized["baselineCelsius"], 33.9)
        self.assertEqual(normalized["measurementLocation"], 0)

    def test_missing_baseline_stays_none(self):
        self.assertIsNone(_skin_temperature(SKIN_DOCUMENT, skin_data(baseline=None))["baselineCelsius"])
        data = skin_data()
        del data["baseline"]
        self.assertIsNone(_skin_temperature(SKIN_DOCUMENT, data)["baselineCelsius"])

    def test_record_without_deltas_raises_instead_of_becoming_zero(self):
        for deltas in ([], None):
            with self.assertRaises(ValueError):
                _skin_temperature(SKIN_DOCUMENT, skin_data(deltas=deltas))
        data = skin_data()
        del data["deltas"]
        with self.assertRaises(ValueError):
            _skin_temperature(SKIN_DOCUMENT, data)

    def test_non_numeric_delta_raises(self):
        with self.assertRaises(ValueError):
            _skin_temperature(SKIN_DOCUMENT, skin_data(deltas=[{"time": "2026-10-16T06:00:00Z", "delta": {}}]))

    def test_requires_end_timestamp(self):
        with self.assertRaises(ValueError):
            _skin_temperature({**SKIN_DOCUMENT, "end": None}, skin_data())


if __name__ == "__main__":
    unittest.main()
