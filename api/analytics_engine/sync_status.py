"""Server-observed phone upload activity.

The phone does not currently open and close a durable sync session, so the
server reports activity from a rolling window after each authenticated upload.
"""

import datetime as dt


COLLECTION = "sync_status"
ACTIVE_WINDOW_SECONDS = 120


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def record_upload(control_db, user_id, record_type, record_count, now=None):
    now = now or utcnow()
    control_db[COLLECTION].update_one(
        {"_id": str(user_id)},
        {
            "$set": {
                "lastUploadAt": now,
                "activeUntil": now + dt.timedelta(seconds=ACTIVE_WINDOW_SECONDS),
                "lastRecordType": record_type,
                "lastRecordCount": record_count,
            },
            "$inc": {"totalUploadRequests": 1, "totalRecordsReceived": record_count},
            "$setOnInsert": {"firstObservedAt": now},
        },
        upsert=True,
    )


def status_response(document, now=None):
    now = now or utcnow()
    if not document:
        return {
            "observedActive": False,
            "state": "never_observed",
            "lastUploadAt": None,
            "activeUntil": None,
            "secondsSinceLastUpload": None,
            "lastRecordType": None,
            "lastRecordCount": None,
            "totalUploadRequests": 0,
            "totalRecordsReceived": 0,
            "activityWindowSeconds": ACTIVE_WINDOW_SECONDS,
            "note": "No phone upload has been observed since sync-status tracking was enabled.",
        }
    last_upload = _utc(document.get("lastUploadAt"))
    active_until = _utc(document.get("activeUntil"))
    active = bool(active_until and now <= active_until)
    elapsed = max(0, int((now - last_upload).total_seconds())) if last_upload else None
    return {
        "observedActive": active,
        "state": "receiving" if active else "idle",
        "lastUploadAt": last_upload.isoformat() if last_upload else None,
        "activeUntil": active_until.isoformat() if active_until else None,
        "secondsSinceLastUpload": elapsed,
        "lastRecordType": document.get("lastRecordType"),
        "lastRecordCount": document.get("lastRecordCount"),
        "totalUploadRequests": document.get("totalUploadRequests", 0),
        "totalRecordsReceived": document.get("totalRecordsReceived", 0),
        "activityWindowSeconds": ACTIVE_WINDOW_SECONDS,
        "note": "Active means the server received an authenticated phone upload within the rolling activity window; it is not a durable phone task state.",
    }
