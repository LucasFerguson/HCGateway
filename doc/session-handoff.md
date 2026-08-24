# Session handoff: analytics backend

Last updated: 2026-08-24

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
- `calculate-database-folder-disk-usage-in-gigabytes.sh` reports decimal GB,
  binary GiB, and exact bytes for the bind-mounted database directory.

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

The current image passes 34 tests covering pipeline, Recovery, and strain behavior,
fingerprints, MongoDB idempotency, job revision/lease safety, bearer
authentication, user isolation, endpoint shape, day shaping, sync activity,
configuration validation, and daily date ranges:

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

The implementation is split into these local commits and was not pushed during
the session:

```text
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
   `docker compose ps`, and the 30-test command above.
2. Set the primary user's real `homeTimeZone`, desired sleep target, and
   optional birth date through `PUT /api/v2/analytics/config`; do not guess
   personal configuration.
3. Finish the in-progress frontend migration in
   `/root/health-connect-dashboard-for-fitbit`: use `/api/v2/analytics/day` for
   the day screen, treat the backend timezone and sleep end-date assignment as
   authoritative, render metric statuses/notes, and use `/api/v2/sync/status`
   for the ingestion indicator. That repository was intentionally read-only in
   the backend task, so get explicit authorization before changing it.
4. Add historical Health Connect ingestion from the mobile app so the `lucas`
   account receives the full device history; the worker will rebuild
   automatically after each sync.
5. Once the frontend works end-to-end, consider exposing paginated prepared
   sleep events/device comparisons and expanding the Python port to additional
   raw signals. Keep raw records as the immutable source of truth.

Before any model or UI describes a healthspan value, label it experimental and
non-clinical. There is not enough information here to claim an actual predicted
lifespan or medical diagnosis.
