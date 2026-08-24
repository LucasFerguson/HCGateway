import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.context import AnalyticsContext
from analytics_engine.crypto import cipher_for_user
from analytics_engine.jobs import claim_job, complete_job, enqueue_job
from analytics_engine.pipeline import process_health_data
from analytics_engine.repository import empty_raw_health_data
from analytics_engine.store import current_metadata, read_snapshot, save_analytics


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class MongoAnalyticsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.database_name = "hcgateway_test_analytics_" + uuid.uuid4().hex
        self.database = self.mongo[self.database_name]
        self.user = {"_id": "test-user", "password": "$argon2id$test-hash"}
        self.cipher = cipher_for_user(self.user)

    def tearDown(self):
        if self.database_name.startswith("hcgateway_test_analytics_"):
            self.mongo.drop_database(self.database_name)
        self.mongo.close()

    def test_immutable_run_is_idempotent_and_snapshot_round_trips(self):
        raw = empty_raw_health_data()
        raw["steps"] = [{
            "id": "steps-1",
            "source": "watch",
            "startAt": "2026-01-01T12:00:00Z",
            "endAt": "2026-01-01T13:00:00Z",
            "count": 500,
        }]
        analytics = process_health_data(raw, AnalyticsContext())
        first, run_id = save_analytics(self.database, self.cipher, raw, analytics)
        second, second_run_id = save_analytics(self.database, self.cipher, raw, analytics)
        snapshot, current = read_snapshot(self.database, self.cipher)

        self.assertEqual(first, "saved")
        self.assertEqual(second, "unchanged")
        self.assertEqual(run_id, second_run_id)
        self.assertEqual(current["runId"], run_id)
        self.assertEqual(snapshot["analytics"]["steps"]["daily"][0]["value"], 500)
        self.assertEqual(current_metadata(self.database)["counts"]["dailySteps"], 1)
        self.assertEqual(self.database["_analytics_runs"].count_documents({}), 1)

    def test_new_revision_queued_during_lease_is_not_lost(self):
        control = self.database
        enqueue_job(control, "user-a", delay_seconds=0)
        claimed = claim_job(control, "worker", lease_seconds=60)
        enqueue_job(control, "user-a", reason="sync", delay_seconds=0)
        complete_job(control, claimed, {"persistence": "saved"})
        job = control["analytics_jobs"].find_one({"_id": "user-a"})
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["requestedRevision"], 2)


if __name__ == "__main__":
    unittest.main()
