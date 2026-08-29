# Session handoff: analytics backend

Last updated: 2026-08-29

This document is the starting point for the next coding session. The completed
implementation is documented in detail in
[`frontend-data-model.md`](frontend-data-model.md); this file records current
state, verification evidence, and likely next work.

## What was completed

HCGateway is now the backend and analytics system for the self-hosted Health
Dashboard. The TypeScript implementation in
`/root/health-connect-dashboard-for-fitbit` was used as the behavioral
reference, but only `/root/HCGateway` was modified.

- `api/analytics_engine/pipeline.py` ports `health-analytics-v6` to Python;
  the current prepared algorithm is `health-analytics-v8.1`.
- `api/analytics_engine/repository.py` decrypts and normalizes sleep sessions,
  steps, active/total calories, resting heart rate, and weight.
- `api/analytics_engine/worker.py` runs independently from Flask and claims
  durable, leased jobs from `hcgateway.analytics_jobs`.
- `api/analytics_engine/store.py` writes encrypted, immutable prepared runs and
  atomically advances a per-user current pointer.
- Sync uploads and database-side deletions queue analytics work.
- Flask exposes authenticated inventory, status, configuration, rebuild,
  snapshot, and daily endpoints under `/api/v2/analytics`.
- Docker Compose builds the local source into `hcgateway-api:local` and runs
  separate `api`, `analytics-worker`, and `db` services.
- Raw syncs now preserve available Health Connect provenance, including device,
  data origin, recording method, and client-record identity/version.
- `GET /api/v2/analytics/devices` exposes that provenance as an observed device
  catalog with stable IDs, descriptions, recording methods, signal/date
  coverage, record-association fields, and explicit ambiguity markers. It is
  derived from raw metadata so it cannot become stale. Fitbit records that omit
  model/type can still combine multiple physical devices.
- `GET /api/v2/analytics/day?date=YYYY-MM-DD&radius=7` now provides the focused
  day contract (`health-day-v1`), including hourly heart-rate summaries, sleep
  stages, hourly steps, workouts, nearby days, and explicit availability notes.
- Experimental cardiovascular strain is implemented separately from the
  proprietary WHOOP algorithm. It requires credible personal zone calibration
  and adequate heart-rate coverage before publishing a score.
- Provisional Recovery v1 combines sleep, trailing personal RHR/HRV baselines,
  and sleep consistency. It can publish a clearly marked partial score without
  HRV, but its heuristic weights and curves are explicitly pending validation.
- Strain v2.1 permits low-confidence empirical calibration from a substantial
  history whose observed high is credible but below 140 bpm. The response exposes
  that confidence, retains strict daily coverage gates, and does not create
  synthetic strain rows for entirely unobserved dates between samples.
- `GET /api/v2/sync/status` exposes a server-observed upload heartbeat. Its
  120-second active window indicates recent authenticated ingestion, not the
  durable state of the Android background task.
- Android sync now pre-filters impossible timestamps and recursively splits a
  server-rejected batch to isolate a bad record instead of losing the batch.
- The app keeps an advisory per-record-type synced-day map, supports forced
  re-upload/reset, and can display the authenticated server inventory. The map
  is an optimization, not server truth; reinstalling or clearing app data loses it.
- `calculate-database-folder-disk-usage-in-gigabytes.sh` reports decimal GB,
  binary GiB, and exact bytes for the bind-mounted database directory.

### Data-source and Android planning checkpoint (2026-08-29)

- `doc/whoop-health-connect-pixel-watch-4-comparison.md` compares the supplied
  WHOOP ZIP, WHOOP-origin Health Connect records, Fitbit/likely-Pixel records,
  other phone/Google Fit sources, Pixel Watch 4 capabilities, and ingestion gaps.
- Live provenance shows a large Fitbit/Google Health source-only group beginning
  2026-02-05, consistent with the stated Pixel Watch era, but Fitbit omitted the
  physical manufacturer/model. Older Fitbit records likely include the 2025
  Inspire and cannot be split conclusively by package name alone.
- `app/README.md` is the prioritized Android completion roadmap. Its first data
  tasks are HRV, the distinct skin-temperature type, exercise routes with
  explicit consent, and an audit of Pixel/Google Health delivery.
