import unittest

import requests

from analytics_engine.calendar import (
    FluidCalendarClient,
    FluidCalendarError,
    format_duration,
    is_retryable_status,
    payload_hash,
    render_sleep_event,
)


def sleep_event(stage_status="detailed", stages=None):
    return {
        "id": "sleep-1",
        "date": "2026-08-29",
        "timeZone": "America/Chicago",
        "localStartAt": "2026-08-28T23:00:00.000-05:00",
        "localEndAt": "2026-08-29T07:00:00.000-05:00",
        "role": "main",
        "primary": {
            "id": "sleep-1",
            "source": "watch",
            "startAt": "2026-08-29T04:00:00+00:00",
            "endAt": "2026-08-29T12:00:00Z",
        },
        "recordingCount": 2,
        "windowMinutes": 480,
        "sleepMinutes": 445.4,
        "stageMinutes": stages if stages is not None else {
            "light": 240,
            "deep": 75,
            "rem": 130.4,
            "asleep": 0,
            "awake": 34.6,
            "unknown": 0,
        },
        "stageDataStatus": stage_status,
        "qualityFlags": [],
    }


class FakeResponse:
    def __init__(self, status_code, body=None, invalid_json=False):
        self.status_code = status_code
        self.body = body
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid")
        return self.body


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.response


class CalendarRenderingTests(unittest.TestCase):
    def test_duration_rounding_and_payload_are_deterministic(self):
        self.assertEqual(format_duration(59.5), "1h")
        first = render_sleep_event(sleep_event(), "feed-1")
        second = render_sleep_event(sleep_event(), "feed-1")

        self.assertEqual(first, second)
        self.assertEqual(payload_hash(first), payload_hash(second))
        self.assertEqual(first["start"], "2026-08-29T04:00:00.000Z")
        self.assertEqual(first["end"], "2026-08-29T12:00:00.000Z")
        self.assertEqual(
            first["title"],
            "Sleep · 7h 25m · Light 4h · Deep 1h 15m · REM 2h 10m",
        )
        self.assertIn("- Awake: 35m", first["description"])
        self.assertNotIn("Unknown", first["description"])
        self.assertTrue(first["skipIfExists"])
        self.assertFalse(first["allDay"])

    def test_missing_stages_are_disclosed_without_false_zeroes(self):
        event = sleep_event(stage_status="missing", stages={})
        payload = render_sleep_event(event, "feed-1")
        self.assertEqual(payload["title"], "Sleep · 7h 25m · stages unavailable")
        self.assertIn("Stages recorded: unavailable", payload["description"])
        self.assertIn("total sleep uses the recorded window", payload["description"])
        self.assertNotIn("Deep: 0m", payload["description"])

    def test_supplemental_event_is_not_called_a_nap(self):
        event = sleep_event()
        event["role"] = "supplemental"
        payload = render_sleep_event(event, "feed-1")
        self.assertIn("Session: Supplemental sleep", payload["description"])
        self.assertNotIn("Nap", payload["description"])


class FluidCalendarClientTests(unittest.TestCase):
    def test_create_accepts_created_and_existing_responses(self):
        for status in (200, 201):
            session = FakeSession(FakeResponse(status, {"id": "remote-1"}))
            client = FluidCalendarClient("https://calendar.test/", "secret", session=session)
            self.assertEqual(client.create_event({"title": "Sleep"})["id"], "remote-1")
            method, url, options = session.calls[0]
            self.assertEqual((method, url), ("POST", "https://calendar.test/api/events"))
            self.assertEqual(options["timeout"], (5, 30))

    def test_update_uses_path_id_and_removes_create_only_flag(self):
        session = FakeSession(FakeResponse(200, {"id": "remote-1"}))
        client = FluidCalendarClient("https://calendar.test", "secret", session=session)
        client.update_event("remote/id", {"title": "Sleep", "skipIfExists": True})
        method, url, options = session.calls[0]
        self.assertEqual(method, "PATCH")
        self.assertEqual(url, "https://calendar.test/api/events/remote%2Fid")
        self.assertNotIn("skipIfExists", options["json"])

    def test_status_and_transport_errors_are_classified_without_body_or_key(self):
        self.assertTrue(is_retryable_status(408))
        self.assertTrue(is_retryable_status(429))
        self.assertTrue(is_retryable_status(503))
        self.assertFalse(is_retryable_status(400))
        self.assertFalse(is_retryable_status(404))

        client = FluidCalendarClient(
            "https://calendar.test", "credential-value", session=FakeSession(FakeResponse(401, {"secret": "echo"}))
        )
        with self.assertRaises(FluidCalendarError) as raised:
            client.create_event({})
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertNotIn("credential-value", str(raised.exception))
        self.assertNotIn("echo", str(raised.exception))

        client = FluidCalendarClient(
            "https://calendar.test", "credential-value", session=FakeSession(error=requests.Timeout("timeout"))
        )
        with self.assertRaises(FluidCalendarError) as raised:
            client.create_event({})
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn("credential-value", str(raised.exception))

    def test_invalid_success_response_is_retryable(self):
        client = FluidCalendarClient(
            "https://calendar.test", "secret", session=FakeSession(FakeResponse(201, invalid_json=True))
        )
        with self.assertRaises(FluidCalendarError) as raised:
            client.create_event({})
        self.assertTrue(raised.exception.retryable)


if __name__ == "__main__":
    unittest.main()
