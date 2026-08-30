import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.calendar import (
    BACKFILLS,
    DELIVERIES,
    FluidCalendarError,
    claim_backfill_window,
    claim_sleep_delivery,
    complete_backfill_window,
    complete_sleep_delivery,
    fail_sleep_delivery,
    initialize_backfill,
    queue_sleep_delivery,
)
from test_calendar import sleep_event


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class CalendarMongoTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.database_name = "hcgateway_test_calendar_" + uuid.uuid4().hex
        self.database = self.mongo[self.database_name]
        self.now = dt.datetime(2026, 8, 29, tzinfo=dt.timezone.utc)

    def tearDown(self):
        if self.database_name.startswith("hcgateway_test_calendar_"):
            self.mongo.drop_database(self.database_name)
        self.mongo.close()

    def test_delivery_create_retry_complete_and_changed_payload_update(self):
        event = sleep_event()
        payload = {"title": "Sleep", "skipIfExists": True}
        queued = queue_sleep_delivery(
            self.database, "user-1", "feed-1", event, "run-1", payload, now=self.now
        )
        self.assertEqual(queued["state"], "pending")
        self.assertNotIn("payload", queued)

        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        self.assertEqual(claimed["attempts"], 1)
        failure = FluidCalendarError("temporary", retryable=True, status_code=503)
        self.assertTrue(fail_sleep_delivery(
            self.database, claimed, failure, retry_delay_seconds=0, now=self.now
        ))
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        self.assertTrue(complete_sleep_delivery(
            self.database, claimed, {"id": "remote-1", "externalEventId": "google-1"}, now=self.now
        ))
        delivered = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(delivered["state"], "delivered")

        changed = queue_sleep_delivery(
            self.database, "user-1", "feed-1", event, "run-2",
            {"title": "Sleep changed", "skipIfExists": True}, now=self.now,
        )
        self.assertEqual(changed["state"], "pending")
        self.assertEqual(changed["operation"], "update")
        self.assertEqual(changed["remoteEventId"], "remote-1")

    def test_permanent_failure_is_not_claimed_again(self):
        queued = queue_sleep_delivery(
            self.database, "user-1", "feed-1", sleep_event(), "run-1", {"title": "Sleep"}, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        error = FluidCalendarError("unauthorized", retryable=False, status_code=401)
        self.assertTrue(fail_sleep_delivery(self.database, claimed, error, now=self.now))
        self.assertEqual(self.database[DELIVERIES].find_one({"_id": queued["_id"]})["state"], "permanent_failure")
        self.assertIsNone(claim_sleep_delivery(self.database, "worker-1", now=self.now))

    def test_backfill_cursor_moves_backward_only_after_completion(self):
        initialized = initialize_backfill(
            self.database, "user-1", "feed-1", "2026-08-22", now=self.now
        )
        self.assertEqual(initialized["nextEndDate"], "2026-08-22")
        state = claim_backfill_window(
            self.database, "user-1", "feed-1", "worker-1", batch_days=7, now=self.now
        )
        self.assertEqual(state["windowStartDate"], "2026-08-16")
        self.assertEqual(state["windowEndDate"], "2026-08-22")
        self.assertTrue(complete_backfill_window(self.database, state, now=self.now))
        stored = self.database[BACKFILLS].find_one({"_id": initialized["_id"]})
        self.assertEqual(stored["nextEndDate"], "2026-08-15")
        self.assertEqual(stored["status"], "ready")


if __name__ == "__main__":
    unittest.main()