- Sensitive WHOOP source files live under the ignored local directory
  `raw-data/whoop/2026-08-24/`, outside MongoDB's `db/` bind mount. Only
  `raw-data/README.md` is tracked. Never force-add the ZIP or extracted CSVs.

The primary frontend bootstrap contract is:

```http
GET /api/v2/analytics/snapshot
Authorization: Bearer <token>
```

It returns exactly `generatedAt`, `source`, `sleepSessions`, and `analytics`,
matching the reference dashboard's expected snapshot shape.

## Analytics behavior worth preserving

- Sleep recordings with at least 80% overlap relative to the shorter session
  are grouped; the longest is canonical and all device recordings remain
  available for comparison.
- Sleep stages exclude awake/unknown time; sessions without stages use their
  full duration. Naps remain distinct but contribute to daily sleep.
- Interval totals are split across local calendar days and sources are selected
  by coverage, then observation count. Steps are rounded.
- Resting heart rate is a daily median; weight is the latest daily observation.
- Sleep debt uses calendar 7/30/90-day windows. Consistency uses a circular
  14-day prior baseline.
- Healthspan is an experimental estimate, not a medical or literal lifespan
  prediction. Age-based results require `birthDate` in analytics configuration.
- Run identity is algorithm + source + configuration fingerprints, making
  unchanged rebuilds idempotent.

## Current data and runtime state

At the initial analytics handoff, all Compose services were running and healthy
and the worker had backfilled all four accounts. These counts are a historical
snapshot and may change as the phone syncs or deletes records:

| Username | Raw entries | Notable prepared output |
| --- | ---: | --- |
| `lucas` | 89,754 | 381 sleep events, 310 sleep days, 331 healthspan trend days, 79 step days, 114 RHR days, 1 weight measurement |
| `lucasadmin` | 30,737 | 115 sleep events, 96 sleep days, 111 healthspan trend days |
| `Lucas` | 1,510 | 2 step days and 2 total-calorie days |
| empty username | 0 | Valid empty analytics snapshot |

The primary account's completed run reported zero normalization issues. Do not
merge or rename these accounts automatically; `lucas` is currently the account
with the longest and largest raw history.

### Full-history sync checkpoint (2026-08-24)

The replacement/full-history Android sync increased `lucas` from 89,754 to
350,682 raw records (260,928 additional records; roughly 3.9 times the earlier
corpus). The inventory now includes 235,337 heart-rate records containing about
3.94 million valid samples over 203 dates, 597 sleep sessions, 338 exercise
sessions, 153 resting-heart-rate records, 153 respiratory-rate records, and 151
oxygen-saturation records. Coverage begins 2025-03-17 and reaches 2026-08-24.
The completed v7 normalization run reported zero issues.

The prior Strain v1 run published no daily scores solely because its empirical
99.5th-percentile high was 131 bpm, below its hard 140-bpm calibration gate.
This finding motivated Strain v2.1's explicitly low-confidence empirical tier;
it did not justify treating 131 bpm as a measured personal maximum.

The deployed `health-analytics-v8.1` real-data rebuild completed with zero
normalization issues. It produced 143 explicitly partial Recovery scores from
357 sleep dates (no complete score because HRV count remains zero) and 182
publishable Strain scores across 205 local-date entries. Recovery scores ranged
from 28–96 and Strain from 0.61–18.66. These ranges are implementation
diagnostics, not evidence that the heuristic models are personally validated.

The 3.94-million-sample rebuild took about eight minutes and briefly used several
GiB of memory. Add incremental, affected-date analytics processing before treating
continuous high-frequency uploads as operationally cheap; the durable queue is
safe, but full-history work after every debounce is unnecessarily expensive.

An obsolete `_analyticsDaily` collection from an earlier prototype may still
exist in a user database. The production implementation uses underscore-separated
collection names such as `_analytics_daily` and does not read the prototype.
Removing it is optional and should only be done after confirming no old client
uses it.

## Verification already performed

The current image passes 35 tests covering pipeline, Recovery, and strain behavior,
fingerprints, MongoDB idempotency, job revision/lease safety, bearer
authentication, user isolation, endpoint shape, day shaping, sync activity,
configuration validation, daily date ranges, and device-provenance inventory:

```bash
docker exec hcgateway_api sh -lc \
  'TEST_MONGO_URI="$MONGO_URI" python -m unittest discover -s tests -v'
```

A full lifecycle test was also completed:

