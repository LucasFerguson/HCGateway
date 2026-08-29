"""Canonical sleep reconciliation and reusable prepared sleep summaries."""

from collections import defaultdict

from .time_utils import date_key, local_iso, parse_instant


SAME_SLEEP_EVENT_OVERLAP_RATIO = 0.8
CANONICAL_DURATION_FLOOR_RATIO = 0.98
STAGE_NAMES = ("deep", "light", "rem", "asleep", "awake", "unknown")
DETAILED_STAGE_NAMES = frozenset(("deep", "light", "rem"))


def session_minutes(session):
    return max(
        0.0,
        (parse_instant(session["endAt"]) - parse_instant(session["startAt"])).total_seconds() / 60,
    )


def stage_minutes(session):
    totals = defaultdict(float)
    for stage in session.get("stages", []):
        minutes = max(
            0.0,
            (parse_instant(stage["endAt"]) - parse_instant(stage["startAt"])).total_seconds() / 60,
        )
        totals[stage["kind"] if stage["kind"] in STAGE_NAMES else "unknown"] += minutes
    return {name: totals.get(name, 0.0) for name in STAGE_NAMES}


def sleep_minutes(session):
    stages = session.get("stages", [])
    if not stages:
        return session_minutes(session)
    totals = stage_minutes(session)
    return sum(totals[name] for name in ("deep", "light", "rem", "asleep"))


def summarize_sleep_session(session):
    """Prepare durations plus auditable stage-quality diagnostics without rewriting source data."""
    start = parse_instant(session["startAt"])
    end = parse_instant(session["endAt"])
    window_minutes = session_minutes(session)
    stages = session.get("stages", [])
    totals = stage_minutes(session)
    flags = []
    intervals = []
    if end <= start:
        flags.append("invalid_session_window")
    for stage in stages:
        stage_start = parse_instant(stage["startAt"])
        stage_end = parse_instant(stage["endAt"])
        if stage_end <= stage_start:
            flags.append("invalid_stage_interval")
            continue
        if stage_start < start or stage_end > end:
            flags.append("stage_outside_session")
        intervals.append((stage_start, stage_end))
    intervals.sort()
    if any(right_start < left_end for (_, left_end), (right_start, _) in zip(intervals, intervals[1:])):
        flags.append("overlapping_stages")

    coverage_minutes = sum(totals.values())
    detailed_minutes = sum(totals[name] for name in DETAILED_STAGE_NAMES)
    coverage_ratio = coverage_minutes / window_minutes if window_minutes else 0.0
    detailed_ratio = detailed_minutes / window_minutes if window_minutes else 0.0
    timeline_valid = bool(stages) and not any(
        flag in flags for flag in (
            "invalid_session_window", "invalid_stage_interval", "stage_outside_session", "overlapping_stages"
        )
    )
    credible_coverage = timeline_valid and 0.5 <= coverage_ratio <= 1.1
    credible_detailed = credible_coverage and detailed_ratio >= 0.5
    if coverage_minutes > window_minutes + 0.01:
        flags.append("stage_coverage_exceeds_window")
    calculated_sleep = sleep_minutes(session)
    if calculated_sleep > window_minutes + 0.01:
        flags.append("sleep_exceeds_window")

    if not stages:
        stage_status = "missing"
    elif not timeline_valid:
        stage_status = "invalid"
    elif detailed_minutes:
        stage_status = "detailed"
    else:
        stage_status = "generic"
    return {
        "windowMinutes": window_minutes,
        "sleepMinutes": calculated_sleep,
        "stageMinutes": totals,
        "stageCoverageMinutes": coverage_minutes,
        "stageCoverageRatio": coverage_ratio,
        "detailedStageCoverageRatio": detailed_ratio,
        "stageDataStatus": stage_status,
        "hasCredibleStageTimeline": credible_coverage,
        "hasCredibleDetailedStages": credible_detailed,
        "qualityFlags": sorted(set(flags)),
    }


def sleep_overlap_ratio(left, right):
    overlap = max(
        0.0,
        (
            min(parse_instant(left["endAt"]), parse_instant(right["endAt"]))
            - max(parse_instant(left["startAt"]), parse_instant(right["startAt"]))
        ).total_seconds(),
    )
    shorter_seconds = min(session_minutes(left), session_minutes(right)) * 60
    return 0.0 if shorter_seconds == 0 else overlap / shorter_seconds


def _canonical_rank(session, summary):
    if summary["hasCredibleDetailedStages"]:
        quality_tier = 2
    elif summary["hasCredibleStageTimeline"]:
        quality_tier = 1
    else:
        quality_tier = 0
    coverage_quality = max(0.0, 1.0 - abs(1.0 - summary["stageCoverageRatio"]))
    return (
        -quality_tier,
        -coverage_quality,
        -summary["windowMinutes"],
        str(session.get("source") or ""),
        str(session["id"]),
    )


