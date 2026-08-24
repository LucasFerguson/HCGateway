# Analytics API and frontend data model

HCGateway keeps encrypted Health Connect records as its source of truth. A
separate Python worker decrypts the supported analytics signals, normalizes
them, runs `health-analytics-v8.1`, and writes encrypted, immutable
prepared runs back to each user's MongoDB database.

The implementation is a behavioral port of the dashboard repository's
`pipeline/` and `src/domain/analytics.ts`. The frontend does not need to merge
WHOOP, Pixel/Fitbit, or Google Fit records itself.

## Frontend endpoints

All endpoints require `Authorization: Bearer <token>` and are scoped to the
authenticated user's `hcgateway_<user-id>` database.

### `GET /api/v2/analytics/day?date=YYYY-MM-DD&radius=7`

This is the preferred endpoint for the health dashboard's day screen. It returns one
fully shaped day plus low-resolution summaries for up to seven days on each
side. The date defaults to today in the configured home timezone.

Every requested UI metric is present even when its value is not. Metric objects
use `status` values such as `available`, `partial`, `missing`,
`insufficient_data`, `not_implemented`, or `blocked`, and include a `note`
when the frontend should explain an absence. The focused day also has a flat
`availabilityNotes` list for banners, diagnostics, or tooltips.

The day contract includes:

- sleep duration, window, stage totals, and stage segments;
- a fixed-target sleep-need percentage clearly marked `partial`;
- provisional Recovery readiness and experimental cardiovascular strain with quality metadata;
- hourly heart-rate min/p25/mean/p75/max candlesticks;
- hourly steps, workouts, daily supporting metrics, and latest observation;
- explicit placeholders for strain target, schedule blocks, missing HRV,
  skin-temperature deviation, and bed/wake preferences.

Recovery v1 is a provisional, non-clinical heuristic. It combines sleep duration
(30%), HRV change from a trailing 28-day personal median (35%), resting-heart-rate
change from its trailing median (25%), and sleep consistency (10%). At least seven
prior days establish each physiological baseline. Missing components are reweighted,
but a score requires sleep plus either calibrated RHR or HRV; a score without HRV is
explicitly `partial`. The weights and response curves are intentionally flagged for
future validation and must not be represented as a proprietary wearable score.
A missing value is represented by a status and reason, never by a numeric zero.

### `GET /api/v2/analytics/snapshot`

This is the primary dashboard bootstrap endpoint. It returns the reference
dashboard contract unchanged:

```json
{
  "generatedAt": "2026-08-24T01:33:19.000Z",
  "source": "health-connect",
  "sleepSessions": [],
  "analytics": {
    "algorithmVersion": "health-analytics-v8.1",
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
    "weight": {},
    "heartRateVariability": {},
    "strain": {},
    "recovery": {}
  }
}
```

The response has a run-specific `ETag` and `Cache-Control: private, no-cache`.
A `404` means the first background run is not ready yet; inspect the status
endpoint and retry.

Detailed `dayViews`, daily strain/recovery, and workout-strain timelines are deliberately
excluded from this legacy monolithic snapshot. They are stored per date and
served by `/api/v2/analytics/day`, avoiding MongoDB's 16 MiB document limit
after encryption.

### `GET /api/v2/analytics/daily`

Returns a compact, date-indexed view of the current run. Supported query
parameters are inclusive `start` and `end` dates (`YYYY-MM-DD`) and `limit`
(1–1000, default 400).

```json
{
  "runId": "health-analytics-v8.1:<source>:<configuration>",
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
      "heartRateVariability": null,
      "weight": null,
      "strain": null,
      "recovery": null,
      "dayView": null
    }
  ]
}
```

### Supporting endpoints

- `GET /api/v2/sync/status` reports whether the server has observed an
  authenticated phone upload within the last 120 seconds, along with the last
  record type/count and lifetime upload counters. This is an ingestion
  heartbeat, not proof that Android's foreground task is still running.
- `GET /api/v2/analytics/status` returns the queue state and current run
  metadata/counts, plus the same `phoneSync` object.
- `POST /api/v2/analytics/rebuild` queues a rebuild and returns `202`; it never
  blocks a web worker.
- `GET /api/v2/analytics/config` returns the effective home timezone, sleep
  target, birth date, and optional personal heart-rate-zone calibration.
- `PUT /api/v2/analytics/config` accepts `homeTimeZone` (IANA name),
  `sleepTargetMinutes` (240–720), `birthDate` (`YYYY-MM-DD` in the past), six
  increasing `heartRateZoneThresholds`, and an optional
  `heartRateZoneTestDate`, then queues a new run.
- `GET /api/v2/analytics/inventory` returns raw counts, date coverage, and
  source packages without decrypting health values.

Sync uploads and database-side deletes automatically queue a debounced run.

## Canonical and device-comparison rules

- Sleep sessions with at least 80% overlap relative to the shorter recording
  are grouped. The longest is canonical while every device recording remains
  available for comparison. Sleep belongs to the local date on which it ends.
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
- Recovery is an explicitly provisional, non-clinical readiness estimate. Complete
  scores require sleep, HRV, RHR, and sleep consistency; partial scores may use sleep
  plus a calibrated RHR baseline while HRV is absent. Its heuristic weights and curves
  remain a documented future-validation task.
- Strain is an explicitly provisional, non-proprietary cardiovascular estimate. It
  integrates gap-limited heart-rate effort and logarithmically maps load to
  0–21. Empirical calibration may now publish with `low` confidence when a substantial
  history has a credible but sub-140 observed high; this is not a measured maximum.
  It still withholds scores when calibration or coverage is inadequate and does not
  claim WHOOP parity or muscular-load measurement. Calibration and load mapping remain
  explicitly flagged for future personal-outcome validation.

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