```bash
docker compose down
docker compose up -d --build
```

The bind-mounted raw database and exact current analytics run survived. Do not
use `docker compose down --volumes` when preservation matters.

Useful checks:

```bash
docker compose ps
docker compose logs -f analytics-worker
curl http://localhost:6644/health
```

## Important MongoDB/kernel caveat

The host kernel is `7.0.2-6-pve`, which is affected by MongoDB
SERVER-121912. New MongoDB images refuse to start, while the previously used
image crashes after about a minute with its default TCMalloc configuration.

Compose therefore temporarily pins the known database image by digest and sets:

```yaml
GLIBC_TUNABLES: glibc.pthread.rseq=1
```

This was observed stable with zero restarts after the change. The correct
long-term operation is to upgrade the host kernel to 7.0.14 or newer, back up
`./db`, upgrade MongoDB to a supported current patch, remove the override, and
repeat the lifecycle and test-suite checks.

## Git checkpoint

The analytics work was committed locally, then the other-device Android sync
commit was fetched from `origin/main` and merged without conflicts. At this
checkpoint local `main` is ahead of `origin/main` by the analytics commits plus
the merge commit; it has not yet been pushed.

Recent commits are:

```text
18e90d3 chore: organize local raw health exports
2a0cfb9 docs(android): add application completion roadmap
c095f49 docs: compare WHOOP and Pixel health data sources
5147ab9 feat: expose Health Connect device provenance
59cfb42 docs: allow scoped health-data analysis
e600686 docs: wrap merged sync and analytics handoff
b622a02 Merge remote-tracking branch 'origin/main'
a97e4bb docs: record analytics v8.1 production checkpoint
e0d02b8 feat: add provisional recovery and strain v2.1
5136e8e feat(android): keep sync moving around bad records
184afeb feat(android): harden Health Connect sync
7091727 docs: add repository agent guidance
8f3f8f6 feat: expose phone sync activity status
9a636b1 chore: add database disk usage report
485a867 feat: add frontend day analytics and strain estimates
90d6d89 fix: stabilize MongoDB on the current host kernel
39912c0 docs: document analytics contracts and operations
9c5d969 feat: expose authenticated frontend analytics APIs
869df93 feat: run durable analytics jobs in a separate worker
1511a7e feat: port health analytics v6 engine to Python
da6f8ff chore: keep local health data and secrets out of git
```

The OpenAPI file at `doc/api-documentation.yml` is currently maintained by
hand; no schema-generation step was found. Keep it synchronized with route and
contract changes.

Local `.env`, `api/.env`, Firebase credentials, Mongo files, and Python caches
are ignored. Never commit or print their secret values.

## Recommended next session

1. Read this file and `doc/frontend-data-model.md`, then run `git status`,
   `docker compose ps`, and the 35-test command above.
2. Set the primary user's real `homeTimeZone`, desired sleep target, and
   optional birth date through `PUT /api/v2/analytics/config`; do not guess
   personal configuration.
3. Validate the merged Android sync behavior on a physical Android 14+ device:
   historical permission, paginated reads, malformed-record isolation, local
   day skipping, forced re-upload/reset, background execution, and inventory UI.
4. Replace full-history analytics after every upload with incremental processing
   of affected dates plus the bounded prior windows needed by Recovery, sleep
   debt, consistency, and healthspan. Preserve immutable run/pointer safety.
5. Add Health Connect `heartRateVariabilityRmssd` to the Android permission/read
   list and verify its actual payload shape. Until HRV arrives, Recovery must
   remain visibly `partial`; do not promote the current heuristic to validated.
6. Finish the in-progress frontend migration in
   `/root/health-connect-dashboard-for-fitbit`: use `/api/v2/analytics/day` for
   the day screen, treat the backend timezone and sleep end-date assignment as
   authoritative, render metric statuses/notes, and use `/api/v2/sync/status`
   for the ingestion indicator. That repository was intentionally read-only in
   the backend task, so get explicit authorization before changing it.
7. Once the frontend works end-to-end, consider exposing paginated prepared
   sleep events/device comparisons and expanding the Python port to additional
   raw signals. Keep raw records as the immutable source of truth.

Before any model or UI describes a healthspan value, label it experimental and
non-clinical. There is not enough information here to claim an actual predicted
lifespan or medical diagnosis.
