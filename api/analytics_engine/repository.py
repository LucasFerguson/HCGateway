"""Read and normalize encrypted HCGateway records without modifying them."""

from .crypto import decrypt_json
from .pipeline import parse_instant


STAGE_KINDS = {1: "awake", 2: "asleep", 3: "unknown", 4: "light", 5: "deep", 6: "rem"}


def _envelope(document, require_end=False):
    record_id = str(document.get("id") or document.get("_id"))
    source = document.get("app")
    start = document.get("start")
    end = document.get("end")
    if not record_id or not isinstance(source, str) or not isinstance(start, str):
        raise ValueError("invalid record envelope")
    parse_instant(start)
    if require_end and not isinstance(end, str):
        raise ValueError("record requires an end timestamp")
    if isinstance(end, str):
        parse_instant(end)
    return record_id, source, start, end


def _number(value, name, minimum=None, strictly_positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if strictly_positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} is below its minimum")
    return value


def _sleep(document, data):
    record_id, source, start, end = _envelope(document, require_end=True)
    stages = []
    for stage in data.get("stages", []):
        stage_start, stage_end = stage.get("startTime"), stage.get("endTime")
        parse_instant(stage_start)
        parse_instant(stage_end)
        stage_id = _number(stage.get("stage"), "sleep stage", integer=True)
        stages.append({"startAt": stage_start, "endAt": stage_end, "kind": STAGE_KINDS.get(stage_id, "unknown")})
    return {
        "id": record_id,
        "source": source,
        "startAt": start,
        "endAt": end,
        "title": data.get("title"),
        "notes": data.get("notes"),
        "stages": stages,
    }


def _steps(document, data):
    record_id, source, start, end = _envelope(document, require_end=True)
    count = _number(data.get("count"), "steps", minimum=0, integer=True)
    return {"id": record_id, "source": source, "startAt": start, "endAt": end, "count": count}


def _energy(document, data):
    record_id, source, start, end = _envelope(document, require_end=True)
    kcal = _number((data.get("energy") or {}).get("inKilocalories"), "energy", minimum=0)
    return {"id": record_id, "source": source, "startAt": start, "endAt": end, "energyKcal": kcal}


def _resting_heart_rate(document, data):
    record_id, source, start, _ = _envelope(document)
    bpm = _number(data.get("beatsPerMinute"), "resting heart rate", strictly_positive=True)
    return {"id": record_id, "source": source, "observedAt": start, "bpm": bpm}


def _weight(document, data):
    record_id, source, start, _ = _envelope(document)
    kilograms = _number((data.get("weight") or {}).get("inKilograms"), "weight", strictly_positive=True)
    return {"id": record_id, "source": source, "observedAt": start, "kilograms": kilograms}


MAPPERS = {
    "sleepSession": ("sleepSessions", _sleep),
    "steps": ("steps", _steps),
    "activeCaloriesBurned": ("activeCalories", _energy),
    "totalCaloriesBurned": ("totalCalories", _energy),
    "restingHeartRate": ("restingHeartRates", _resting_heart_rate),
    "weight": ("weights", _weight),
}


def empty_raw_health_data():
    return {name: [] for name in ("sleepSessions", "steps", "activeCalories", "totalCalories", "restingHeartRates", "weights")}


def load_raw_health_data(user_db, cipher):
    raw = empty_raw_health_data()
    issues = []
    available = set(user_db.list_collection_names())
    for collection_name, (target, mapper) in MAPPERS.items():
        if collection_name not in available:
            continue
        cursor = user_db[collection_name].find({}, {"id": 1, "app": 1, "start": 1, "end": 1, "data": 1}).sort("_id", 1)
        for document in cursor:
            try:
                data = decrypt_json(cipher, document["data"])
                raw[target].append(mapper(document, data))
            except Exception as error:
                issues.append({
                    "collection": collection_name,
                    "recordId": str(document.get("_id")),
                    "error": type(error).__name__,
                    "message": str(error)[:300],
                })
    return raw, issues
