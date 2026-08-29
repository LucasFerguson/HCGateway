# Analytics API and frontend data model

HCGateway keeps encrypted Health Connect records as its source of truth. A
separate Python worker decrypts the supported analytics signals, normalizes
them, runs `health-analytics-v8.3`, and writes encrypted, immutable
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
    "algorithmVersion": "health-analytics-v8.3",
    "timeZone": "America/Chicago",
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
  "runId": "health-analytics-v8.3:<source>:<configuration>",
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
- `GET /api/v2/analytics/devices` derives a device/source catalog from raw
  Health Connect provenance without decrypting health values. Entries include
  stable observed IDs, descriptions, device type/manufacturer/model when the
  writing app supplied them, recording-method counts, signal/date coverage,
  association fields, and an ambiguity flag. A source-only entry can combine
  multiple physical devices and must not be relabeled as a specific watch
  without corroborating metadata or a user-supplied date-window rule.

Sync uploads and database-side deletes automatically queue a debounced run.

## Canonical and device-comparison rules

- Sleep sessions with at least 80% overlap relative to the shorter recording
  are grouped before local wake-date assignment, so near-identical recordings
  ending on opposite sides of local midnight do not become two events. Every
  device recording remains available for comparison. Candidates within 98% of
  the longest window are ranked by credible stage detail and stage-timeline
  quality, then duration and stable source/record identifiers; a richer
  recording cannot displace one more than 2% longer. The prepared event exposes
  the selection method, duration threshold, stage coverage, quality flags, and
  per-stage minutes. The selected recording's local end date owns the event.
- Every wake date labels its longest event `main` and any additional separate
  sleep as `supplemental`. Supplemental events remain part of daily sleep and
  are not automatically called naps because split sleep and night-shift sleep
  require a distinct classification policy.
- Sleep stages exclude awake and unknown time. If stages are absent, the full
  session duration is used and `stageDataStatus` remains `missing`; stage zeros
  must not be presented as observed. Separate supplemental sleep still
  contributes to daily sleep. Daily stage totals cover all events on that wake
  date, while the headline `window` is explicitly scoped to the main event.
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

## Timezone ownership

Health Connect currently uploads absolute timestamps as UTC `Z` instants. A
live audit of 728 sleep sessions and 62,454 stage timestamps found no naive
timestamps and no sleep-specific source timezone/offset fields. The primary
account's configured analytics timezone is `America/Chicago`; it is therefore
the authoritative timezone for day ownership, local-midnight splitting, hourly
buckets, local-today defaults, and sleep wake-date assignment.

The backend owns those semantic conversions. Prepared timestamps remain
canonical absolute UTC instants, while analytics and prepared sleep events
expose the IANA `timeZone`. Sleep events also expose `localStartAt` and
`localEndAt` with the Chicago offset applicable at each instant, including DST
changes. These local strings are explanatory projections, not replacement
identities for the UTC instants.

The frontend owns presentation only. It must format every absolute instant with
the API-provided `timeZone` (for example `Intl.DateTimeFormat(..., {timeZone})`),
never the browser/server timezone. The focused day UI follows this rule. The
legacy sleep-stage graph in the separate frontend repository still uses
browser-local `toLocaleTimeString` and must be fixed or retired there; this
backend must not distort UTC timestamps to compensate for it.

Timezone-aware ingestion now rejects naive timestamps atomically with `400`,
and normalization emits canonical UTC strings while retaining raw source text
in the encrypted source-of-truth records. Exercise-session source offsets are
preserved as provenance, but they do not override the configured home timezone.
Travel-aware day assignment needs an explicit future policy because sleep
records currently carry no original location or timezone.

Local-midnight splitting honors 23-hour and 25-hour Chicago DST days. The
focused API's hourly arrays intentionally remain 24 wall-clock-number buckets:
the missing spring hour has no observations and both fall-back occurrences of
the repeated hour share one bucket. Consumers needing an elapsed-time DST axis
will require an offset/fold-aware contract rather than guessing client-side.

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
