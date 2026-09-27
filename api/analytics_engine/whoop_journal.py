"""Import WHOOP journal responses as tenant-scoped habit history."""

import argparse
import csv
import hashlib
import os
import re
import sys
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient, ReplaceOne


DEFINITIONS = "habitDefinitions"
ENTRIES = "habitEntries"
SOURCE = "whoop"
STORAGE_FORMAT = "plain-bson-v1"

CSV_FIELDS = {
    "Cycle start time",
    "Cycle end time",
    "Cycle timezone",
    "Question text",
    "Answered yes",
    "Notes",
}


def _stable_id(prefix, *parts):
    value = "\0".join(str(part) for part in parts)
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _question_id(question):
    normalized = " ".join(question.casefold().split())
    return _stable_id(SOURCE, "question", normalized)


def _parse_offset(value):
    match = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"unsupported Cycle timezone {value!r}")
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    if hours > 14 or minutes > 59:
        raise ValueError(f"invalid Cycle timezone {value!r}")
    return sign * (hours * 60 + minutes)


def _parse_local_instant(value, offset_minutes):
    local = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    offset_sign = "+" if offset_minutes >= 0 else "-"
    absolute_minutes = abs(offset_minutes)
    offset = f"{offset_sign}{absolute_minutes // 60:02d}:{absolute_minutes % 60:02d}"
    aware = datetime.fromisoformat(f"{local.isoformat()}{offset}")
    return aware.astimezone(timezone.utc), local


def parse_row(row, row_number):
    missing = CSV_FIELDS.difference(row)
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")

    question = (row["Question text"] or "").strip()
    if not question:
        raise ValueError(f"row {row_number} has no Question text")
    answer = (row["Answered yes"] or "").strip().lower()
    if answer not in {"true", "false"}:
        raise ValueError(f"row {row_number} has invalid Answered yes value {answer!r}")

    offset_label = (row["Cycle timezone"] or "").strip()
    offset_minutes = _parse_offset(offset_label)
    start_at, start_local = _parse_local_instant(row["Cycle start time"], offset_minutes)
    end_at, end_local = _parse_local_instant(row["Cycle end time"], offset_minutes)
    if end_at < start_at:
        raise ValueError(f"row {row_number} has a cycle ending before it starts")

    question_id = _question_id(question)
    entry_id = _stable_id(
        SOURCE,
        "entry",
        start_local.isoformat(),
        end_local.isoformat(),
        offset_label,
        question_id,
    )
    notes = (row["Notes"] or "").strip() or None
    return {
        "_id": entry_id,
        "id": entry_id,
        "source": SOURCE,
        "questionId": question_id,
        "question": question,
        "date": end_local.date().isoformat(),
        "cycleStartAt": start_at,
        "cycleEndAt": end_at,
        "cycleStartLocal": start_local.isoformat(timespec="seconds"),
        "cycleEndLocal": end_local.isoformat(timespec="seconds"),
        "sourceUtcOffsetMinutes": offset_minutes,
        "answeredYes": answer == "true",
        "notes": notes,
        "storageFormat": STORAGE_FORMAT,
    }


def import_csv(database, stream, source_export_date, imported_at=None):
    imported_at = imported_at or datetime.now(timezone.utc)
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        raise ValueError("WHOOP journal CSV has no header")
    missing = CSV_FIELDS.difference(reader.fieldnames)
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")

    parsed_entries = {}
    for row_number, row in enumerate(reader, start=2):
        entry = parse_row(row, row_number)
        entry["sourceExportDate"] = source_export_date
        entry["importedAt"] = imported_at
        # Collapse exact duplicate CSV rows before a bulk upsert. Two upserts
        # for the same previously absent _id in one unordered batch can race
        # into a duplicate-key error even though the import is logically
        # idempotent.
        parsed_entries[entry["_id"]] = entry
    operations = [
        ReplaceOne({"_id": entry["_id"]}, entry, upsert=True)
        for entry in parsed_entries.values()
    ]

    entries = database[ENTRIES]
    entries.create_index([("cycleEndAt", ASCENDING), ("questionId", ASCENDING)])
    entries.create_index([("date", ASCENDING), ("questionId", ASCENDING)])
    if operations:
        entries.bulk_write(operations, ordered=False)

    definitions = database[DEFINITIONS]
    definitions.create_index([("source", ASCENDING), ("question", ASCENDING)])
    definition_operations = []
    for summary in entries.aggregate([
        {"$match": {"source": SOURCE}},
        {"$group": {
            "_id": "$questionId",
            "question": {"$first": "$question"},
            "firstSeenDate": {"$min": "$date"},
            "lastSeenDate": {"$max": "$date"},
            "entryCount": {"$sum": 1},
        }},
    ]):
        definition = {
            "_id": summary["_id"],
            "id": summary["_id"],
            "source": SOURCE,
            "question": summary["question"],
            "firstSeenDate": summary["firstSeenDate"],
            "lastSeenDate": summary["lastSeenDate"],
            "entryCount": summary["entryCount"],
            "updatedAt": imported_at,
        }
        definition_operations.append(ReplaceOne({"_id": definition["_id"]}, definition, upsert=True))
    if definition_operations:
        definitions.bulk_write(definition_operations, ordered=False)

    return {
        "rows": len(operations),
        "definitions": len(definition_operations),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a WHOOP journal_entries.csv file from stdin")
    parser.add_argument("--username", required=True, help="HCGateway account username")
    parser.add_argument("--source-export-date", required=True, help="WHOOP export date (YYYY-MM-DD)")
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI"))
    args = parser.parse_args(argv)
    if not args.mongo_uri:
        parser.error("--mongo-uri or MONGO_URI is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.source_export_date):
        parser.error("--source-export-date must be YYYY-MM-DD")

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=10_000)
    try:
        user = client["hcgateway"]["users"].find_one({"username": args.username}, {"_id": 1})
        if not user:
            raise SystemExit(f"No HCGateway user found for username {args.username!r}")
        database = client[f"hcgateway_{user['_id']}"]
        result = import_csv(database, sys.stdin, args.source_export_date)
        print(f"Imported {result['rows']} WHOOP journal rows across {result['definitions']} questions.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
