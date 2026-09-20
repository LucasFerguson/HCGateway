import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.calendar import (
    BACKFILLS,
    DELIVERIES,
    RECONCILIATIONS,
    FluidCalendarError,
    claim_backfill_window,
    claim_reconciliation,
    claim_sleep_delivery,
    complete_backfill_window,
    complete_reconciliation,
    complete_sleep_delivery,
    fail_reconciliation,
    fail_sleep_delivery,
    initialize_backfill,
    queue_sleep_delivery,
    render_sleep_event,
    requeue_sleep_delivery,
)
from analytics_engine.calendar_worker import maybe_run_reconciliation, reconcile_window
from analytics_engine.crypto import cipher_for_user
from test_calendar import sleep_event


class FakeReconciliationClient:
    """Records windowed list_events calls; never issues a write call."""

    def __init__(self, events, *, truncated=False, has_more=False):
        self.events = events
        self.truncated = truncated
        self.has_more = has_more
        self.list_calls = []

    def list_events(self, start, end):
        self.list_calls.append((start, end))
        return {
            "events": self.events,
            "window": {"start": start, "end": end},
            "count": len(self.events),
            "hasMore": self.has_more,
            "truncated": self.truncated,
        }

    def create_event(self, payload):
        raise AssertionError("reconciliation must never write to FluidCalendar")

    def update_event(self, event_id, payload):
        raise AssertionError("reconciliation must never write to FluidCalendar")


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

    def test_permanent_failure_can_be_requeued_by_reconciliation(self):
        queued = queue_sleep_delivery(
            self.database, "user-1", "feed-1", sleep_event(), "run-1", {"title": "Sleep"}, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        error = FluidCalendarError("gone", retryable=False, status_code=500)
        self.assertTrue(fail_sleep_delivery(self.database, claimed, error, now=self.now))
        failed = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(failed["state"], "permanent_failure")
        self.assertEqual(failed["attempts"], 1)

        self.assertTrue(requeue_sleep_delivery(self.database, failed, now=self.now))
        reset = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(reset["state"], "pending")
        self.assertEqual(reset["operation"], "create")
        self.assertEqual(reset["attempts"], 0)
        self.assertNotIn("error", reset)
        self.assertNotIn("failedAt", reset)
        self.assertIn("nextAttemptAt", reset)

        # The normal delivery loop can now claim and complete it.
        reclaimed = claim_sleep_delivery(self.database, "worker-2", now=self.now)
        self.assertIsNotNone(reclaimed)
        self.assertTrue(complete_sleep_delivery(
            self.database, reclaimed, {"id": "remote-1"}, now=self.now
        ))
        delivered = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(delivered["state"], "delivered")

    def test_delivered_row_missing_from_fluidcalendar_can_be_requeued_without_stale_remote_id(self):
        queued = queue_sleep_delivery(
            self.database, "user-1", "feed-1", sleep_event(), "run-1", {"title": "Sleep"}, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        self.assertTrue(complete_sleep_delivery(
            self.database, claimed, {"id": "remote-1", "externalEventId": "google-1"}, now=self.now
        ))
        delivered = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(delivered["state"], "delivered")

        self.assertTrue(requeue_sleep_delivery(self.database, delivered, now=self.now))
        reset = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(reset["state"], "pending")
        self.assertEqual(reset["operation"], "create")
        self.assertNotIn("remoteEventId", reset)
        self.assertNotIn("externalEventId", reset)
        self.assertNotIn("deliveredAt", reset)

    def test_requeue_does_not_touch_a_row_already_claimed_by_another_worker(self):
        queued = queue_sleep_delivery(
            self.database, "user-1", "feed-1", sleep_event(), "run-1", {"title": "Sleep"}, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        # Reconciliation read a stale ("permanent_failure") snapshot, but the
        # row has since moved to "delivering" under an active lease. The
        # exact-prior-state match must refuse to stomp on it.
        stale_view = dict(claimed)
        stale_view["state"] = "permanent_failure"
        self.assertFalse(requeue_sleep_delivery(self.database, stale_view, now=self.now))
        still_delivering = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(still_delivering["state"], "delivering")

    def test_reconciliation_claim_is_immediate_once_then_rate_limited_and_lease_scoped(self):
        state = claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-1", interval_seconds=3600, now=self.now
        )
        self.assertIsNotNone(state)
        self.assertEqual(state["status"], "running")

        # Already running under a live lease: a second worker cannot claim it.
        self.assertIsNone(claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-2", interval_seconds=3600, now=self.now
        ))

        self.assertTrue(complete_reconciliation(self.database, state, now=self.now))
        stored = self.database[RECONCILIATIONS].find_one({"_id": state["_id"]})
        self.assertEqual(stored["status"], "ready")
        self.assertIsNotNone(stored["lastRunAt"])

        # Immediately after completion, still within the interval: not due.
        self.assertIsNone(claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-1", interval_seconds=3600, now=self.now
        ))
        later = self.now + dt.timedelta(seconds=3600)
        due_again = claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-1", interval_seconds=3600, now=later
        )
        self.assertIsNotNone(due_again)

    def test_failed_reconciliation_releases_the_lease_for_a_later_retry(self):
        state = claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-1", interval_seconds=3600, now=self.now
        )
        self.assertTrue(fail_reconciliation(
            self.database, state, FluidCalendarError("boom", retryable=True), now=self.now
        ))
        stored = self.database[RECONCILIATIONS].find_one({"_id": state["_id"]})
        self.assertEqual(stored["status"], "ready")
        self.assertIsNone(stored.get("lastRunAt"))
        # A failure does not advance lastRunAt, so it is due again right away.
        retried = claim_reconciliation(
            self.database, "user-1", "feed-1", "worker-1", interval_seconds=3600, now=self.now
        )
        self.assertIsNotNone(retried)

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


