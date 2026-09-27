import io
import os
import unittest
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient

from analytics_engine.whoop_journal import DEFINITIONS, ENTRIES, import_csv, parse_row


CSV = """Cycle start time,Cycle end time,Cycle timezone,Question text,Answered yes,Notes
2026-01-01 23:00:00,2026-01-02 07:00:00,UTC-06:00,Consumed caffeine?,true,afternoon coffee
2026-01-01 23:00:00,2026-01-02 07:00:00,UTC-06:00,Experienced a headache?,false,
2026-01-02 23:30:00,2026-01-03 07:30:00,UTC-06:00,Consumed caffeine?,false,
"""


class WhoopJournalParsingTests(unittest.TestCase):
    def test_preserves_local_cycle_and_converts_offset_to_utc(self):
        row = {
            "Cycle start time": "2026-04-07 22:00:00",
            "Cycle end time": "2026-04-08 06:30:00",
            "Cycle timezone": "UTC-05:00",
            "Question text": "Consumed caffeine?",
            "Answered yes": "true",
            "Notes": "",
        }
        parsed = parse_row(row, 2)
        self.assertEqual(parsed["date"], "2026-04-08")
        self.assertEqual(parsed["cycleEndAt"], datetime(2026, 4, 8, 11, 30, tzinfo=timezone.utc))
        self.assertEqual(parsed["cycleEndLocal"], "2026-04-08T06:30:00")
        self.assertEqual(parsed["sourceUtcOffsetMinutes"], -300)
        self.assertTrue(parsed["answeredYes"])
        self.assertIsNone(parsed["notes"])

    def test_rejects_non_boolean_answers(self):
        row = {
            "Cycle start time": "2026-01-01 00:00:00",
            "Cycle end time": "2026-01-01 01:00:00",
            "Cycle timezone": "UTC-06:00",
            "Question text": "Question?",
            "Answered yes": "sometimes",
            "Notes": "",
        }
        with self.assertRaisesRegex(ValueError, "invalid Answered yes"):
            parse_row(row, 2)


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class WhoopJournalMongoTests(unittest.TestCase):
    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.database_name = "hcgateway_test_whoop_journal_" + uuid.uuid4().hex
        self.database = self.mongo[self.database_name]

    def tearDown(self):
        if self.database_name.startswith("hcgateway_test_whoop_journal_"):
            self.mongo.drop_database(self.database_name)
        self.mongo.close()

    def test_import_is_idempotent_and_builds_question_definitions(self):
        imported_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
        first = import_csv(self.database, io.StringIO(CSV), "2026-08-24", imported_at)
        second = import_csv(self.database, io.StringIO(CSV), "2026-08-24", imported_at)

        self.assertEqual(first, {"rows": 3, "definitions": 2})
        self.assertEqual(second, first)
        self.assertEqual(self.database[ENTRIES].count_documents({}), 3)
        self.assertEqual(self.database[DEFINITIONS].count_documents({}), 2)

        caffeine = self.database[DEFINITIONS].find_one({"question": "Consumed caffeine?"})
        self.assertEqual(caffeine["entryCount"], 2)
        self.assertEqual(caffeine["firstSeenDate"], "2026-01-02")
        self.assertEqual(caffeine["lastSeenDate"], "2026-01-03")
        response = self.database[ENTRIES].find_one({"notes": "afternoon coffee"})
        self.assertEqual(response["questionId"], caffeine["id"])
        self.assertEqual(response["storageFormat"], "plain-bson-v1")
        self.assertIn("cycleEndAt_1_questionId_1", self.database[ENTRIES].index_information())


if __name__ == "__main__":
    unittest.main()
