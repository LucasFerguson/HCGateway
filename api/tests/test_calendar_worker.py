import datetime as dt
import unittest
from unittest.mock import Mock, patch

from analytics_engine.calendar_worker import (
    CalendarWorkerConfig,
    _backfill_due,
    _dates_for_recent_window,
    deliver_one,
    queue_window,
)


UTC = dt.timezone.utc


def config(**overrides):
    values = {
        "mongo_uri": "mongodb://example",
        "base_url": "https://calendar.example",
        "api_key": "fc_secret",
        "user_id": "user-1",
        "feed_id": "feed-1",
    }
    values.update(overrides)
    return CalendarWorkerConfig(**values)


class CalendarWorkerConfigurationTests(unittest.TestCase):
    def test_required_configuration_and_secret_safe_repr(self):
        environment = {
            "MONGO_URI": "mongodb://example",
            "FLUIDCALENDAR_BASE_URL": "https://calendar.example/",
            "FLUIDCALENDAR_API_KEY": "fc_do-not-render",
            "CALENDAR_SLEEP_USER_ID": "user-1",
            "CALENDAR_SLEEP_FEED_ID": "feed-1",
        }
        parsed = CalendarWorkerConfig.from_environment(environment)
        self.assertEqual(parsed.initial_lookback_days, 7)
        self.assertEqual(parsed.base_url, "https://calendar.example")
        self.assertNotIn(parsed.api_key, repr(parsed))
        self.assertNotIn(parsed.mongo_uri, repr(parsed))

    def test_invalid_boolean_is_rejected(self):
        environment = {
            "MONGO_URI": "mongodb://example",
            "FLUIDCALENDAR_BASE_URL": "https://calendar.example",
            "FLUIDCALENDAR_API_KEY": "fc_secret",
            "CALENDAR_SLEEP_USER_ID": "user-1",
            "CALENDAR_SLEEP_FEED_ID": "feed-1",
            "CALENDAR_SLEEP_BACKFILL_ENABLED": "sometimes",
        }
        with self.assertRaisesRegex(ValueError, "must be true or false"):
            CalendarWorkerConfig.from_environment(environment)

    def test_recent_window_uses_configured_home_timezone(self):
        user = {
            "analyticsConfig": {"homeTimeZone": "America/Chicago"},
        }
        start, end = _dates_for_recent_window(
            user, 7, dt.datetime(2026, 8, 30, 2, 0, tzinfo=UTC)
        )
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-08-23", "2026-08-29"))


class CalendarWorkerOrchestrationTests(unittest.TestCase):
    @patch("analytics_engine.calendar_worker.queue_sleep_delivery")
    @patch("analytics_engine.calendar_worker.render_sleep_event")
    @patch("analytics_engine.calendar_worker.read_sleep_events")
    def test_queue_window_includes_main_and_supplemental_events(
        self, read_sleep_events, render_sleep_event, queue_sleep_delivery
    ):
        events = [
            {"id": "main", "date": "2026-08-29", "role": "main"},
            {"id": "nap", "date": "2026-08-29", "role": "supplemental"},
        ]
        read_sleep_events.return_value = (events, {"runId": "run-current"})
        render_sleep_event.side_effect = lambda event, feed: {"event": event["id"], "feedId": feed}
        now = dt.datetime(2026, 8, 29, 12, tzinfo=UTC)

        count, current = queue_window(
            Mock(), Mock(), config(), dt.date(2026, 8, 23), dt.date(2026, 8, 29), now=now
        )

        self.assertEqual(count, 2)
        self.assertEqual(current["runId"], "run-current")
        self.assertEqual(
            [call.args[5]["event"] for call in queue_sleep_delivery.call_args_list],
            ["main", "nap"],
        )

    @patch("analytics_engine.calendar_worker.complete_sleep_delivery", return_value=True)
    @patch("analytics_engine.calendar_worker.payload_hash", return_value="same")
    @patch("analytics_engine.calendar_worker.render_sleep_event", return_value={"payload": True})
    @patch("analytics_engine.calendar_worker._current_event")
    @patch("analytics_engine.calendar_worker.claim_sleep_delivery")
    def test_new_delivery_posts_event(
        self, claim, current_event, _render, _hash, complete
    ):
        delivery = {
            "_id": "delivery-1",
            "preparedEventId": "sleep-1",
            "wakeDate": "2026-08-29",
            "payloadHash": "same",
        }
        claim.return_value = delivery
        current_event.return_value = ({"id": "sleep-1"}, {"runId": "run-current"})
        client = Mock()
        client.create_event.return_value = {"id": "remote-1"}

        self.assertTrue(deliver_one(Mock(), Mock(), client, config(), "worker-1"))
        client.create_event.assert_called_once_with({"payload": True})
        client.update_event.assert_not_called()
        complete.assert_called_once()

    @patch("analytics_engine.calendar_worker.complete_sleep_delivery", return_value=True)
    @patch("analytics_engine.calendar_worker.payload_hash", return_value="changed")
    @patch("analytics_engine.calendar_worker.render_sleep_event", return_value={"payload": True})
    @patch("analytics_engine.calendar_worker._current_event")
    @patch("analytics_engine.calendar_worker.claim_sleep_delivery")
    def test_changed_delivery_patches_known_remote_event(
        self, claim, current_event, _render, _hash, _complete
    ):
        claim.return_value = {
            "_id": "delivery-1",
            "preparedEventId": "sleep-1",
            "wakeDate": "2026-08-29",
            "payloadHash": "changed",
            "remoteEventId": "remote-1",
        }
        current_event.return_value = ({"id": "sleep-1"}, {"runId": "run-current"})
        client = Mock()
        client.update_event.return_value = {"id": "remote-1"}

        self.assertTrue(deliver_one(Mock(), Mock(), client, config(), "worker-1"))
        client.update_event.assert_called_once_with("remote-1", {"payload": True})
        client.create_event.assert_not_called()

    def test_backfill_is_immediate_once_then_rate_limited(self):
        now = dt.datetime(2026, 8, 29, 12, tzinfo=UTC)
        self.assertTrue(_backfill_due({"status": "ready", "nextEndDate": "2026-08-22"}, 3600, now))
        state = {
            "status": "ready",
            "nextEndDate": "2026-08-15",
            "lastCompletedEndDate": "2026-08-22",
            "updatedAt": now - dt.timedelta(seconds=3599),
        }
        self.assertFalse(_backfill_due(state, 3600, now))
        state["updatedAt"] = now - dt.timedelta(seconds=3600)
        self.assertTrue(_backfill_due(state, 3600, now))


if __name__ == "__main__":
    unittest.main()
