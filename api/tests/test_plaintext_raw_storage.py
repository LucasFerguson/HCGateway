import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.crypto import PLAIN_STORAGE_FORMAT, cipher_for_user, encrypt_json
from analytics_engine.repository import load_raw_health_data


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class PlaintextRawStorageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app

        cls.app = app

    def setUp(self):
        from apiVersions.v2 import routes

        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        routes.mongo.close()
        routes.mongo = self.mongo
        self.user_id = "test-api-plain-" + uuid.uuid4().hex
        self.token = "token-" + uuid.uuid4().hex
        self.user = {
            "_id": self.user_id,
            "username": self.user_id,
            "password": "$argon2id$plaintext-storage-test-hash",
            "token": self.token,
            "expiry": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        }
        self.mongo["hcgateway"]["users"].insert_one(self.user)
        self.client = self.app.test_client()

    def tearDown(self):
        control = self.mongo["hcgateway"]
        control["users"].delete_one({"_id": self.user_id})
        control["analytics_jobs"].delete_one({"_id": self.user_id})
        control["sync_status"].delete_one({"_id": self.user_id})
        self.mongo.drop_database("hcgateway_" + self.user_id)
        self.mongo.close()

    def auth(self):
        return {"Authorization": "Bearer " + self.token}

    def test_new_sync_is_plain_bson_and_readers_accept_mixed_storage(self):
        response = self.client.post(
            "/api/v2/sync/Steps",
            headers=self.auth(),
            json={"data": {
                "metadata": {"id": "plain-step", "dataOrigin": "test.phone"},
                "startTime": "2026-01-02T12:00:00-06:00",
                "endTime": "2026-01-02T13:00:00-06:00",
                "count": 500,
            }},
        )
        self.assertEqual(response.status_code, 200)

        collection = self.mongo["hcgateway_" + self.user_id]["steps"]
        stored = collection.find_one({"_id": "plain-step"})
        self.assertEqual(stored["storageFormat"], PLAIN_STORAGE_FORMAT)
        self.assertEqual(stored["data"], {"count": 500})
        self.assertEqual(stored["start"], "2026-01-02T12:00:00-06:00")
        self.assertEqual(stored["startInstant"], dt.datetime(2026, 1, 2, 18, 0))
        self.assertEqual(stored["endInstant"], dt.datetime(2026, 1, 2, 19, 0))
        self.assertIn("startInstant_1_app_1", collection.index_information())

        collection.insert_one({
            "_id": "legacy-step",
            "id": "legacy-step",
            "data": encrypt_json(cipher_for_user(self.user), {"count": 250}),
            "app": "test.watch",
            "start": "2026-01-02T19:00:00Z",
            "end": "2026-01-02T20:00:00Z",
        })

        fetched = self.client.post(
            "/api/v2/fetch/steps", headers=self.auth(), json={"queries": {}}
        )
        self.assertEqual(fetched.status_code, 200)
        by_id = {item["id"]: item for item in fetched.get_json()}
        self.assertEqual(by_id["plain-step"]["data"], {"count": 500})
        self.assertEqual(by_id["legacy-step"]["data"], {"count": 250})

        raw, issues = load_raw_health_data(
            self.mongo["hcgateway_" + self.user_id], cipher_for_user(self.user)
        )
        self.assertEqual(issues, [])
        self.assertEqual(
            [(record["id"], record["count"]) for record in raw["steps"]],
            [("legacy-step", 250), ("plain-step", 500)],
        )


if __name__ == "__main__":
    unittest.main()
