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
        control["sync_status"].delete_many({"_id": {"$in": [self.user_id, self.other_user_id]}})
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
        self.assertNotIn("daily", payload["analytics"]["recovery"])
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
        self.assertEqual(payload["day"]["headlineScores"]["recovery"]["status"], "insufficient_data")
        self.assertTrue(any(
            note["field"] == "headlineScores.recovery"
            for note in payload["day"]["availabilityNotes"]
        ))

    def test_phone_sync_status_tracks_recent_upload_activity(self):
        upload = self.client.post(
            "/api/v2/sync/Steps",
            headers=self.auth(),
            json={"data": {
                "metadata": {"id": "phone-steps", "dataOrigin": "test.phone"},
                "startTime": "2026-01-02T12:00:00Z",
                "endTime": "2026-01-02T13:00:00Z",
                "count": 500,
            }},
        )
        self.assertEqual(upload.status_code, 200)

        response = self.client.get("/api/v2/sync/status", headers=self.auth())
        status = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(status["observedActive"])
        self.assertEqual(status["state"], "receiving")
        self.assertEqual(status["lastRecordType"], "steps")
        self.assertEqual(status["lastRecordCount"], 1)

        analytics_status = self.client.get("/api/v2/analytics/status", headers=self.auth()).get_json()
        self.assertTrue(analytics_status["phoneSync"]["observedActive"])

    def test_device_inventory_preserves_provenance_and_flags_ambiguous_sources(self):
        records = [
            {
                "metadata": {
                    "id": "watch-steps",
                    "dataOrigin": "com.fitbit.FitbitMobile",
                    "device": {"manufacturer": None, "model": None, "type": 0},
                    "recordingMethod": 2,
                },
                "startTime": "2026-02-05T12:00:00Z",
                "endTime": "2026-02-05T13:00:00Z",
                "count": 500,
            },
            {
                "metadata": {
                    "id": "phone-steps-with-device",
                    "dataOrigin": "com.android.healthconnect.phone.test",
                    "device": {"manufacturer": "Example", "model": "Phone 1", "type": 2},
                    "recordingMethod": 2,
                },
                "startTime": "2026-02-05T13:00:00Z",
                "endTime": "2026-02-05T14:00:00Z",
                "count": 250,
            },
        ]
        response = self.client.post(
            "/api/v2/sync/Steps", headers=self.auth(), json={"data": records}
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.get("/api/v2/analytics/devices", headers=self.auth())
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["count"], 2)

        by_source = {item["sourcePackage"]: item for item in payload["devices"]}
        fitbit = by_source["com.fitbit.FitbitMobile"]
        self.assertEqual(fitbit["identityQuality"], "source_only")
        self.assertTrue(fitbit["mayCombinePhysicalDevices"])
        self.assertEqual(fitbit["recordingMethods"][0]["label"], "automatically_recorded")

        phone = by_source["com.android.healthconnect.phone.test"]
        self.assertEqual(phone["identityQuality"], "explicit_model")
        self.assertFalse(phone["mayCombinePhysicalDevices"])
        self.assertEqual(phone["device"]["typeLabel"], "phone")
        self.assertEqual(phone["signals"], {"steps": 1})

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
