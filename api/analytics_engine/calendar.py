"""FluidCalendar delivery primitives for prepared sleep events.

Health-data interpretation belongs to :mod:`analytics_engine.sleep`.  This
module only renders prepared values, talks to FluidCalendar, and records the
minimum durable state needed for safe retries and incremental backfills.
"""

import datetime as dt
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

import requests
from pymongo import ASCENDING, ReturnDocument

from .time_utils import utc_iso


DELIVERIES = "_calendar_sleep_deliveries"
BACKFILLS = "_calendar_sleep_backfills"
DISPLAY_STAGES = (
    ("light", "Light"),
    ("deep", "Deep"),
    ("rem", "REM"),
    ("asleep", "Asleep (unclassified)"),
    ("awake", "Awake"),
    ("unknown", "Unknown"),
)
PERMANENT_HTTP_STATUSES = frozenset((400, 401, 403, 404))
RETRYABLE_HTTP_STATUSES = frozenset((408, 425, 429))


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def _whole_minutes(value):
    return max(0, int(Decimal(str(value or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def format_duration(minutes):
    """Render a prepared minute count deterministically for a human."""
    whole = _whole_minutes(minutes)
    hours, remainder = divmod(whole, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _stage_rows(event):
    values = event.get("stageMinutes") or {}
    return [
        (key, label, format_duration(values[key]))
        for key, label in DISPLAY_STAGES
        if _whole_minutes(values.get(key)) > 0
    ]


def _stage_summary(event):
    status = event.get("stageDataStatus")
    rows = _stage_rows(event)
    if status == "missing":
        return "stages unavailable"
    if status == "invalid":
        return "stage data limited"
    sleep_rows = [(label, value) for key, label, value in rows if key in {"light", "deep", "rem", "asleep"}]
    if not sleep_rows:
        return "stage data limited"
    return " · ".join(f"{label} {value}" for label, value in sleep_rows)


def render_sleep_event(event, feed_id, title_prefix="Sleep"):
    """Build the stable FluidCalendar representation of one prepared event."""
    primary = event["primary"]
    sleep_duration = format_duration(event.get("sleepMinutes"))
    title = f"{title_prefix} · {sleep_duration} · {_stage_summary(event)}"
    status = event.get("stageDataStatus") or "unknown"
    status_label = {
        "missing": "unavailable (total sleep uses the recorded window)",
        "invalid": "limited (source stage timeline did not pass validation)",
    }.get(status, status)
    role = "Main sleep" if event.get("role") == "main" else "Supplemental sleep"
    lines = [
        f"Actual sleep: {sleep_duration}",
        f"Recorded window: {format_duration(event.get('windowMinutes'))}",
    ]
    rows = _stage_rows(event)
    if rows:
        lines.append("Stages recorded:")
        lines.extend(f"- {label}: {value}" for _, label, value in rows)
    else:
        lines.append("Stages recorded: unavailable")
    lines.extend((
        f"Stage data: {status_label}",
        f"Session: {role}",
        f"Source: {primary.get('source') or 'unknown'}",
        f"Recordings reconciled: {int(event.get('recordingCount') or 1)}",
        f"Local time: {event.get('localStartAt')} – {event.get('localEndAt')} ({event.get('timeZone')})",
    ))
    flags = sorted(set(event.get("qualityFlags") or []))
    if flags:
        lines.append("Quality notes: " + ", ".join(flag.replace("_", " ") for flag in flags))
    lines.append("Imported by HCGateway.")
    return {
        "feedId": str(feed_id),
        "title": title,
        "start": utc_iso(primary["startAt"]),
        "end": utc_iso(primary["endAt"]),
        "description": "\n".join(lines),
        "allDay": False,
        "skipIfExists": True,
    }


def payload_hash(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FluidCalendarError(RuntimeError):
    def __init__(self, message, *, retryable, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def is_retryable_status(status_code):
    if status_code in RETRYABLE_HTTP_STATUSES or status_code >= 500:
        return True
    if status_code in PERMANENT_HTTP_STATUSES or 400 <= status_code < 500:
        return False
    return False


class FluidCalendarClient:
    """Small synchronous client; credentials are never included in errors."""

    def __init__(self, base_url, api_key, *, timeout=(5, 30), session=None):
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("FluidCalendar base URL must be an absolute HTTP(S) URL")
        if not api_key:
            raise ValueError("FluidCalendar API key is required")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _request(self, method, path, payload, success_statuses):
        try:
            response = self.session.request(
                method,
                self.base_url + path,
                headers=self.headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise FluidCalendarError(
                f"FluidCalendar request failed ({type(error).__name__})", retryable=True
            ) from error
        if response.status_code not in success_statuses:
            raise FluidCalendarError(
                f"FluidCalendar returned HTTP {response.status_code}",
                retryable=is_retryable_status(response.status_code),
                status_code=response.status_code,
            )
        try:
            result = response.json()
        except (TypeError, ValueError) as error:
            raise FluidCalendarError("FluidCalendar returned invalid JSON", retryable=True) from error
        if not isinstance(result, dict):
            raise FluidCalendarError("FluidCalendar returned an unexpected response", retryable=True)
        return result

    def create_event(self, payload):
        return self._request("POST", "/api/events", payload, {200, 201})

    def update_event(self, event_id, payload):
        update = {key: value for key, value in payload.items() if key != "skipIfExists"}
        return self._request("PATCH", "/api/events/" + quote(str(event_id), safe=""), update, {200})


def _scope_id(user_id, feed_id):
    value = json.dumps([str(user_id), str(feed_id)], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def delivery_id(user_id, feed_id, prepared_event_id):
    value = json.dumps(
        [str(user_id), str(feed_id), str(prepared_event_id)], separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def ensure_calendar_indexes(database):
    database[DELIVERIES].create_index(
        [("userId", ASCENDING), ("feedId", ASCENDING), ("state", ASCENDING), ("nextAttemptAt", ASCENDING)]
    )
    database[DELIVERIES].create_index(
        [("userId", ASCENDING), ("feedId", ASCENDING), ("preparedEventId", ASCENDING)], unique=True
    )
    database[BACKFILLS].create_index([("userId", ASCENDING), ("feedId", ASCENDING)], unique=True)


def queue_sleep_delivery(database, user_id, feed_id, event, analytics_run_id, payload, *, now=None):
    """Observe a prepared event and queue create/update work when its rendering changed."""
    ensure_calendar_indexes(database)
    now = now or utcnow()
    item_id = delivery_id(user_id, feed_id, event["id"])
    rendered_hash = payload_hash(payload)
    collection = database[DELIVERIES]
    existing = collection.find_one({"_id": item_id})
    if existing is None:
        collection.insert_one({
            "_id": item_id,
            "userId": str(user_id),
            "feedId": str(feed_id),
            "preparedEventId": str(event["id"]),
            "wakeDate": event["date"],
            "analyticsRunId": analytics_run_id,
            "payloadHash": rendered_hash,
            "state": "pending",
            "operation": "create",
            "attempts": 0,
            "nextAttemptAt": now,
            "firstSeenAt": now,
            "lastSeenAt": now,
        })
        return collection.find_one({"_id": item_id})

    collection.update_one(
        {"_id": item_id},
        {"$set": {
            "wakeDate": event["date"], "analyticsRunId": analytics_run_id, "lastSeenAt": now,
        }},
    )
    if existing.get("payloadHash") != rendered_hash and existing.get("state") != "delivering":
        collection.update_one(
            {"_id": item_id, "payloadHash": existing.get("payloadHash"), "state": {"$ne": "delivering"}},
            {
                "$set": {
                    "payloadHash": rendered_hash,
                    "state": "pending",
                    "operation": "update" if existing.get("remoteEventId") else "create",
                    "attempts": 0,
                    "nextAttemptAt": now,
                },
                "$unset": {"error": "", "failedAt": ""},
            },
        )
    return collection.find_one({"_id": item_id})


def claim_sleep_delivery(database, worker_id, *, user_id=None, feed_id=None, lease_seconds=300, now=None):
    now = now or utcnow()
    query = {
        "$or": [
            {"state": {"$in": ["pending", "retryable"]}, "nextAttemptAt": {"$lte": now}},
            {"state": "delivering", "leaseUntil": {"$lt": now}},
        ]
    }
    if user_id is not None:
        query["userId"] = str(user_id)
    if feed_id is not None:
        query["feedId"] = str(feed_id)
    return database[DELIVERIES].find_one_and_update(
        query,
        {
            "$set": {
                "state": "delivering",
                "workerId": str(worker_id),
                "startedAt": now,
                "leaseUntil": now + dt.timedelta(seconds=lease_seconds),
            },
            "$inc": {"attempts": 1},
        },
        sort=[("nextAttemptAt", ASCENDING), ("wakeDate", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def complete_sleep_delivery(database, delivery, response, *, now=None):
    now = now or utcnow()
    remote_id = response.get("id")
    if not remote_id:
        raise ValueError("FluidCalendar response is missing event id")
    result = database[DELIVERIES].update_one(
        {"_id": delivery["_id"], "state": "delivering", "workerId": delivery.get("workerId")},
        {
            "$set": {
                "state": "delivered",
                "remoteEventId": str(remote_id),
                "externalEventId": response.get("externalEventId"),
                "deliveredAt": now,
            },
            "$unset": {"workerId": "", "leaseUntil": "", "error": "", "nextAttemptAt": ""},
        },
    )
    return result.modified_count == 1


def fail_sleep_delivery(
    database, delivery, error, *, max_attempts=8, retry_delay_seconds=60, now=None
):
    now = now or utcnow()
    retryable = bool(getattr(error, "retryable", False)) and delivery.get("attempts", 0) < max_attempts
    state = "retryable" if retryable else "permanent_failure"
    update = {
        "$set": {
            "state": state,
            "failedAt": now,
            "error": {
                "type": type(error).__name__,
                "message": str(error)[:500],
                "statusCode": getattr(error, "status_code", None),
            },
        },
        "$unset": {"workerId": "", "leaseUntil": ""},
    }
    if retryable:
        update["$set"]["nextAttemptAt"] = now + dt.timedelta(seconds=max(0, retry_delay_seconds))
    else:
        update["$unset"]["nextAttemptAt"] = ""
    result = database[DELIVERIES].update_one(
        {"_id": delivery["_id"], "state": "delivering", "workerId": delivery.get("workerId")}, update
    )
    return result.modified_count == 1


def initialize_backfill(database, user_id, feed_id, next_end_date, *, now=None):
    """Create, but never rewind, the per-user/feed backward cursor."""
    ensure_calendar_indexes(database)
    now = now or utcnow()
    identity = _scope_id(user_id, feed_id)
    database[BACKFILLS].update_one(
        {"_id": identity},
        {"$setOnInsert": {
            "userId": str(user_id), "feedId": str(feed_id), "status": "ready",
            "nextEndDate": str(next_end_date), "createdAt": now, "updatedAt": now,
        }},
        upsert=True,
    )
    return database[BACKFILLS].find_one({"_id": identity})


def claim_backfill_window(
    database, user_id, feed_id, worker_id, *, batch_days=7, lease_seconds=300, now=None
):
    now = now or utcnow()
    identity = _scope_id(user_id, feed_id)
    state = database[BACKFILLS].find_one_and_update(
        {
            "_id": identity,
            "$or": [
                {"status": "ready"},
                {"status": "running", "leaseUntil": {"$lt": now}},
            ],
        },
        {"$set": {
            "status": "running", "workerId": str(worker_id), "startedAt": now,
            "leaseUntil": now + dt.timedelta(seconds=lease_seconds), "updatedAt": now,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not state or not state.get("nextEndDate"):
        return None
    end_date = dt.date.fromisoformat(state["nextEndDate"])
    start_date = end_date - dt.timedelta(days=max(1, int(batch_days)) - 1)
    state["windowStartDate"] = start_date.isoformat()
    state["windowEndDate"] = end_date.isoformat()
    return state


def complete_backfill_window(database, state, *, reached_beginning=False, now=None):
    now = now or utcnow()
    next_end = (dt.date.fromisoformat(state["windowStartDate"]) - dt.timedelta(days=1)).isoformat()
    values = {
        "status": "complete" if reached_beginning else "ready",
        "nextEndDate": None if reached_beginning else next_end,
        "lastCompletedStartDate": state["windowStartDate"],
        "lastCompletedEndDate": state["windowEndDate"],
        "updatedAt": now,
    }
    result = database[BACKFILLS].update_one(
        {"_id": state["_id"], "status": "running", "workerId": state.get("workerId")},
        {"$set": values, "$unset": {"workerId": "", "leaseUntil": ""}},
    )
    return result.modified_count == 1


def fail_backfill_window(database, state, error, *, now=None):
    now = now or utcnow()
    result = database[BACKFILLS].update_one(
        {"_id": state["_id"], "status": "running", "workerId": state.get("workerId")},
        {
            "$set": {
                "status": "ready", "failedAt": now, "updatedAt": now,
                "error": {"type": type(error).__name__, "message": str(error)[:500]},
            },
            "$unset": {"workerId": "", "leaseUntil": ""},
        },
    )
    return result.modified_count == 1
