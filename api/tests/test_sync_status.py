import datetime as dt
import unittest

from analytics_engine.sync_status import ACTIVE_WINDOW_SECONDS, status_response


class SyncStatusTests(unittest.TestCase):
    def test_recent_upload_is_active_and_old_upload_is_idle(self):
        now = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
        document = {
            "lastUploadAt": now - dt.timedelta(seconds=30),
            "activeUntil": now + dt.timedelta(seconds=ACTIVE_WINDOW_SECONDS - 30),
            "lastRecordType": "heartRate",
            "lastRecordCount": 10,
            "totalUploadRequests": 3,
            "totalRecordsReceived": 25,
        }
        active = status_response(document, now)
        self.assertTrue(active["observedActive"])
        self.assertEqual(active["state"], "receiving")
        self.assertEqual(active["secondsSinceLastUpload"], 30)

        document["activeUntil"] = now - dt.timedelta(seconds=1)
        idle = status_response(document, now)
        self.assertFalse(idle["observedActive"])
        self.assertEqual(idle["state"], "idle")

    def test_unobserved_status_is_explicit(self):
        result = status_response(None)
        self.assertEqual(result["state"], "never_observed")
        self.assertFalse(result["observedActive"])

    def test_mongodb_naive_utc_datetimes_are_normalized(self):
        now = dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc)
        result = status_response({
            "lastUploadAt": dt.datetime(2026, 1, 1, 11, 59, 30),
            "activeUntil": dt.datetime(2026, 1, 1, 12, 1, 30),
        }, now)
        self.assertTrue(result["observedActive"])
        self.assertTrue(result["lastUploadAt"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
