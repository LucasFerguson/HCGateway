import datetime as dt

from pymongo import ASCENDING, DESCENDING

from .crypto import decrypt_json, encrypt_json


RUNS = "_analytics_runs"
CURRENT = "_analytics_current"
SNAPSHOTS = "_analytics_snapshots"
DAILY = "_analytics_daily"
SLEEP_EVENTS = "_analytics_sleep_events"
DEVICE_COMPARISONS = "_analytics_device_comparisons"
SUMMARIES = "_analytics_summaries"


def ensure_indexes(database):
    database[RUNS].create_index([("status", ASCENDING), ("completedAt", DESCENDING)])
    database[DAILY].create_index([("runId", ASCENDING), ("date", ASCENDING)], unique=True)
    database[SLEEP_EVENTS].create_index([("runId", ASCENDING), ("eventId", ASCENDING)], unique=True)
    database[SLEEP_EVENTS].create_index([("runId", ASCENDING), ("date", ASCENDING)])
    database[DEVICE_COMPARISONS].create_index(
        [("runId", ASCENDING), ("metric", ASCENDING), ("source", ASCENDING)], unique=True
    )
    database[SUMMARIES].create_index([("runId", ASCENDING), ("kind", ASCENDING)], unique=True)


def run_id_for(analytics):
    return ":".join((analytics["algorithmVersion"], analytics["sourceFingerprint"], analytics["configurationFingerprint"]))


def _without(container, key):
    return {name: value for name, value in container.items() if name != key}


def _daily_documents(analytics):
    names = {
        "sleep": analytics["dailySleep"],
        "sleepDebt": analytics["sleepDebt"]["daily"],
        "sleepConsistency": analytics["sleepConsistency"]["daily"],
        "healthspan": analytics["healthspan"]["trend"],
        "steps": analytics["steps"]["daily"],
        "activeCalories": analytics["activeCalories"]["daily"],
        "totalCalories": analytics["totalCalories"]["daily"],
        "restingHeartRate": analytics["restingHeartRate"]["daily"],
        "weight": analytics["weight"]["daily"],
        "strain": analytics["strain"]["daily"],
        "dayView": analytics["dayViews"],
    }
    dates = sorted({item["date"] for values in names.values() for item in values})
    lookup = {name: {item["date"]: item for item in values} for name, values in names.items()}
    return [{"date": date, **{name: values.get(date) for name, values in lookup.items()}} for date in dates]


