import copy
import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient

from analytics_engine.crypto import PLAIN_STORAGE_FORMAT, cipher_for_user, encrypt_json
from migrations.plaintext_bson import (
    BACKUP_PREFIX,
    SHADOW_PREFIX,
    MigrationError,
    backup_name,
    collection_manifest,
    compare_manifests,
    convert_document,
    copy_to_shadows,
    cutover_database,
    database_manifest,
    discover_payload_collections,
    resolve_targets,
    rollback_database,
    shadow_name,
    validation_report,
)


class _NamedDatabase:
    name = "hcgateway_test"


class _MemoryCollection:
    database = _NamedDatabase()

    def __init__(self, documents):
        self.documents = copy.deepcopy(documents)

    def find(self, *_args, **_kwargs):
        return copy.deepcopy(self.documents)


class PlaintextBsonMigrationUnitTests(unittest.TestCase):
    def setUp(self):
        self.user = {"_id": "user-a", "password": "$argon2id$test-hash"}
        self.cipher = cipher_for_user(self.user)
        self.payload = {
            "samples": [
                {"time": "2026-01-02T05:15:00Z", "beatsPerMinute": 61},
                {"time": "2026-01-02T05:15:05+00:00", "beatsPerMinute": 62},
            ],
            "stages": [{
                "startTime": "2026-01-01T23:00:00-06:00",
                "endTime": "2026-01-02T00:00:00-06:00",
                "stage": 4,
            }],
        }
        self.legacy = {
            "_id": "record-1",
            "id": "record-1",
            "data": encrypt_json(self.cipher, self.payload),
            "app": "test.watch",
            "provenance": {
                "dataOrigin": "test.watch",
                "device": {"manufacturer": "Test", "model": "One", "type": 1},
                "recordingMethod": 2,
            },
            "start": "2026-01-01T23:00:00-06:00",
            "end": "2026-01-02T01:00:00-06:00",
        }

    def test_conversion_preserves_source_text_and_adds_plain_bson_instants(self):
        converted = convert_document(self.legacy, self.cipher, raw_collection=True)
        self.assertEqual(converted["storageFormat"], PLAIN_STORAGE_FORMAT)
        self.assertEqual(converted["data"], self.payload)
        self.assertEqual(converted["start"], self.legacy["start"])
        self.assertEqual(converted["end"], self.legacy["end"])
        self.assertEqual(converted["startInstant"], dt.datetime(2026, 1, 2, 5, 0))
        self.assertEqual(converted["endInstant"], dt.datetime(2026, 1, 2, 7, 0))
        self.assertIsInstance(self.legacy["data"], str)

    def test_manifest_proves_content_ids_nested_values_provenance_and_instants(self):
        converted = convert_document(self.legacy, self.cipher, raw_collection=True)
        source = collection_manifest(
            _MemoryCollection([self.legacy]), self.cipher, "heartRate", require_plain=False
        )
        shadow = collection_manifest(
            _MemoryCollection([converted]), self.cipher, "heartRate", require_plain=True
        )
        wrapper = lambda item: {
            "database": "hcgateway_test",
            "collectionCount": 1,
            "collectionSetSha256": "same",
            "collections": [item],
        }
        self.assertEqual(compare_manifests(wrapper(source), wrapper(shadow)), [])
        self.assertEqual(source["nestedSampleCount"], 2)
        self.assertEqual(source["nestedStageCount"], 1)
        self.assertEqual(source["mongoIdDuplicateCount"], 0)
        self.assertEqual(source["logicalIdDuplicateCount"], 0)
        self.assertGreater(source["sourceProvenanceValueCount"], 0)
        self.assertEqual(source["invalidTimestampCount"], 0)
        self.assertEqual(source["envelopeInstantMismatchCount"], 0)

    def test_manifest_detects_timezone_shift_and_plaintext_format_violation(self):
        converted = convert_document(self.legacy, self.cipher, raw_collection=True)
        converted["startInstant"] = converted["startInstant"] + dt.timedelta(hours=1)
        converted["storageFormat"] = "wrong"
        manifest = collection_manifest(
            _MemoryCollection([converted]), self.cipher, "heartRate", require_plain=True
        )
        self.assertEqual(manifest["storageViolationCount"], 1)
        self.assertEqual(manifest["envelopeInstantMismatchCount"], 1)

    def test_names_are_static_hidden_prefixes(self):
        self.assertEqual(shadow_name("heartRate"), SHADOW_PREFIX + "heartRate")
        self.assertEqual(backup_name("heartRate"), BACKUP_PREFIX + "heartRate")
        self.assertTrue(shadow_name("heartRate").startswith("_"))
        self.assertTrue(backup_name("heartRate").startswith("_"))


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class PlaintextBsonMigrationMongoTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.user_id = "test-migrate-" + uuid.uuid4().hex[:16]
        self.database_name = "hcgateway_" + self.user_id
        self.database = self.mongo[self.database_name]
        self.user = {"_id": self.user_id, "password": "$argon2id$test-hash"}
        self.cipher = cipher_for_user(self.user)
        self.mongo["hcgateway"]["users"].insert_one(self.user)
        raw = {
            "_id": "raw-1", "id": "raw-1", "app": "test.watch",
            "provenance": {"dataOrigin": "test.watch", "recordingMethod": 2},
            "start": "2026-01-01T23:00:00-06:00",
            "end": "2026-01-02T01:00:00-06:00",
            "data": encrypt_json(self.cipher, {
                "samples": [{"time": "2026-01-02T05:30:00Z", "beatsPerMinute": 60}],
            }),
        }
        self.database["heartRate"].insert_one(raw)
        self.database["heartRate"].create_index([("start", 1), ("app", 1)])
        self.database["_analytics_daily"].insert_one({
            "_id": "day-1", "runId": "run", "date": "2026-01-02",
            "data": encrypt_json(self.cipher, {"date": "2026-01-02", "steps": None}),
        })
        self.database["_analytics_runs"].insert_one({"_id": "run", "status": "completed"})

    def tearDown(self):
        exact_database = "hcgateway_" + self.user_id
        if self.database_name == exact_database and self.database_name.startswith("hcgateway_test-migrate-"):
            self.mongo.drop_database(self.database_name)
        self.mongo["hcgateway"]["users"].delete_one({"_id": self.user_id})
        self.mongo.close()

    def test_copy_validate_interrupted_cutover_resume_and_rollback(self):
        self.assertEqual(discover_payload_collections(self.database), ["_analytics_daily", "heartRate"])
        before = database_manifest(self.database, self.cipher)
        copied = copy_to_shadows(self.database, self.cipher, batch_size=1)
        self.assertEqual(copied, {"_analytics_daily": 1, "heartRate": 1})
        report = validation_report(self.database, self.cipher)
        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(before["collectionSetSha256"], report["shadow"]["collectionSetSha256"])

        # Simulate a process interruption between the two renames.
        self.database["heartRate"].rename(backup_name("heartRate"), dropTarget=False)
        cutover_database(self.database, self.cipher)
        cutover_database(self.database, self.cipher)  # fully idempotent resume
        live = self.database["heartRate"].find_one({"_id": "raw-1"})
        self.assertEqual(live["storageFormat"], PLAIN_STORAGE_FORMAT)
        self.assertIsInstance(live["data"], dict)
        self.assertEqual(live["start"], "2026-01-01T23:00:00-06:00")
        self.assertEqual(live["startInstant"], dt.datetime(2026, 1, 2, 5, 0))
        self.assertIn(backup_name("heartRate"), self.database.list_collection_names())

        rollback_database(self.database)
        rollback_database(self.database)  # idempotent after backups are restored
        restored = self.database["heartRate"].find_one({"_id": "raw-1"})
        self.assertIsInstance(restored["data"], str)
        self.assertIn(shadow_name("heartRate"), self.database.list_collection_names())

    def test_selection_rejects_test_database_without_double_opt_in(self):
        with self.assertRaises(MigrationError):
            resolve_targets(
                self.mongo, [], [self.database_name], include_test_databases=False
            )
        targets = resolve_targets(
            self.mongo, [], [self.database_name], include_test_databases=True
        )
        self.assertEqual([(str(user["_id"]), db.name) for user, db in targets], [
            (self.user_id, self.database_name)
        ])


if __name__ == "__main__":
    unittest.main()
