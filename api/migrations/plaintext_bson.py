"""Resumable Fernet JSON to plaintext BSON migration.

This module deliberately does not run on import.  Operators must select exact
user databases and an explicit command.  The encrypted collections are copied
to shadows, validated, and retained under static backup names at cutover.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps as bson_dumps
from pymongo import DeleteOne, MongoClient, ReplaceOne

from analytics_engine.crypto import PLAIN_STORAGE_FORMAT, cipher_for_user, decode_stored_json
from analytics_engine.time_utils import parse_instant


CONTROL_DATABASE = "hcgateway"
USERS_COLLECTION = "users"
MIGRATIONS_COLLECTION = "plaintext_bson_migrations"
MAINTENANCE_COLLECTION = "maintenance"
MAINTENANCE_ID = "plaintext-bson-v1"
MIGRATION_VERSION = "plaintext-bson-v1"
DATABASE_PREFIX = "hcgateway_"
TEST_DATABASE_PREFIX = "hcgateway_test"
SHADOW_PREFIX = "__plain_bson_v1_shadow__"
BACKUP_PREFIX = "__fernet_backup_v1__"

PREPARED_PAYLOAD_COLLECTIONS = frozenset({
    "_analytics_snapshots",
    "_analytics_daily",
    "_analytics_sleep_events",
    "_analytics_device_comparisons",
    "_analytics_summaries",
})

SOURCE_KEYS = frozenset({
    "app", "provenance", "source", "dataOrigin", "device", "recordingMethod",
    "clientRecordId", "clientRecordVersion", "lastModifiedTime",
})
TIMESTAMP_KEYS = frozenset({
    "time", "start", "end", "startTime", "endTime", "observedAt", "startAt", "endAt",
    "localStartAt", "localEndAt", "lastModifiedTime", "generatedAt", "processedAt",
    "startedAt", "completedAt", "failedAt", "requestedAt",
})


class MigrationError(RuntimeError):
    """An operator-actionable migration safety failure."""


def shadow_name(collection_name: str) -> str:
    return SHADOW_PREFIX + collection_name


def backup_name(collection_name: str) -> str:
    return BACKUP_PREFIX + collection_name


def canonical_bytes(value) -> bytes:
    return bson_dumps(
        value,
        json_options=CANONICAL_JSON_OPTIONS,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_values(values) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _utc_datetime(value):
    # PyMongo's default CodecOptions round BSON UTC datetimes back to naive UTC.
    return parse_instant(value).astimezone(dt.timezone.utc).replace(tzinfo=None)


def is_raw_collection(collection_name: str) -> bool:
    return not collection_name.startswith("_")


def convert_document(document: dict, cipher, raw_collection: bool) -> dict:
    """Return the exact plaintext BSON representation used at cutover."""
    converted = deepcopy(document)
    converted["data"] = decode_stored_json(cipher, document["data"])
    converted["storageFormat"] = PLAIN_STORAGE_FORMAT
    if raw_collection:
        if not isinstance(converted.get("start"), str):
            raise MigrationError("raw source document is missing its original start timestamp string")
        converted["startInstant"] = _utc_datetime(converted["start"])
        end = converted.get("end")
        converted["endInstant"] = _utc_datetime(end) if isinstance(end, str) else None
    return converted


def _walk(value, path=()):
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            child_path = path + (str(key),)
            yield child_path, key, child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + (str(index),))


def _path_bytes(path, value):
    return canonical_bytes({"path": list(path), "value": value})


def _document_facts(document: dict) -> dict:
    sample_count = 0
    stage_count = 0
    provenance = hashlib.sha256()
    raw_timestamps = hashlib.sha256()
    parsed_timestamps = hashlib.sha256()
    invalid_timestamps = hashlib.sha256()
    provenance_count = raw_timestamp_count = parsed_timestamp_count = invalid_timestamp_count = 0

    def add(digest, value):
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)

    for path, key, value in _walk(document):
        if key == "samples" and isinstance(value, list):
            sample_count += len(value)
        elif key == "stages" and isinstance(value, list):
            stage_count += len(value)
        if key in SOURCE_KEYS:
            add(provenance, _path_bytes(path, value))
            provenance_count += 1
        if key in TIMESTAMP_KEYS and isinstance(value, str):
            add(raw_timestamps, _path_bytes(path, value))
            raw_timestamp_count += 1
            try:
                parsed = _utc_datetime(value)
            except (TypeError, ValueError) as error:
                add(invalid_timestamps, _path_bytes(path, type(error).__name__))
                invalid_timestamp_count += 1
            else:
                add(parsed_timestamps, _path_bytes(path, parsed))
                parsed_timestamp_count += 1
    return {
        "sampleCount": sample_count,
        "stageCount": stage_count,
        "provenanceCount": provenance_count,
        "provenanceDigest": provenance.digest(),
        "rawTimestampCount": raw_timestamp_count,
        "rawTimestampDigest": raw_timestamps.digest(),
        "parsedTimestampCount": parsed_timestamp_count,
        "parsedTimestampDigest": parsed_timestamps.digest(),
        "invalidTimestampCount": invalid_timestamp_count,
        "invalidTimestampDigest": invalid_timestamps.digest(),
    }


def collection_manifest(collection, cipher, logical_name: str, require_plain: bool) -> dict:
    """Build a value-free, exact manifest for one source or shadow collection."""
    raw = is_raw_collection(logical_name)
    mongo_ids = []
    logical_ids = []
    contents = []
    provenance = []
    raw_timestamps = []
    parsed_timestamps = []
    invalid_timestamps = []
    provenance_count = raw_timestamp_count = parsed_timestamp_count = invalid_timestamp_count = 0
    sample_count = stage_count = 0
    storage_violations = 0
    instant_mismatches = 0

    for original in collection.find({}):
        if require_plain:
            if original.get("storageFormat") != PLAIN_STORAGE_FORMAT or isinstance(original.get("data"), str):
                storage_violations += 1
            converted = deepcopy(original)
        else:
            converted = convert_document(original, cipher, raw)

        mongo_ids.append(canonical_bytes(converted.get("_id")))
        logical_ids.append(canonical_bytes(converted.get("id")))
        contents.append(hashlib.sha256(canonical_bytes(converted)).digest())
        facts = _document_facts(converted)
        sample_count += facts["sampleCount"]
        stage_count += facts["stageCount"]
        provenance_count += facts["provenanceCount"]
        provenance.append(facts["provenanceDigest"])
        raw_timestamp_count += facts["rawTimestampCount"]
        raw_timestamps.append(facts["rawTimestampDigest"])
        parsed_timestamp_count += facts["parsedTimestampCount"]
        parsed_timestamps.append(facts["parsedTimestampDigest"])
        invalid_timestamp_count += facts["invalidTimestampCount"]
        invalid_timestamps.append(facts["invalidTimestampDigest"])

        if raw:
            try:
                expected_start = _utc_datetime(converted.get("start"))
                expected_end = _utc_datetime(converted["end"]) if isinstance(converted.get("end"), str) else None
            except (TypeError, ValueError):
                instant_mismatches += 1
            else:
                if converted.get("startInstant") != expected_start or converted.get("endInstant") != expected_end:
                    instant_mismatches += 1

    id_counts = Counter(mongo_ids)
    logical_id_counts = Counter(logical_ids)
    return {
        "collection": logical_name,
        "documentCount": len(mongo_ids),
        "mongoIdCount": len(id_counts),
        "mongoIdDuplicateCount": sum(count - 1 for count in id_counts.values()),
        "mongoIdSetSha256": sha256_values(id_counts),
        "logicalIdCount": len(logical_id_counts),
        "logicalIdDuplicateCount": sum(count - 1 for count in logical_id_counts.values()),
        "logicalIdMultisetSha256": sha256_values(
            canonical_bytes({"idSha256": hashlib.sha256(key).hexdigest(), "count": count})
            for key, count in logical_id_counts.items()
        ),
        "canonicalContentSha256": sha256_values(contents),
        "nestedSampleCount": sample_count,
        "nestedStageCount": stage_count,
        "sourceProvenanceValueCount": provenance_count,
        "sourceProvenanceSha256": sha256_values(provenance),
        "rawTimestampTextCount": raw_timestamp_count,
        "rawTimestampTextSha256": sha256_values(raw_timestamps),
        "parsedInstantCount": parsed_timestamp_count,
        "parsedInstantSha256": sha256_values(parsed_timestamps),
        "invalidTimestampCount": invalid_timestamp_count,
        "invalidTimestampSha256": sha256_values(invalid_timestamps),
        "storageViolationCount": storage_violations,
        "envelopeInstantMismatchCount": instant_mismatches,
    }


def discover_payload_collections(database) -> list[str]:
    """Return only source/prepared payload collections, never operational copies."""
    selected = []
    for name in sorted(database.list_collection_names()):
        if name.startswith((SHADOW_PREFIX, BACKUP_PREFIX)):
            continue
        if name in PREPARED_PAYLOAD_COLLECTIONS:
            selected.append(name)
            continue
        if name.startswith("_"):
            continue
        sample = database[name].find_one({}, {"data": 1})
        if sample is not None and "data" in sample:
            selected.append(name)
    return selected


def database_manifest(database, cipher, require_shadows: bool = False) -> dict:
    names = discover_payload_collections(database)
    manifests = []
    for name in names:
        target = database[shadow_name(name)] if require_shadows else database[name]
        if require_shadows and shadow_name(name) not in database.list_collection_names():
            raise MigrationError(f"missing shadow collection for {name}")
        manifests.append(collection_manifest(target, cipher, name, require_plain=require_shadows))
    return {
        "storageFormat": PLAIN_STORAGE_FORMAT,
        "database": database.name,
        "collectionCount": len(names),
        "collectionSetSha256": sha256_values(canonical_bytes(name) for name in names),
        "collections": manifests,
    }


def compare_manifests(source: dict, shadow: dict) -> list[str]:
    ignored = {"storageViolationCount"}
    errors = []
    for key in ("database", "collectionCount", "collectionSetSha256"):
        if source.get(key) != shadow.get(key):
            errors.append(f"database manifest mismatch: {key}")
    source_collections = {item["collection"]: item for item in source["collections"]}
    shadow_collections = {item["collection"]: item for item in shadow["collections"]}
    if set(source_collections) != set(shadow_collections):
        errors.append("collection name sets differ")
    for name in sorted(set(source_collections) | set(shadow_collections)):
        if name not in source_collections or name not in shadow_collections:
            continue
        for key, expected in source_collections[name].items():
            if key in ignored:
                continue
            if shadow_collections[name].get(key) != expected:
                errors.append(f"{name}: {key} differs")
        if shadow_collections[name]["storageViolationCount"]:
            errors.append(f"{name}: shadow contains non-{PLAIN_STORAGE_FORMAT} documents")
        if shadow_collections[name]["envelopeInstantMismatchCount"]:
            errors.append(f"{name}: BSON envelope instants differ from source timestamp text")
    return errors


def _copy_indexes(source, shadow):
    existing = set(shadow.index_information())
    for index in source.list_indexes():
        name = index["name"]
        if name == "_id_" or name in existing:
            continue
        options = {
            key: index[key]
            for key in (
                "unique", "sparse", "expireAfterSeconds", "partialFilterExpression",
                "collation", "hidden",
            )
            if key in index
        }
        shadow.create_index(list(index["key"].items()), name=name, **options)


def copy_to_shadows(database, cipher, batch_size: int = 1000) -> dict:
    copied = {}
    for name in discover_payload_collections(database):
        if backup_name(name) in database.list_collection_names():
            raise MigrationError(f"{name}: backup exists; resume cutover or rollback instead of copying")
        source = database[name]
        shadow_collection_name = shadow_name(name)
        if shadow_collection_name not in database.list_collection_names():
            database.create_collection(shadow_collection_name)
        shadow = database[shadow_collection_name]
        operations = []
        source_ids = set()
        count = 0
        for document in source.find({}):
            source_ids.add(canonical_bytes(document["_id"]))
            operations.append(ReplaceOne(
                {"_id": document["_id"]}, convert_document(document, cipher, is_raw_collection(name)), upsert=True
            ))
            if len(operations) >= batch_size:
                shadow.bulk_write(operations, ordered=False)
                count += len(operations)
                operations = []
        if operations:
            shadow.bulk_write(operations, ordered=False)
            count += len(operations)

        stale = []
        for item in shadow.find({}, {"_id": 1}):
            if canonical_bytes(item["_id"]) not in source_ids:
                stale.append(DeleteOne({"_id": item["_id"]}))
                if len(stale) >= batch_size:
                    shadow.bulk_write(stale, ordered=False)
                    stale = []
        if stale:
            shadow.bulk_write(stale, ordered=False)
        _copy_indexes(source, shadow)
        copied[name] = count
    return copied


def validation_report(database, cipher) -> dict:
    source = database_manifest(database, cipher, require_shadows=False)
    shadow = database_manifest(database, cipher, require_shadows=True)
    errors = compare_manifests(source, shadow)
    return {"valid": not errors, "errors": errors, "source": source, "shadow": shadow}


def _collection_storage_format(collection) -> str:
    sample = collection.find_one({}, {"data": 1, "storageFormat": 1})
    if sample is None:
        return "empty"
    if sample.get("storageFormat") == PLAIN_STORAGE_FORMAT and not isinstance(sample.get("data"), str):
        return PLAIN_STORAGE_FORMAT
    return "legacy-fernet-json"


def _validate_collection_pair(source, shadow, cipher, logical_name):
    source_manifest = collection_manifest(source, cipher, logical_name, require_plain=False)
    shadow_manifest = collection_manifest(shadow, cipher, logical_name, require_plain=True)
    errors = compare_manifests(
        {
            "database": database_name(source),
            "collectionCount": 1,
            "collectionSetSha256": sha256_values([canonical_bytes(logical_name)]),
            "collections": [source_manifest],
        },
        {
            "database": database_name(shadow),
            "collectionCount": 1,
            "collectionSetSha256": sha256_values([canonical_bytes(logical_name)]),
            "collections": [shadow_manifest],
        },
    )
    if errors:
        raise MigrationError(f"{logical_name}: validation failed: {errors}")


def database_name(collection):
    return collection.database.name


def _check_collection_pair(source, shadow, cipher, logical_name, fast):
    """Prove a shadow is safe to promote. Fast mode trusts an exact count match;
    full mode re-derives and compares the value-free manifest (much slower on
    large nested collections such as heartRate)."""
    if fast:
        source_count = source.count_documents({})
        shadow_count = shadow.count_documents({})
        if source_count != shadow_count:
            raise MigrationError(
                f"{logical_name}: fast cutover count mismatch: source={source_count} shadow={shadow_count}"
            )
        return
    _validate_collection_pair(source, shadow, cipher, logical_name)


def cutover_database(database, cipher, fast=False):
    """Atomically rename each collection; safe to resume between collections."""
    names = discover_payload_collections(database)
    # A partially completed cutover hides completed logical names from discovery.
    for physical in database.list_collection_names():
        if physical.startswith(BACKUP_PREFIX):
            names.append(physical[len(BACKUP_PREFIX):])
    for name in sorted(set(names)):
        available = set(database.list_collection_names())
        shadow = shadow_name(name)
        backup = backup_name(name)
        if backup in available and name in available and shadow not in available:
            _check_collection_pair(database[backup], database[name], cipher, name, fast)
            continue
        if backup in available and name not in available and shadow in available:
            _check_collection_pair(database[backup], database[shadow], cipher, name, fast)
            database[shadow].rename(name, dropTarget=False)
            continue
        if backup in available:
            raise MigrationError(f"{name}: ambiguous cutover state; encrypted backup already exists")
        if name not in available or shadow not in available:
            raise MigrationError(f"{name}: source or validated shadow is missing")
        _check_collection_pair(database[name], database[shadow], cipher, name, fast)
        database[name].rename(backup, dropTarget=False)
        database[shadow].rename(name, dropTarget=False)


def rollback_database(database):
    """Restore every encrypted backup while retaining the plaintext shadow."""
    backups = sorted(name for name in database.list_collection_names() if name.startswith(BACKUP_PREFIX))
    for backup in backups:
        name = backup[len(BACKUP_PREFIX):]
        shadow = shadow_name(name)
        available = set(database.list_collection_names())
        if name in available:
            if shadow in available:
                raise MigrationError(f"{name}: rollback shadow already exists")
            database[name].rename(shadow, dropTarget=False)
        database[backup].rename(name, dropTarget=False)


def _assert_exact_name(value: str, label: str):
    if not value or any(character in value for character in "*?[]"):
        raise MigrationError(f"{label} must be an exact name without wildcards")


def resolve_targets(client, user_ids, database_names, all_production_users=False,
                    confirm_all=False, include_test_databases=False):
    users = client[CONTROL_DATABASE][USERS_COLLECTION]
    pairs = set()
    if all_production_users:
        if not confirm_all:
            raise MigrationError("--all-production-users requires --confirm-all-production-users")
        for user in users.find({}, {"_id": 1}):
            user_id = str(user["_id"])
            database_name = DATABASE_PREFIX + user_id
            if not database_name.startswith(TEST_DATABASE_PREFIX):
                pairs.add((user_id, database_name))
    for user_id in user_ids or []:
        _assert_exact_name(user_id, "user id")
        pairs.add((user_id, DATABASE_PREFIX + user_id))
    for database_name in database_names or []:
        _assert_exact_name(database_name, "database")
        if not database_name.startswith(DATABASE_PREFIX) or database_name == CONTROL_DATABASE:
            raise MigrationError(f"database must use the exact {DATABASE_PREFIX}<user-id> form")
        pairs.add((database_name[len(DATABASE_PREFIX):], database_name))
    if not pairs:
        raise MigrationError("select --user-id, --database, or explicitly confirmed production users")

    available_databases = set(client.list_database_names())
    resolved = []
    for user_id, database_name in sorted(pairs):
        if database_name.startswith(TEST_DATABASE_PREFIX) and not include_test_databases:
            raise MigrationError(
                f"refusing test database {database_name}; exact selection also requires --include-test-databases"
            )
        if database_name not in available_databases:
            raise MigrationError(f"selected database does not exist: {database_name}")
        user = users.find_one({"_id": user_id})
        if not user:
            raise MigrationError(f"no exact control user exists for database {database_name}")
        resolved.append((user, client[database_name]))
    return resolved


def set_maintenance(control_database, active: bool, migration_id: str, database_names: list[str]):
    now = dt.datetime.now(dt.timezone.utc)
    if active:
        control_database[MAINTENANCE_COLLECTION].update_one(
            {"_id": MAINTENANCE_ID},
            {"$set": {
                "active": True,
                "storageFormat": PLAIN_STORAGE_FORMAT,
                "migrationId": migration_id,
                "databases": sorted(database_names),
                "startedAt": now,
            }},
            upsert=True,
        )
    else:
        control_database[MAINTENANCE_COLLECTION].update_one(
            {"_id": MAINTENANCE_ID, "migrationId": migration_id},
            {"$set": {"active": False, "endedAt": now}},
        )


def assert_maintenance(control_database, migration_id: str, database_names: list[str]):
    marker = control_database[MAINTENANCE_COLLECTION].find_one({"_id": MAINTENANCE_ID})
    if not marker or not marker.get("active"):
        raise MigrationError("maintenance mode is not active")
    if marker.get("migrationId") != migration_id:
        raise MigrationError("maintenance migration id does not match")
    if marker.get("databases") != sorted(database_names):
        raise MigrationError("maintenance database selection does not match")


def write_manifest(path: str | None, value: dict):
    rendered = json.dumps(value, sort_keys=True, indent=2, default=str)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "audit", "copy", "validate", "maintenance-on", "cutover", "rollback", "maintenance-off", "status"
    ))
    parser.add_argument("--mongo-uri", default=os.environ.get("MONGO_URI"))
    parser.add_argument("--user-id", action="append", default=[])
    parser.add_argument("--database", action="append", default=[])
    parser.add_argument("--all-production-users", action="store_true")
    parser.add_argument("--confirm-all-production-users", action="store_true")
    parser.add_argument("--include-test-databases", action="store_true")
    parser.add_argument("--migration-id", default=MIGRATION_VERSION)
    parser.add_argument("--manifest")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--acknowledge-services-stopped", action="store_true")
    parser.add_argument(
        "--fast", action="store_true",
        help="cutover only: trust an exact document-count match instead of re-hashing "
             "every document's nested values; use only after a separate full validation "
             "(e.g. the validate command, or an equivalent count audit) has already passed",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.mongo_uri:
        raise MigrationError("MONGO_URI or --mongo-uri is required")
    if args.batch_size < 1:
        raise MigrationError("--batch-size must be positive")
    client = MongoClient(args.mongo_uri)
    try:
        targets = resolve_targets(
            client, args.user_id, args.database, args.all_production_users,
            args.confirm_all_production_users, args.include_test_databases,
        )
        database_names = [database.name for _, database in targets]
        control = client[CONTROL_DATABASE]
        output = {"command": args.command, "storageFormat": PLAIN_STORAGE_FORMAT, "databases": {}}

        if args.command == "maintenance-on":
            set_maintenance(control, True, args.migration_id, database_names)
        elif args.command == "maintenance-off":
            set_maintenance(control, False, args.migration_id, database_names)
        else:
            if args.command in ("cutover", "rollback"):
                if not args.acknowledge_services_stopped:
                    raise MigrationError("cutover/rollback requires --acknowledge-services-stopped")
                assert_maintenance(control, args.migration_id, database_names)
            for user, database in targets:
                cipher = cipher_for_user(user)
                if args.command == "audit":
                    output["databases"][database.name] = database_manifest(database, cipher)
                elif args.command == "copy":
                    output["databases"][database.name] = copy_to_shadows(database, cipher, args.batch_size)
                elif args.command == "validate":
                    output["databases"][database.name] = validation_report(database, cipher)
                elif args.command == "cutover":
                    cutover_database(database, cipher, fast=args.fast)
                    output["databases"][database.name] = {"cutover": True, "fast": args.fast}
                elif args.command == "rollback":
                    rollback_database(database)
                    output["databases"][database.name] = {"rolledBack": True}
                elif args.command == "status":
                    output["databases"][database.name] = {
                        "collections": sorted(database.list_collection_names()),
                    }
        if args.command in ("validate",) and not all(
            result["valid"] for result in output["databases"].values()
        ):
            write_manifest(args.manifest, output)
            return 2
        write_manifest(args.manifest, output)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
