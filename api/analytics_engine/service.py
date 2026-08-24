from .context import context_for_user
from .crypto import cipher_for_user
from .pipeline import process_health_data
from .repository import load_raw_health_data
from .store import save_analytics


SOURCE_LABELS = {
    "com.whoop.android": "WHOOP",
    "com.fitbit.FitbitMobile": "Fitbit / Pixel Watch",
    "com.google.android.apps.fitness": "Google Fit",
    "android": "Android",
}


def process_user(mongo, user):
    user_id = str(user["_id"])
    database = mongo["hcgateway_" + user_id]
    cipher = cipher_for_user(user)
    raw, issues = load_raw_health_data(database, cipher)
    analytics = process_health_data(raw, context_for_user(user))
    persistence, run_id = save_analytics(database, cipher, raw, analytics, issues)
    return {
        "persistence": persistence,
        "runId": run_id,
        "algorithmVersion": analytics["algorithmVersion"],
        "sourceFingerprint": analytics["sourceFingerprint"],
        "configurationFingerprint": analytics["configurationFingerprint"],
        "issueCount": len(issues),
    }


def inventory_for_user(database):
    result = {"totalRecords": 0, "earliest": None, "latest": None, "signals": {}, "sources": {}}
    for name in sorted(item for item in database.list_collection_names() if not item.startswith("_")):
        collection = database[name]
        count = collection.count_documents({})
        source_counts = {
            (item["_id"] or "unknown"): item["count"]
            for item in collection.aggregate([{"$group": {"_id": "$app", "count": {"$sum": 1}}}])
        }
        first = collection.find_one({}, {"start": 1}, sort=[("start", 1)])
        last = collection.find_one({}, {"start": 1}, sort=[("start", -1)])
        earliest = first.get("start") if first else None
        latest = last.get("start") if last else None
        result["signals"][name] = {"records": count, "earliest": earliest, "latest": latest, "bySource": source_counts}
        result["totalRecords"] += count
        if earliest and (result["earliest"] is None or earliest < result["earliest"]):
            result["earliest"] = earliest
        if latest and (result["latest"] is None or latest > result["latest"]):
            result["latest"] = latest
        for source, source_count in source_counts.items():
            entry = result["sources"].setdefault(source, {"label": SOURCE_LABELS.get(source, source), "records": 0})
            entry["records"] += source_count
    return result
