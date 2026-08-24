import hashlib
import json

from .context import context_for_user
from .crypto import cipher_for_user
from .pipeline import process_health_data
from .repository import load_raw_health_data
from .store import save_analytics


SOURCE_LABELS = {
    "com.whoop.android": "WHOOP",
    "com.fitbit.FitbitMobile": "Fitbit / Google Health",
    "com.google.android.apps.fitness": "Google Fit",
    "android": "Android",
}

DEVICE_TYPE_LABELS = {
    0: "unknown",
    1: "watch",
    2: "phone",
    3: "scale",
    4: "ring",
    5: "head_mounted",
    6: "fitness_band",
    7: "chest_strap",
    8: "smart_display",
    9: "consumer_medical_device",
    10: "glasses",
    11: "hearable",
    12: "fitness_machine",
    13: "fitness_equipment",
    14: "portable_computer",
    15: "meter",
}

RECORDING_METHOD_LABELS = {
    0: "unknown",
    1: "actively_recorded",
    2: "automatically_recorded",
    3: "manual_entry",
}


def source_label(source):
    if source and source.startswith("com.android.healthconnect.phone."):
        return "Health Connect phone source"
    return SOURCE_LABELS.get(source, source or "unknown")


def _normalized_device(value):
    if not isinstance(value, dict):
        return None
    device_type = value.get("type")
    return {
        "manufacturer": value.get("manufacturer") or None,
        "model": value.get("model") or None,
        "type": device_type,
        "typeLabel": DEVICE_TYPE_LABELS.get(device_type, "unknown"),
    }


def _device_description(source, device, legacy):
    label = source_label(source)
    if legacy:
        return f"{label} legacy records (device metadata unavailable)"
    manufacturer = device.get("manufacturer") if device else None
    model = device.get("model") if device else None
    type_label = (device or {}).get("typeLabel", "unknown").replace("_", " ")
    name = " ".join(str(value) for value in (manufacturer, model) if value)
    if name:
        return f"{name} ({type_label})"
    return f"{label} {type_label} device (manufacturer/model not supplied)"


def device_inventory_for_user(database):
    """Build a device/source catalog from unencrypted Health Connect metadata."""
    grouped = {}
    for signal in sorted(item for item in database.list_collection_names() if not item.startswith("_")):
        collection = database[signal]
        rows = collection.aggregate([{
            "$group": {
                "_id": {
                    "source": "$app",
                    "device": "$provenance.device",
                    "recordingMethod": "$provenance.recordingMethod",
                },
                "records": {"$sum": 1},
                "earliest": {"$min": "$start"},
                "latest": {"$max": "$start"},
            }
        }])
        for row in rows:
            raw_identity = row.get("_id") or {}
            source = raw_identity.get("source") or "unknown"
            device = _normalized_device(raw_identity.get("device"))
            legacy = device is None
            identity = {"sourcePackage": source, "device": device, "legacy": legacy}
            identity_json = json.dumps(identity, sort_keys=True, separators=(",", ":"))
            device_id = "observed-" + hashlib.sha256(identity_json.encode()).hexdigest()[:16]
            identity_quality = (
                "explicit_model" if device and (device.get("manufacturer") or device.get("model"))
                else "device_type" if device and device.get("type") not in (None, 0)
                else "source_only"
            )
            entry = grouped.setdefault(device_id, {
                "id": device_id,
                "description": _device_description(source, device, legacy),
                "sourcePackage": source,
                "sourceLabel": source_label(source),
                "device": device,
                "identityQuality": identity_quality,
                "mayCombinePhysicalDevices": identity_quality != "explicit_model",
                "records": 0,
                "earliest": None,
                "latest": None,
                "signals": {},
                "recordingMethods": {},
                "association": {
                    "sourcePackage": source,
                    "deviceMetadata": device,
                    "legacyWithoutDeviceMetadata": legacy,
                },
            })
            count = row["records"]
            entry["records"] += count
            entry["signals"][signal] = entry["signals"].get(signal, 0) + count
            method = raw_identity.get("recordingMethod")
            method_key = "unavailable" if method is None else str(method)
            method_entry = entry["recordingMethods"].setdefault(method_key, {
                "value": method,
                "label": RECORDING_METHOD_LABELS.get(method, "unavailable"),
                "records": 0,
            })
            method_entry["records"] += count
            earliest, latest = row.get("earliest"), row.get("latest")
            if earliest is not None and (entry["earliest"] is None or earliest < entry["earliest"]):
                entry["earliest"] = earliest
            if latest is not None and (entry["latest"] is None or latest > entry["latest"]):
                entry["latest"] = latest

    devices = []
    for entry in grouped.values():
        entry["signals"] = dict(sorted(entry["signals"].items()))
        entry["recordingMethods"] = sorted(
            entry["recordingMethods"].values(),
            key=lambda item: (item["value"] is None, item["value"] if item["value"] is not None else 99),
        )
        devices.append(entry)
    devices.sort(key=lambda item: (-item["records"], item["id"]))
    return {
        "count": len(devices),
        "devices": devices,
        "limitations": [
            "Device metadata is supplied by the app that writes each Health Connect record.",
            "Source-only identities can combine multiple physical devices, including Fitbit devices used in different date ranges.",
            "Date ranges describe observations and are not used as identity keys.",
        ],
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
            entry = result["sources"].setdefault(source, {"label": source_label(source), "records": 0})
            entry["records"] += source_count
    return result