def choose_primary_sleep_recording(recordings):
    summaries = {str(recording["id"]): summarize_sleep_session(recording) for recording in recordings}
    longest = max((summary["windowMinutes"] for summary in summaries.values()), default=0.0)
    duration_floor = longest * CANONICAL_DURATION_FLOOR_RATIO
    eligible = [recording for recording in recordings if summaries[str(recording["id"])]["windowMinutes"] >= duration_floor]
    primary = min(eligible, key=lambda recording: _canonical_rank(recording, summaries[str(recording["id"])]))
    ordered = [primary] + sorted(
        (recording for recording in recordings if recording is not primary),
        key=lambda recording: (-summaries[str(recording["id"])]["windowMinutes"], str(recording["id"])),
    )
    primary_summary = summaries[str(primary["id"])]
    return primary, ordered, primary_summary, {
        "method": "quality_then_window_within_duration_tolerance",
        "version": "sleep-primary-v2",
        "minimumWindowRatio": CANONICAL_DURATION_FLOOR_RATIO,
        "longestWindowMinutes": longest,
        "selectedWindowMinutes": primary_summary["windowMinutes"],
        "selectedStageDataStatus": primary_summary["stageDataStatus"],
        "selectedHasCredibleDetailedStages": primary_summary["hasCredibleDetailedStages"],
        "eligibleRecordingCount": len(eligible),
    }


def select_main_sleep_event(events):
    return min(
        events,
        key=lambda event: (
            -event.get("windowMinutes", session_minutes(event["primary"])),
            parse_instant(event["primary"]["startAt"]),
            str(event["id"]),
        ),
        default=None,
    )


def reconcile_sleep_events(sessions, context):
    """Group overlapping recordings before assigning the canonical local wake date."""
    ordered = sorted(
        sessions,
        key=lambda session: (parse_instant(session["startAt"]), parse_instant(session["endAt"]), str(session["id"])),
    )
    parent = list(range(len(ordered)))

    def find(index):
        if parent[index] != index:
            parent[index] = find(parent[index])
        return parent[index]

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(ordered):
        left_end = parse_instant(left["endAt"])
        for right_index in range(left_index + 1, len(ordered)):
            right = ordered[right_index]
            if parse_instant(right["startAt"]) >= left_end:
                break
            if sleep_overlap_ratio(left, right) >= SAME_SLEEP_EVENT_OVERLAP_RATIO:
                union(left_index, right_index)

    groups = defaultdict(list)
    for index, session in enumerate(ordered):
        groups[find(index)].append(session)

    events = []
    for recordings in groups.values():
        primary, ranked, summary, selection = choose_primary_sleep_recording(recordings)
        date = date_key(primary["endAt"], context.homeTimeZone)
        events.append({
            "id": primary["id"],
            "date": date,
            "timeZone": context.homeTimeZone,
            "localStartAt": local_iso(primary["startAt"], context.homeTimeZone),
            "localEndAt": local_iso(primary["endAt"], context.homeTimeZone),
            "role": None,
            "primary": primary,
            "recordings": ranked,
            "recordingCount": len(ranked),
            **summary,
            "primarySelection": selection,
        })

    by_date = defaultdict(list)
    for event in events:
        by_date[event["date"]].append(event)
    for daily in by_date.values():
        main = select_main_sleep_event(daily)
        for event in daily:
            event["role"] = "main" if event is main else "supplemental"
    return sorted(events, key=lambda event: parse_instant(event["primary"]["startAt"]))


def aggregate_daily_sleep(events):
    groups = defaultdict(list)
    for event in events:
        groups[event["date"]].append(event)
    results = []
    for date, daily in sorted(groups.items()):
        stage_totals = {name: sum(event["stageMinutes"][name] for event in daily) for name in STAGE_NAMES}
        statuses = [event["stageDataStatus"] for event in daily]
        valid_statuses = {"detailed", "generic"}
        if all(status == "missing" for status in statuses):
            stage_status = "missing"
        elif all(status in valid_statuses for status in statuses):
            stage_status = "available"
        else:
            stage_status = "partial"
        main = select_main_sleep_event(daily)
        results.append({
            "date": date,
            "timeZone": main["timeZone"],
            "sleepMinutes": sum(event["sleepMinutes"] for event in daily),
            "stageMinutes": stage_totals,
            "stageDataStatus": stage_status,
            "unclassifiedSleepMinutes": sum(
                event["sleepMinutes"] for event in daily if event["stageDataStatus"] == "missing"
            ),
            "eventCount": len(daily),
            "recordingCount": sum(event["recordingCount"] for event in daily),
            "mainEventId": main["id"],
        })
    return results
