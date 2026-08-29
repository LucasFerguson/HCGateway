import datetime as dt

from pymongo import ASCENDING, ReturnDocument


COLLECTION = "analytics_jobs"


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def ensure_job_indexes(control_db):
    control_db[COLLECTION].create_index([("status", ASCENDING), ("notBefore", ASCENDING), ("leaseUntil", ASCENDING)])


def enqueue_job(control_db, user_id, reason="sync", delay_seconds=15):
    ensure_job_indexes(control_db)
    now = utcnow()
    return control_db[COLLECTION].find_one_and_update(
        {"_id": str(user_id)},
        {
            "$inc": {"requestedRevision": 1},
            "$set": {
                "status": "queued",
                "reason": reason,
                "requestedAt": now,
                "notBefore": now + dt.timedelta(seconds=max(0, delay_seconds)),
                "attempts": 0,
            },
            "$unset": {
                "completedAt": "", "failedAt": "", "startedAt": "", "leaseUntil": "",
                "workerId": "", "error": "", "result": "",
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


def enqueue_missing_users(control_db, users):
    ensure_job_indexes(control_db)
    now = utcnow()
    for user in users:
        control_db[COLLECTION].update_one(
            {"_id": str(user["_id"])},
            {
                "$setOnInsert": {
                    "status": "queued",
                    "reason": "initial",
                    "requestedRevision": 1,
                    "requestedAt": now,
                    "notBefore": now,
                    "attempts": 0,
                }
            },
            upsert=True,
        )


def claim_job(control_db, worker_id, lease_seconds=300):
    now = utcnow()
    return control_db[COLLECTION].find_one_and_update(
        {
            "$or": [
                {"status": "queued", "notBefore": {"$lte": now}},
                {"status": "running", "leaseUntil": {"$lt": now}},
            ]
        },
        {
            "$set": {
                "status": "running",
                "workerId": worker_id,
                "startedAt": now,
                "leaseUntil": now + dt.timedelta(seconds=lease_seconds),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("requestedAt", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def complete_job(control_db, job, result):
    now = utcnow()
    revision = job.get("requestedRevision", 1)
    completed = control_db[COLLECTION].update_one(
        {
            "_id": job["_id"], "status": "running", "requestedRevision": revision,
            "workerId": job.get("workerId"),
        },
        {
            "$set": {"status": "completed", "completedAt": now, "result": result},
            "$unset": {"leaseUntil": "", "workerId": "", "error": ""},
        },
    )
    return completed.modified_count == 1


def fail_job(control_db, job, error, retry_delay_seconds=60):
    now = utcnow()
    failed = control_db[COLLECTION].update_one(
        {
            "_id": job["_id"], "status": "running",
            "requestedRevision": job.get("requestedRevision", 1),
            "workerId": job.get("workerId"),
        },
        {
            "$set": {
                "status": "queued" if job.get("attempts", 0) < 5 else "failed",
                "failedAt": now,
                "notBefore": now + dt.timedelta(seconds=retry_delay_seconds),
                "error": {"type": type(error).__name__, "message": str(error)[:1000]},
            },
            "$unset": {"leaseUntil": "", "workerId": ""},
        },
    )
    return failed.modified_count == 1
