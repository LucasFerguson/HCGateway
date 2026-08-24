# Analytics API and frontend data model

HCGateway keeps encrypted Health Connect records as its source of truth. A
separate Python worker decrypts the six currently supported analytics signals,
normalizes them, runs `health-analytics-v6`, and writes encrypted, immutable
prepared runs back to each user's MongoDB database.

The implementation is a behavioral port of the dashboard repository's
`pipeline/` and `src/domain/analytics.ts`. The frontend does not need to merge
WHOOP, Pixel/Fitbit, or Google Fit records itself.

## Frontend endpoints

All endpoints require `Authorization: Bearer <token>` and are scoped to the
authenticated user's `hcgateway_<user-id>` database.

### `GET /api/v2/analytics/snapshot`

This is the primary dashboard bootstrap endpoint. It returns the reference
dashboard contract unchanged:

```json
{
  "generatedAt": "2026-08-24T01:33:19.000Z",
  "source": "health-connect",
  "sleepSessions": [],
  "analytics": {
    "algorithmVersion": "health-analytics-v6",
    "sourceFingerprint": "...",
    "configurationFingerprint": "...",
    "processedAt": "...",
    "sleepEvents": [],
    "dailySleep": [],
    "deviceSleep": [],
    "sleepDebt": {},
    "sleepConsistency": {},
    "healthspan": {},
    "steps": {},
    "activeCalories": {},
    "totalCalories": {},
    "restingHeartRate": {},
    "weight": {}
  }
}
```

The response has a run-specific `ETag` and `Cache-Control: private, no-cache`.
A `404` means the first background run is not ready yet; inspect the status
endpoint and retry.

### `GET /api/v2/analytics/daily`

Returns a compact, date-indexed view of the current run. Supported query
parameters are inclusive `start` and `end` dates (`YYYY-MM-DD`) and `limit`
(1–1000, default 400).

```json
{
  "runId": "health-analytics-v6:<source>:<configuration>",
  "count": 1,
  "days": [
    {
      "date": "2026-08-24",
      "sleep": null,
      "sleepDebt": null,
      "sleepConsistency": null,
      "healthspan": null,
      "steps": null,
      "activeCalories": null,
      "totalCalories": null,
      "restingHeartRate": null,
      "weight": null
    }
  ]
}
```

### Supporting endpoints

- `GET /api/v2/analytics/status` returns the queue state and current run
  metadata/counts.
- `POST /api/v2/analytics/rebuild` queues a rebuild and returns `202`; it never
  blocks a web worker.
- `GET /api/v2/analytics/config` returns the effective home timezone, sleep
  target, and birth date.
- `PUT /api/v2/analytics/config` accepts `homeTimeZone` (IANA name),
  `sleepTargetMinutes` (240–720), and `birthDate` (`YYYY-MM-DD` in the past),
  then queues a new run.
- `GET /api/v2/analytics/inventory` returns raw counts, date coverage, and
  source packages without decrypting health values.

Sync uploads and database-side deletes automatically queue a debounced run.

## Canonical and device-comparison rules

- Sleep sessions with at least 80% overlap relative to the shorter recording
  are grouped. The longest is canonical while every device recording remains
  available for comparison.
- Sleep stages exclude awake and unknown time. If stages are absent, the full
  session duration is used. Naps stay separate and still contribute to daily
  sleep.
- Steps and calorie intervals are split proportionally at local-midnight
  boundaries. A day's canonical source is selected by interval coverage, then
  observation count. Steps are rounded.
- Resting heart rate uses the daily median. Weight uses the day's latest
  measurement. Source selection uses observation count, then recency.
- Sleep debt uses calendar 7/30/90-day windows. Consistency compares sleep
  timing to a circular 14-day prior baseline.
- Healthspan is explicitly an experimental estimate, not a clinical prediction.
  It uses available sleep, steps, resting-heart-rate, and weight factors; a
  birth date is required for age-based outputs.

Source identity is the Health Connect data-origin package (for example WHOOP or
Fitbit/Pixel Watch). New syncs also preserve device and recording provenance on
the raw record, so later algorithms can become device-aware without rewriting
history.

## MongoDB prepared collections

Every user database can contain:

- `_analytics_runs`: immutable run metadata, fingerprints, counts, and up to
  100 normalization issues;
- `_analytics_current`: the atomic pointer to the latest completed run;
- `_analytics_snapshots`: encrypted full frontend snapshots;
- `_analytics_daily`: encrypted per-date documents with queryable dates;
- `_analytics_sleep_events`: encrypted reconciled sleep events;
- `_analytics_device_comparisons`: encrypted per-source sleep comparisons;
- `_analytics_summaries`: encrypted debt, consistency, healthspan, and metric
  summaries.

Run IDs combine algorithm, source, and configuration fingerprints. Reprocessing
unchanged inputs is idempotent and does not duplicate a run. The current pointer
only advances after every prepared document is written successfully.

## Operations

The Compose stack contains `db`, `api`, and `analytics-worker` services:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f analytics-worker
docker compose down
docker compose up -d
```

`docker compose down` removes containers and the Compose network but preserves
the bind-mounted `./db` data. Do not add `--volumes` unless data removal is
intentional. To process queued jobs once and exit:

```bash
docker compose run --rm analytics-worker python -m analytics_engine.worker --drain
```

The current host runs Linux 7.0.2, which is in MongoDB's documented affected
kernel range for SERVER-121912. Compose temporarily pins the previously running
MongoDB 8.0.8 image by digest and sets `GLIBC_TUNABLES=glibc.pthread.rseq=1` to
avoid the affected TCMalloc path. This is a compatibility workaround: upgrade
the host to kernel 7.0.14 or later, then remove the override and move the image
pin to a supported current MongoDB patch release.