def save_analytics(database, cipher, raw, analytics, issues=None):
    ensure_indexes(database)
    run_id = run_id_for(analytics)
    current = database[CURRENT].find_one({"_id": "current"})
    if current and current.get("runId") == run_id:
        return "unchanged", run_id

    database[RUNS].update_one(
        {"_id": run_id},
        {
            "$setOnInsert": {
                "status": "started",
                "algorithmVersion": analytics["algorithmVersion"],
                "sourceFingerprint": analytics["sourceFingerprint"],
                "configurationFingerprint": analytics["configurationFingerprint"],
                "startedAt": analytics["processedAt"],
            }
        },
        upsert=True,
    )
    try:
        # Detailed day views and per-sample strain timelines live in the
        # date-indexed collections. Keeping them out of the legacy monolithic
        # snapshot avoids MongoDB's 16 MiB document limit after encryption.
        snapshot_analytics = {
            key: value for key, value in analytics.items() if key != "dayViews"
        }
        snapshot_analytics["strain"] = _without(_without(analytics["strain"], "daily"), "workouts")
        snapshot = {
            "generatedAt": analytics["processedAt"],
            "source": "health-connect",
            "sleepSessions": raw["sleepSessions"],
            "analytics": snapshot_analytics,
        }
        database[SNAPSHOTS].update_one(
            {"_id": run_id},
            {"$setOnInsert": {"runId": run_id, "data": encrypt_json(cipher, snapshot)}},
            upsert=True,
        )
        for payload in _daily_documents(analytics):
            database[DAILY].update_one(
                {"runId": run_id, "date": payload["date"]},
                {"$setOnInsert": {"runId": run_id, "date": payload["date"], "data": encrypt_json(cipher, payload)}},
                upsert=True,
            )
        for event in analytics["sleepEvents"]:
            database[SLEEP_EVENTS].update_one(
                {"runId": run_id, "eventId": event["id"]},
                {"$setOnInsert": {"runId": run_id, "eventId": event["id"], "date": event["date"], "data": encrypt_json(cipher, event)}},
                upsert=True,
            )
        for comparison in analytics["deviceSleep"]:
            database[DEVICE_COMPARISONS].update_one(
                {"runId": run_id, "metric": "sleep", "source": comparison["source"]},
                {"$setOnInsert": {"runId": run_id, "metric": "sleep", "source": comparison["source"], "data": encrypt_json(cipher, comparison)}},
                upsert=True,
            )
        summaries = {
            "sleepDebt": _without(analytics["sleepDebt"], "daily"),
            "sleepConsistency": _without(analytics["sleepConsistency"], "daily"),
            "healthspan": _without(analytics["healthspan"], "trend"),
            "metricOverviews": {
                name: {key: value for key, value in analytics[name].items() if key != "daily"}
                for name in ("steps", "activeCalories", "totalCalories", "restingHeartRate", "weight")
            },
            "strain": _without(_without(analytics["strain"], "daily"), "workouts"),
        }
        for kind, payload in summaries.items():
            database[SUMMARIES].update_one(
                {"runId": run_id, "kind": kind},
                {"$setOnInsert": {"runId": run_id, "kind": kind, "data": encrypt_json(cipher, payload)}},
                upsert=True,
            )
        completed_at = dt.datetime.now(dt.timezone.utc)
        counts = {
            "sleepEvents": len(analytics["sleepEvents"]),
            "dailySleep": len(analytics["dailySleep"]),
            "dailySleepDebt": len(analytics["sleepDebt"]["daily"]),
            "dailySleepConsistency": len(analytics["sleepConsistency"]["daily"]),
            "dailyHealthspanEstimates": len(analytics["healthspan"]["trend"]),
            "dailySteps": len(analytics["steps"]["daily"]),
            "dailyActiveCalories": len(analytics["activeCalories"]["daily"]),
            "dailyTotalCalories": len(analytics["totalCalories"]["daily"]),
            "dailyRestingHeartRate": len(analytics["restingHeartRate"]["daily"]),
            "weightMeasurements": len(analytics["weight"]["daily"]),
            "dailyStrain": len(analytics["strain"]["daily"]),
            "dayViews": len(analytics["dayViews"]),
        }
        database[RUNS].update_one(
            {"_id": run_id},
            {"$set": {"status": "completed", "completedAt": completed_at, "counts": counts, "issueCount": len(issues or []), "issues": (issues or [])[:100]}},
        )
        database[CURRENT].update_one(
            {"_id": "current"},
            {"$set": {
                "runId": run_id,
                "algorithmVersion": analytics["algorithmVersion"],
                "sourceFingerprint": analytics["sourceFingerprint"],
                "configurationFingerprint": analytics["configurationFingerprint"],
                "completedAt": completed_at,
            }},
            upsert=True,
        )
        return "saved", run_id
    except Exception as error:
        database[RUNS].update_one(
            {"_id": run_id},
            {"$set": {"status": "failed", "failedAt": dt.datetime.now(dt.timezone.utc), "error": {"type": type(error).__name__, "message": str(error)[:1000]}}},
            upsert=True,
        )
        raise


def current_metadata(database):
    current = database[CURRENT].find_one({"_id": "current"}, {"_id": 0})
    if not current:
        return None
    run = database[RUNS].find_one({"_id": current["runId"]}, {"issues": 0}) or {}
    return {**current, "counts": run.get("counts", {}), "issueCount": run.get("issueCount", 0)}


def read_snapshot(database, cipher):
    current = database[CURRENT].find_one({"_id": "current"})
    if not current:
        return None, None
    document = database[SNAPSHOTS].find_one({"_id": current["runId"]})
    if not document:
        return None, current
    return decrypt_json(cipher, document["data"]), current


def read_daily(database, cipher, start=None, end=None, limit=400):
    current = database[CURRENT].find_one({"_id": "current"})
    if not current:
        return [], None
    query = {"runId": current["runId"]}
    if start or end:
        query["date"] = {}
        if start:
            query["date"]["$gte"] = start
        if end:
            query["date"]["$lte"] = end
    documents = database[DAILY].find(query).sort("date", ASCENDING).limit(limit)
    return [decrypt_json(cipher, document["data"]) for document in documents], current