class ReconciliationConfig:
    """A minimal stand-in for CalendarWorkerConfig's fields reconcile_window reads."""

    def __init__(self, user_id="user-1", feed_id="feed-1"):
        self.user_id = user_id
        self.feed_id = feed_id


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class ReconciliationIntegrationTests(unittest.TestCase):
    """Exercises reconcile_window/maybe_run_reconciliation against real Mongo.

    FluidCalendar itself is always a fake client here (never a live HTTP
    call) so these tests are safe to run anywhere, but the ledger and
    prepared-event reads go through real Mongo the same way the worker does.
    """

    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.database_name = "hcgateway_test_calendar_" + uuid.uuid4().hex
        self.database = self.mongo[self.database_name]
        self.cipher = cipher_for_user({"_id": "test-user", "password": "$argon2id$test-hash"})
        self.now = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
        self.config = ReconciliationConfig()

    def tearDown(self):
        if self.database_name.startswith("hcgateway_test_calendar_"):
            self.mongo.drop_database(self.database_name)
        self.mongo.close()

    def _seed_prepared_run(self, run_id, events):
        self.database["_analytics_current"].insert_one({"_id": "current", "runId": run_id})
        for event in events:
            self.database["_analytics_sleep_events"].insert_one({
                "runId": run_id,
                "eventId": event["id"],
                "date": event["date"],
                "data": event,
            })

    def test_permanent_failure_missing_from_fluidcalendar_is_requeued(self):
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queued = queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        fail_sleep_delivery(
            self.database, claimed, FluidCalendarError("HTTP 500", retryable=False, status_code=500), now=self.now
        )
        self.assertEqual(
            self.database[DELIVERIES].find_one({"_id": queued["_id"]})["state"], "permanent_failure"
        )

        client = FakeReconciliationClient(events=[])  # nothing exists on FluidCalendar's side
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )

        self.assertEqual(result, {"checked": 1, "missing": 1, "requeued": 1})
        reset = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(reset["state"], "pending")
        self.assertEqual(reset["attempts"], 0)
        self.assertEqual(len(client.list_calls), 1)

    def test_delivered_event_present_on_fluidcalendar_is_left_alone(self):
        """A genuinely delivered, still-present event must never be touched.

        This is the safety-critical case: production has real, correctly
        delivered calendar events, and a reconciliation bug here (bad
        pagination handling, a timezone mismatch, misreading the response
        shape) could re-trigger a duplicate creation against a live account.
        """
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queued = queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        complete_sleep_delivery(
            self.database, claimed, {"id": "remote-1", "externalEventId": "google-1"}, now=self.now
        )
        before = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(before["state"], "delivered")

        remote_event = {
            "id": "remote-1",
            "feedId": self.config.feed_id,
            "title": payload["title"],
            "start": payload["start"],
            "end": payload["end"],
            "description": payload["description"],
            "status": "confirmed",
        }
        client = FakeReconciliationClient(events=[remote_event])
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )

        self.assertEqual(result, {"checked": 1, "missing": 0, "requeued": 0})
        after = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(after, before)

    def test_cancelled_remote_event_still_counts_as_missing(self):
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queued = queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        complete_sleep_delivery(self.database, claimed, {"id": "remote-1"}, now=self.now)

        remote_event = {
            "id": "remote-1", "feedId": self.config.feed_id, "title": payload["title"],
            "start": payload["start"], "end": payload["end"], "description": payload["description"],
            "status": "cancelled",
        }
        client = FakeReconciliationClient(events=[remote_event])
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )

        self.assertEqual(result, {"checked": 1, "missing": 1, "requeued": 1})
        reset = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(reset["state"], "pending")

    def test_event_present_under_a_different_feed_still_counts_as_missing(self):
        """The FluidCalendar list endpoint is not feed-scoped server-side, so
        reconciliation must filter by feedId itself before matching -- an id
        collision on an unrelated feed must not be treated as "found"."""
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queued = queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        complete_sleep_delivery(self.database, claimed, {"id": "remote-1"}, now=self.now)

        remote_event = {
            "id": "remote-1", "feedId": "some-other-feed", "title": payload["title"],
            "start": payload["start"], "end": payload["end"], "description": payload["description"],
            "status": "confirmed",
        }
        client = FakeReconciliationClient(events=[remote_event])
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )

        self.assertEqual(result, {"checked": 1, "missing": 1, "requeued": 1})
        reset = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(reset["state"], "pending")

    def test_permanent_failure_matched_by_strict_fields_is_marked_delivered_not_duplicated(self):
        """The create actually succeeded server-side but the ledger update was lost.

        Reconciliation must recognize the existing event by FluidCalendar's
        own strict skipIfExists fields and requeue for a normal create retry
        rather than leaving it permanently stuck -- but it must never fabricate
        a remote id or itself call create/update.
        """
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queued = queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        fail_sleep_delivery(
            self.database, claimed, FluidCalendarError("HTTP 500", retryable=False, status_code=500), now=self.now
        )

        remote_event = {
            "id": "remote-orphaned", "feedId": self.config.feed_id, "title": payload["title"],
            "start": payload["start"], "end": payload["end"], "description": payload["description"],
            "status": "confirmed",
        }
        client = FakeReconciliationClient(events=[remote_event])
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )

        # It was found (by content match), so it is not requeued at all --
        # requeuing here would be a duplicate creation waiting to happen.
        self.assertEqual(result, {"checked": 1, "missing": 0, "requeued": 0})
        unchanged = self.database[DELIVERIES].find_one({"_id": queued["_id"]})
        self.assertEqual(unchanged["state"], "permanent_failure")

    def test_truncated_listing_raises_instead_of_guessing(self):
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        fail_sleep_delivery(
            self.database, claimed, FluidCalendarError("HTTP 500", retryable=False, status_code=500), now=self.now
        )

        client = FakeReconciliationClient(events=[], truncated=True)
        with self.assertRaises(FluidCalendarError):
            reconcile_window(
                self.database, self.cipher, client, self.config,
                dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
            )

    def test_no_candidates_in_window_does_not_call_fluidcalendar(self):
        client = FakeReconciliationClient(events=[])
        result = reconcile_window(
            self.database, self.cipher, client, self.config,
            dt.date(2026, 8, 23), dt.date(2026, 9, 20), now=self.now,
        )
        self.assertEqual(result, {"checked": 0, "missing": 0, "requeued": 0})
        self.assertEqual(client.list_calls, [])

    def test_maybe_run_reconciliation_completes_lease_and_is_rate_limited(self):
        event = sleep_event()
        self._seed_prepared_run("run-1", [event])
        payload = render_sleep_event(event, self.config.feed_id)
        queue_sleep_delivery(
            self.database, self.config.user_id, self.config.feed_id, event, "run-1", payload, now=self.now
        )
        claimed = claim_sleep_delivery(self.database, "worker-1", now=self.now)
        fail_sleep_delivery(
            self.database, claimed, FluidCalendarError("HTTP 500", retryable=False, status_code=500), now=self.now
        )

        class Cfg(ReconciliationConfig):
            reconcile_enabled = True
            reconcile_window_days = 7
            reconcile_interval_seconds = 3600
            lease_seconds = 300

        client = FakeReconciliationClient(events=[])
        result = maybe_run_reconciliation(
            self.database, self.cipher, client, Cfg(), "worker-1", dt.date(2026, 8, 29), now=self.now
        )
        self.assertEqual(result, {"checked": 1, "missing": 1, "requeued": 1})

        # Immediately re-running is rate-limited and does not call FluidCalendar again.
        second = maybe_run_reconciliation(
            self.database, self.cipher, client, Cfg(), "worker-1", dt.date(2026, 8, 29), now=self.now
        )
        self.assertIsNone(second)
        self.assertEqual(len(client.list_calls), 1)


if __name__ == "__main__":
    unittest.main()
