import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.context import AnalyticsContext
from analytics_engine.crypto import cipher_for_user
from analytics_engine.pipeline import process_health_data
from analytics_engine.repository import empty_raw_health_data
from analytics_engine.store import save_analytics


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class AnalyticsApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app

        cls.app = app

    @classmethod
    def tearDownClass(cls):
        from apiVersions.v2.routes import mongo

        mongo.close()

    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.user_id = "test-api-" + uuid.uuid4().hex
        self.other_user_id = "test-api-" + uuid.uuid4().hex
        self.token = "token-" + uuid.uuid4().hex
        self.user = {
            "_id": self.user_id,
            "username": self.user_id,
            "password": "$argon2id$test-hash",
            "token": self.token,
            "expiry": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        }
        self.mongo["hcgateway"]["users"].insert_many([
            self.user,
            {
                "_id": self.other_user_id,
                "username": self.other_user_id,
                "password": "$argon2id$other-hash",
                "token": "other-" + uuid.uuid4().hex,
                "expiry": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
            },
        ])
        raw = empty_raw_health_data()
        raw["steps"] = [{
            "id": "steps-1",
            "source": "pixel",
            "startAt": "2026-01-01T12:00:00Z",
            "endAt": "2026-01-01T13:00:00Z",
            "count": 1234,
        }]
        analytics = process_health_data(raw, AnalyticsContext())
        save_analytics(
            self.mongo["hcgateway_" + self.user_id],
            cipher_for_user(self.user),
            raw,
            analytics,
        )
        self.client = self.app.test_client()

    def tearDown(self):
        control = self.mongo["hcgateway"]
        control["users"].delete_many({"_id": {"$in": [self.user_id, self.other_user_id]}})
        control["analytics_jobs"].delete_many({"_id": {"$in": [self.user_id, self.other_user_id]}})
        for user_id in (self.user_id, self.other_user_id):
            self.mongo.drop_database("hcgateway_" + user_id)
        self.mongo.close()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def test_snapshot_is_frontend_ready_and_scoped_to_authenticated_user(self):
        response = self.client.get("/api/v2/analytics/snapshot", headers=self.auth())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(payload), {"generatedAt", "source", "sleepSessions", "analytics"})
        self.assertEqual(payload["analytics"]["steps"]["daily"][0]["value"], 1234)
        self.assertNotIn("dayViews", payload["analytics"])
        self.assertNotIn("daily", payload["analytics"]["strain"])
        self.assertIn("ETag", response.headers)

        other = self.mongo["hcgateway"]["users"].find_one({"_id": self.other_user_id})
        response = self.client.get(
            "/api/v2/analytics/snapshot",
            headers={"Authorization": "Bearer " + other["token"]},
        )
        self.assertEqual(response.status_code, 404)

    def test_daily_range_and_authentication_errors(self):
        response = self.client.get(
            "/api/v2/analytics/daily?start=2026-01-01&end=2026-01-01",
            headers=self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 1)

        self.assertEqual(self.client.get("/api/v2/analytics/snapshot").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/v2/analytics/snapshot", headers={"Authorization": "not-a-bearer-token"}
            ).status_code,
            401,
        )

    def test_day_contract_includes_values_and_explicit_missing_notes(self):
        response = self.client.get(
            "/api/v2/analytics/day?date=2026-01-01&radius=1",
            headers=self.auth(),
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["contractVersion"], "health-day-v1")
        self.assertEqual(len(payload["nearbyDays"]), 3)
        self.assertEqual(payload["day"]["supportingMetrics"]["steps"]["value"], 1234)
        self.assertEqual(payload["day"]["headlineScores"]["recovery"]["status"], "not_implemented")
        self.assertTrue(any(
            note["field"] == "headlineScores.recovery"
            for note in payload["day"]["availabilityNotes"]
        ))

    def test_config_validation_and_rebuild_queue(self):
        invalid = self.client.put(
            "/api/v2/analytics/config",
            headers=self.auth(),
            json={"homeTimeZone": "Mars/Olympus"},
        )
        self.assertEqual(invalid.status_code, 400)

        accepted = self.client.put(
            "/api/v2/analytics/config",
            headers=self.auth(),
            json={"homeTimeZone": "America/Los_Angeles", "sleepTargetMinutes": 480},
        )
        self.assertEqual(accepted.status_code, 202)
        self.assertEqual(accepted.get_json()["job"]["status"], "queued")

        invalid_zones = self.client.put(
            "/api/v2/analytics/config",
            headers=self.auth(),
            json={"heartRateZoneThresholds": [100, 120, 110, 160, 180, 200]},
        )
        self.assertEqual(invalid_zones.status_code, 400)


if __name__ == "__main__":
    unittest.main()
