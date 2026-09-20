# Session handoff: analytics backend

Last updated: 2026-09-20

## 2026-09-20 — Calendar-worker reconciliation closes the permanent-failure gap

A real production gap: once a FluidCalendar delivery exhausted its retries
(`permanent_failure`), nothing automatically retried it -- only a change to
the underlying prepared event's rendered payload re-triggered delivery, and
correct content never changes. This happened for real: FluidCalendar
returned HTTP 500 for about 16 hours on 2026-09-19, and 9 sleep events (wake
dates 2026-09-12 through 2026-09-19, one date with two events) landed in
`permanent_failure` in `_calendar_sleep_deliveries`
(`hcgateway_67fe85bc02e55009f847ff7e`). The worker's "recent scan completed"
logs kept reporting success the entire time because they measure queueing,
not delivery outcome -- nobody noticed until this was investigated directly
against the live ledger.

Added a periodic reconciliation pass to `calendar_worker.py`/`calendar.py`
that checks FluidCalendar's own event list (not Google Calendar directly)
for a bounded recent window and re-queues anything expected but not found
there. Full design and exact matching logic are documented in
`doc/calendar-worker-plan.md`'s new "FluidCalendar-side reconciliation"
section; summary:

- New `reconcile_window`/`maybe_run_reconciliation` in `calendar_worker.py`,
  and new `calendar.py` primitives: `FluidCalendarClient.list_events`
  (`GET /api/events?start=&end=` -- the documented windowed
  `GET /api/calendar/events` 404s against the deployed FluidCalendar
  version, discovered live while building this), `requeue_sleep_delivery`,
  and a `claim_reconciliation`/`complete_reconciliation`/
  `fail_reconciliation` durable lease trio (new `_calendar_sleep_reconciliations`
  collection) mirroring the existing backfill cursor's shape so the interval
  survives worker restarts and two instances can't double-run it.
- Runs on its own interval, separate from the 5-minute recent-scan poll:
  `CALENDAR_SLEEP_RECONCILE_INTERVAL_SECONDS` (default 21600s/6h),
  `CALENDAR_SLEEP_RECONCILE_WINDOW_DAYS` (default 7),
  `CALENDAR_SLEEP_RECONCILE_ENABLED` (default true). Added to
  `docker-compose.yml` and `.env.example`.
- Matching: a `delivered` row is confirmed by its stored `remoteEventId`
  against the fetched list (a `cancelled` remote event does not count as
  present); a `permanent_failure` row has no `remoteEventId`, so it falls
  back to FluidCalendar's own strict `skipIfExists` fields (title/start/end/
  description, recomputed from the current prepared event) in case the
  create actually succeeded server-side but the ledger update was lost --
  this path marks it found rather than requeuing a duplicate. The list
  endpoint is not feed-scoped server-side, so results are filtered by
  `feedId` client-side before either comparison. A row whose event cannot be
  confirmed either way is reset via `requeue_sleep_delivery`: `attempts` to
  0, `state` to `pending`, `remoteEventId`/`externalEventId` cleared (a
  confirmed-missing event cannot be safely `PATCH`ed, so the next attempt
  must `create`). Reconciliation itself never calls a FluidCalendar write
  endpoint; redelivery happens through the normal `deliver_one` path only.
  A truncated/paginated listing is treated as inconclusive and raised as a
  retryable error rather than risking a false "missing" that would
  duplicate a genuinely delivered event still sitting on a later page.
- 15 new tests (100 total, up from 85): 2 in `test_calendar.py`
  (`list_events`), 13 in `test_calendar_mongo.py` covering
  `requeue_sleep_delivery`'s exact-prior-state guard, the reconciliation
  lease's immediate-once-then-rate-limited behavior (mirroring backfill's
  existing test), and a dedicated
  `test_delivered_event_present_on_fluidcalendar_is_left_alone` safety test
  plus cases for a cancelled remote event, a cross-feed id collision, a
  content-matched orphaned success, and truncated-listing refusal. All use
  a fake HTTP client per this project's existing pattern -- no live
  FluidCalendar write call is ever made by a test.
- Verified live end-to-end after `./redeploy-docker-containers.sh
  calendar-worker analytics-worker` (both share the `hcgateway-api:local`
  image). On the first post-deploy cycle, reconciliation correctly found
  and requeued the 7 of 9 stuck rows that fall inside the default 7-day
  window (wake dates 2026-09-14 through 2026-09-19); the 2 oldest
  (2026-09-12, 2026-09-13) correctly remained untouched since they are
  outside that window -- a real illustration that the reconciliation window
  and the retention of older `permanent_failure` rows need to stay
  consistent, noted below as follow-up. The 7 requeued rows were then
  picked up by the normal delivery loop and attempted again, but
  FluidCalendar's write path (`POST /api/events`) was still returning HTTP
  500 at verification time even though every read endpoint checked
  (`/api/events` GET, `/api/feeds`, `/api/stats/live`) returned 200 --
  i.e., reconciliation correctly did its job (detect + requeue), and the
  remaining 500s are a live external-service condition outside this
  change's scope, left to the normal retry loop rather than forced
  manually per the task's constraint against making write calls outside
  the worker's own tested path.

Follow-up worth doing next session: decide whether
`CALENDAR_SLEEP_RECONCILE_WINDOW_DAYS` should default to something wider
than the delivery lookback (or whether `permanent_failure` rows outside the
window should be swept some other way), since the 2026-09-12/09-13 rows
found during this incident will not be reconciled again until they either
scroll back into a widened window or someone changes the config -- they are
not silently lost (still visible as `permanent_failure` in the ledger), but
also not self-healing under the current default window. Also confirm once
FluidCalendar's write path recovers that the 7 requeued rows actually reach
`delivered`, and re-check the 2 untouched older rows with a temporarily
widened `CALENDAR_SLEEP_RECONCILE_WINDOW_DAYS` if they still need clearing.

## 2026-09-20 — Persisted per-workout strain detail (health-analytics-v8.4)

Closed the `StrainSummary.workouts` GraphQL gap: `store.py`'s
`_daily_documents` now writes a `strainWorkouts` field into each
`_analytics_daily` date document (full per-workout strain: `loadMinutes`,
`zoneMinutes`, `timeline`, `quality`), grouped via a new
`_strain_workouts_by_date` helper onto every local date a workout's start or
end touches - the same membership rule `day_dashboard.py`'s
`Day.timeline.workouts` already used for its own reduced
`strainContribution`/`strainQuality` projection. `graphql-api`'s
`strainSummary` resolver now reads and dedupes across dates by workout id.
6 new Python unit tests in `api/tests/test_store_daily_documents.py`
(85 total, up from 79).

**A real lesson from verifying this live**: adding a field to what
`save_analytics` persists is a genuine schema change, but `save_analytics`'s
idempotency check (`if current.runId == run_id: return "unchanged"`) is keyed
only on data + configuration fingerprints, never on pipeline/store *code*.
A manually triggered `POST /api/v2/analytics/rebuild` against unchanged
source data and config silently no-ops even when the code that would
process it has changed - there is no feedback that nothing happened besides
the response shape being identical to a real rebuild. This was caught only
by directly inspecting `_analytics_daily` documents in Mongo after the
"rebuild" claimed success and finding the new field simply absent.

The correct fix, and the project's existing sanctioned mechanism for
exactly this: bump `ALGORITHM_VERSION` (`api/analytics_engine/pipeline.py`,
now `health-analytics-v8.4`, was `v8.3`). `analytics-worker`'s startup check
(`worker.py`, queues an `algorithm_upgrade` job for every user whose current
run predates the running algorithm version) then reprocesses every real
account automatically on the next container start - verified across all 4
production accounts, no manual per-account nudging needed. The primary
account's full reprocess took about 17 minutes (up from an ~8-minute August
benchmark - consistent with continued data growth since then, not a
performance regression in the new code; confirmed via `docker stats` and
process state during the wait rather than assumed). Live-verified
afterward: all 474 real exercise sessions appear in
`viewer.analytics.strain.workouts` via GraphQL with zero duplicates,
confirming the midnight-spanning dedupe logic is correct against real data.

Updated every doc that named the algorithm version explicitly:
`AGENTS.md`, `README.md`, `doc/frontend-data-model.md`,
`doc/graphql-api.md`'s known-gaps entry (now marked fixed with the
reasoning). `doc/graphql-schema-design.md`'s deferred
`HeartRateData.series(resolution:)` gap remains deliberately unaddressed -
real new aggregation logic, out of scope for this fix.

Added `./redeploy-docker-containers.sh [service...]` at the repo root:
rebuilds and recreates (`docker compose build` + `up -d`) any or all
services in one command. Exists because `docker compose restart` reuses the
running container's existing image/environment and does **not** pick up a
rebuilt image or an edited `.env` - this has caused confusion more than
once this week (the GraphQL Sandbox landing-page fix earlier today, and
almost this same strain-workouts verification, both required remembering
the distinction manually). Referenced in `AGENTS.md`'s verification section
now so it doesn't need rediscovering next session.

## 2026-09-20 — GraphQL API structured logging

`graphql-api/src/logging.ts` adds structured JSON-line request logging
(stdout, one line per operation) - previously the service had no logging
beyond a startup message and a fatal-crash `console.error`. Covers:

- Every completed GraphQL request: operation name, duration, HTTP status,
  the authenticated `userId` (never the token), and error count/codes.
- Auth failures (missing/invalid/expired token) as a separate
  `graphql_auth_failure` event - these were found, live, to log **nothing at
  all** under the original implementation, because Apollo's request-lifecycle
  plugin hooks never fire when the `context()` callback itself throws (which
  is how auth rejection works here). Logged directly from the `context()`
  catch block in `index.ts` instead.
- Depth/alias/complexity validation-rule rejections: confirmed live that
  validation-stage failures *do* reach the normal plugin lifecycle (a
  malformed-field test query logged correctly as `GRAPHQL_VALIDATION_FAILED`/
  400), so the same mechanism covers the safety limiters. `depthLimitRule`
  in `security/validationRules.ts` was patched to attach a proper
  `QUERY_DEPTH_LIMIT_EXCEEDED` extensions code, since the underlying
  `graphql-depth-limit` package's own errors carry no code at all (unlike
  the alias/complexity rules, which already did).

Never logs: the bearer token, query variables, resolver return values, or
raw health values - matching the existing rule already followed elsewhere in
this service (`security/auth.ts`, `formatError` in `index.ts`).

Deliberately deferred (asked about, not selected this round): Sentry/error
tracking (`@sentry/node`, to match the Flask API's existing `sentry-sdk`) and
per-field resolver timing. Revisit if/when real traffic makes either useful.

Verified live end-to-end after a full `docker compose build graphql-api` +
`docker compose up -d graphql-api` (a plain `restart` does not pick up a
rebuilt image or edited `.env` - this has bitten this project more than once
this week; always recreate, not restart, after an image or env change).

## 2026-09-19 — Account cleanup and config

- Set `lucas`'s `birthDate` to `2003-09-16` via `PUT /api/v2/analytics/config`
  (verified afterward through the new GraphQL API too). This queued a normal
  analytics rebuild job (revision 42387) so healthspan calculations start
  using the real configured age; no manual pipeline rerun needed.
- Deleted the `Lucas` (capital L) account entirely: user `67ff4a5402e55009f847ff7f`,
  token expired 2025-04-17 (stale, unused for well over a year), 1,510 raw
  records. Removed the user record from `hcgateway.users`, its
  `analytics_jobs` entry, and dropped the whole `hcgateway_67ff4a5402e55009f847ff7f`
  database (raw collections, prepared analytics, and the encrypted
  `__fernet_backup_v1__*` backups from the September plaintext-BSON
  migration). This incidentally also removed a leftover obsolete
  `_analyticsDaily` (no underscore separator) prototype collection that an
  earlier handoff note had flagged as safe to remove once confirmed unused.
  `lucasadmin` (30,737 records, real multi-month sleep history) was
  explicitly evaluated and kept - do not delete it without a separate,
  explicit decision; it is real data, not a test fixture.
- The 7 leftover `hcgateway_test-api-*` databases from the 2026-09-19
  low-disk test-run failure (see below) were also dropped this session,
  by exact prefix match, after their orphaned users were already deleted
  earlier. None remain.

## 2026-09-19 — Milestone: read-only GraphQL API implemented

`graphql-api/` (Node.js + TypeScript, Apollo Server) now exists as a fifth
Compose service, port 6645, alongside `api`, `analytics-worker`,
`calendar-worker`, and `db`. It is a thin, read-only passthrough over the
same MongoDB instance - own `MONGO_URI`, no HTTP hop through Flask, no
mutations, no health-domain logic reimplemented in Node. Auth independently
validates the bearer token against `hcgateway.users` (mirroring Flask's
`before_request` hook) before any resolver runs; every field is scoped to
`hcgateway_<userId>` derived from that token, never from a query argument.
See `doc/graphql-api.md` for the endpoint, root schema shape, and safety
model (resolver timeout, response-size cap, depth/alias/complexity limits -
all env-configurable), and `doc/graphql-schema-design.md`/
`doc/graphql-read-api-audit.md` for the design this was built from.

Verified end-to-end against the real primary account (`lucas`) through
`docker compose up -d --build`: all five services report healthy, an
unauthenticated query is rejected before any resolver executes, a
mismatched user/token cannot see another account's data, and missing health
values consistently render as an explicit `MetricStatus` with `value: null`
- never a numeric zero. The existing 79-test Python suite still passes
unchanged; a new, separate 12-test Vitest suite
(`graphql-api/src/__tests__/`, run via `npm test` inside `graphql-api/`,
using `mongodb-memory-server` rather than the live database) covers tenant
isolation, missing-data representation, and the depth/alias/complexity
limiter rejecting a pathological query.

Real data diverged from `doc/graphql-schema-design.md`'s hypothesis in a
few places, each handled by following the real shape rather than forcing it
into the doc (per the doc's own "how literally to take this" instructions):
the `MetricStatus` enum needed two more real values (`UNAVAILABLE`,
`SAMPLE_TIME_ONLY`) beyond the six originally proposed; `Analytics.day()`
for a date with no prepared data now synthesizes the same fully-shaped
"empty day" `day_dashboard.py`'s `empty_day()` produces, rather than
returning `null`; and `@defer` is declared on fragments/inline-fragments
(its only valid SDL locations) rather than on field definitions as the
design doc's SDL literally showed, since `@defer` is a query-side directive
in the GraphQL spec, not a schema one.

Two things were deliberately left as gaps rather than worked around: (1)
`@defer`/incremental delivery is schema-ready but not yet functionally
streaming, because no released `@apollo/server` version (checked stable,
rc, and alpha/next-v3 tags) has a peer dependency range that includes
graphql-js 17's now-stable incremental execution - it's pinned to
`graphql ^16.x` everywhere, and Apollo's own polyfill only activates real
streaming on the exact alpha build `17.0.0-alpha.9`. (2)
`StrainSummary.workouts` returns `[]` because per-workout strain detail is
computed in-memory during a pipeline run but never persisted to any
`_analytics_*` collection - there is nothing for a read-only service to
read back without a Python-side storage change first.

## 2026-09-19 — Milestone: the database went unencrypted

This was a major structural turning point for the project. Every health record
HCGateway stores moved off per-record Fernet encryption onto plain BSON,
across all production data, with zero data loss and zero downtime beyond the
migration window itself. The work spanned this session and a prior one, and
the maintenance window (services stopped, `hcgateway.maintenance` marked
active) ran from 2026-09-19T18:01 UTC to 2026-09-19T20:56 UTC — just under
three hours of actual database maintenance, inside a same-day session that
ran over four hours end to end once GraphQL audit work, design discussion,
migration engineering, and post-cutover verification are included.

Some numbers from the day:

- **1,101,965 documents** migrated to plaintext BSON across 5 production
  databases, 0 lost, 0 corrupted.
- **610,787 heart-rate records** in the primary account alone — the single
  largest collection moved.
- **93 collections** cut over live (raw source collections plus every
  `_analytics_*` prepared collection), each individually verified before its
  encrypted original was renamed to a `__fernet_backup_v1__` backup rather
  than deleted.
- **79 tests passing** afterward (up from 71 at the start of the day) in
  **~2.3 seconds** for the full suite.
- Fernet decryption had been consuming **60–78% of total backend read time**
  on the largest legacy raw-record endpoints — up to **5.3 of 8.8 seconds**
  for a single 138,637-record read. That cost is now gone for every future
  read of this data.
- A first cutover attempt using full cryptographic re-validation (rehashing
  every nested sample/stage per document before each rename) was on pace to
  take **30+ minutes just for the primary account's two largest collections**;
  switching to a fast, count-verified cutover path finished all 5 databases in
  **under 3 minutes**.
- This is also the day the project's read API audit began
  (`doc/graphql-read-api-audit.md`), setting up the next milestone: an
  additive, read-only GraphQL layer over prepared analytics.

### Plaintext BSON cutover completed (2026-09-19)

- The Fernet-encrypted-per-record storage migration (`api/migrations/plaintext_bson.py`)
  was completed for all 5 production databases. Every raw source collection and
  every `_analytics_*` prepared collection now stores `data` as plain BSON
  (`storageFormat: "plain-bson-v1"`) instead of an encrypted JSON string. Raw
  collections also gained typed `startInstant`/`endInstant` BSON datetime
  fields alongside their original string `start`/`end`, enabling native Mongo
  range queries and projections.
- Encrypted originals were preserved as `__fernet_backup_v1__<name>` collections
  in each production database, not deleted. `rollback` in the migration script
  can restore them if ever needed. Do not drop the backups without explicit
  authorization.
- Safety approach actually used: shadow copies were verified by exact
  document-count match per collection (not the migration script's slower
  full cryptographic manifest hash) before cutover, at the user's explicit
  direction to trade paranoia for speed once counts already proved equality.
  The migration script gained a `--fast` cutover flag for this (trusts an
  exact count match instead of re-hashing every nested sample/stage value);
  the original full-manifest path remains available via `validate` or cutover
  without `--fast`.
- `analytics_engine/crypto.py`, `repository.py`, `store.py`, and
  `apiVersions/v2/routes.py` all read via `decode_stored_json`, which accepts
  either a plain dict/list (new format) or a legacy encrypted string
  (old format) — this compatibility path is intentional and covered by tests,
  since legacy-format documents may still exist in some collections that were
  never migrated, and to protect any future document written by older code.
- Two orphaned test users (`test_plaintext_migration_*`, 67-char IDs from a
  superseded version of the migration test file, `$argon2id$test-hash`
  password, no associated database) were found and deleted after they crashed
  the analytics worker: `"hcgateway_" + user_id` exceeded MongoDB's 63-character
  database-name limit. Their orphaned `analytics_jobs` queue entries were also
  removed. If a similarly malformed test artifact appears again, check
  `hcgateway.users` for `_id` values whose derived database name exceeds 63
  characters — the worker crashes the whole process on this, not just the one
  job, because it happens in `run()`'s user-iteration loop
  (`api/analytics_engine/worker.py:55`), before per-job exception handling.
- All 4 Compose services were rebuilt and are healthy. The complete test suite
  now has 79 tests (up from 71) and passes in ~2.3 seconds. Live spot checks
  against the primary account's `/analytics/status`, `/analytics/day`, and
  `/analytics/snapshot` all returned correct data with response times at or
  better than the pre-migration baseline.
- Passwords remain Argon2-hashed; only health/analytics payload encryption was
  removed. API bearer-token authentication is unchanged.
- Next step from here is the GraphQL design/implementation work this migration
  was a prerequisite for — see the planning checkpoint below and
  `doc/graphql-read-api-audit.md`. No GraphQL schema or server code exists yet.

### GraphQL read-API planning checkpoint (2026-09-19)

- `doc/graphql-read-api-audit.md` now records the pre-migration baseline: nine
  active authenticated database-backed REST read operations (eight GET routes
  plus the legacy POST-based raw fetch), with `/health` tracked separately.
- Aggregate-only local measurements found that the six legacy raw reads used by
  the home page transfer about 87.64 MB, while the current prepared snapshot is
  about 10.17 MB and the focused-day response is about 34 KB. No tokens, record
  identifiers, or health values were printed or added to documentation.
- No GraphQL implementation or schema decision has been made. The intended
  design session will keep writes/authentication/commands in REST and evaluate
  a read-only GraphQL layer over bounded prepared analytics. The audit
  explicitly recommends against reproducing the arbitrary, unbounded raw fetch
  interface in GraphQL.

### Session-closing audit (2026-09-19)

- No source commits were added after the Android `2.2.2` release metadata
  checkpoint on 2026-09-02. The source baseline remains `55a8c54`; subsequent
  local commits are documentation-only and may not yet be pushed.
- All four Compose services are running; the API and MongoDB report healthy.
  Analytics continued completing runs through 2026-09-19, and the calendar
  worker continues its five-minute recent-window scans.
- `node --check app/App.js` passes. The first 71-test Python run failed during
  setup because the root filesystem was 99% full: about 468 MB (447 MiB)
  remained, below MongoDB's 500 MiB minimum for creating indexes. Proxmox VM
  storage was then expanded; the guest now sees about 47.4 GB total with
  15.7 GB available (66% used). The complete 71-test suite subsequently passed
  in 1.805 seconds.
- The failed API-test setup initially left 14 UUID-named temporary `test-api-*`
  users and seven `hcgateway_test-api-*` databases because `setUp` failed before
  teardown. The 14 users were deleted on 2026-09-19 after exact ID/username
  validation. The seven test databases remain intentionally untouched because
  only user deletion was authorized. They contain test fixtures, not health
  data; re-check the exact prefix before any later targeted removal.
- The local MongoDB bind mount was about 4.645 GB at this audit. The VM expansion
  restored healthy headroom without deleting MongoDB data, Docker assets, or
  raw health exports. Capacity monitoring is still recommended so the database
  cannot silently approach its write-safety threshold again.
- `.vscode/` and `doc/external/` remain untracked. The latter contains copied
  FluidCalendar reference material used during integration work. They were not
  reviewed for redistribution or added to Git; decide their disposition
  explicitly rather than committing them incidentally.

### Android session and date-range checkpoint (2026-09-02)

- The Android/Expo application release is now `2.2.2` with Android
  `versionCode` 2, synchronized across native, Expo, and package metadata.
- Android access and refresh tokens are now stored with Expo SecureStore
  (Android Keystore-backed) instead of AsyncStorage. Existing installations
  migrate their saved plaintext credentials once, and logout clears both the
  secure values and any legacy copies.
- App startup restores the saved access token and proactively uses the refresh
  token before deciding whether login is required. Temporary network failures
  retain the local session; an explicit invalid-token response clears it.
- Custom sync ranges now use separate single-date start and end pickers. The
  end date cannot precede the start, and sync includes the selected end day.
- This change adds `expo-secure-store`; rebuild the Android application before
  on-device verification because it includes a native module.

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
  the current prepared algorithm is `health-analytics-v8.3`.
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
  separate `api`, `analytics-worker`, `calendar-worker`, and `db` services.
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

### Sleep preparation cleanup checkpoint (2026-08-29)

- `health-analytics-v8.2` extracts shared sleep/time preparation primitives so
  the day API and future calendar worker consume the same prepared durations,
  stage totals, quality flags, roles, and reconciliation decision metadata.
- Overlapping recordings are reconciled before wake-date assignment. Among
  recordings at least 98% as long as the longest, a valid detailed-stage
  timeline is preferred without hardcoding a device vendor; duration and stable
  identifiers break remaining ties. A live read-only audit found this would
  replace 30 near-equal generic-stage primaries, usually sacrificing about three
  minutes of window length for substantially richer stage data.
- Daily headline sleep and stage totals now both cover every event, including
  supplemental sleep, while the single headline window is explicitly marked as
  the main event. Prepared events use `main`/`supplemental`, not an inferred nap
  label.
- HRV is now included in the source fingerprint. Previously, an HRV-only upload
  could calculate changed Recovery output but reuse the old run ID and be
  discarded as unchanged.
- Worker startup queues an `algorithm_upgrade` job when a user's current
  prepared run was produced by an older algorithm version.
- Analytics retry attempts are now scoped to the requested revision, and
  completion/failure updates require the claiming worker and revision. A stale
  lease holder can no longer overwrite a newer queued revision.
- `doc/calendar-worker-plan.md` records the thin delivery-worker design. The
  calendar integration reuses prepared sleep events rather than repeating
  reconciliation or stage calculations.

### Timezone consolidation checkpoint (2026-08-29)

- `health-analytics-v8.3` makes the configured IANA home timezone authoritative
  for analytics semantics while retaining canonical UTC instants. The primary
  account is configured as `America/Chicago`.
- Live data contained 728 UTC `Z` sleep-session windows and 62,454 UTC `Z` stage
  timestamps, with no sleep source offset. Exercise records do contain source
  offsets, now retained as provenance without overriding the home-zone policy.
- Shared helpers now own strict instant parsing, UTC/local rendering, local date
  keys, local-today calculation, and DST-aware local-midnight splitting. Naive
  uploads are rejected atomically instead of being silently interpreted as UTC.
- Prepared analytics expose `timeZone`; sleep events additionally expose
  offset-aware `localStartAt`/`localEndAt`. Frontends must format UTC instants in
  that supplied zone, never the browser zone.
- Chicago spring/fall transitions are tested as 23/25 elapsed-hour local days.
  Hourly day arrays remain 24 wall-clock-number buckets and merge the repeated
  fall-back hour; an offset/fold-aware timeline is future contract work.

### FluidCalendar worker checkpoint (2026-08-29)

- `calendar-worker` is a separate Compose service using the same local image and
  analytics package as the API and analytics worker. It can be stopped without
  interrupting ingestion or analytics.
- Calendar presentation consumes the atomically selected prepared sleep events,
  including supplemental sleep and naps. It posts canonical UTC instants and
  uses FluidCalendar's exact-match `skipIfExists` behavior plus a durable local
  ledger for retries and remote identifiers.
- The destination feed and HCGateway user are configurable. The supplied Lucas
  Calendar Private feed is the default, while the FluidCalendar base URL and
  user ID are required deployment values.
- The initial window is seven local wake dates: today plus the preceding six.
  Normal polling handles newly prepared events. Historical backfill is disabled
  by default and, when enabled, advances backward in configurable date batches
  no more frequently than the configured backfill interval.
- Store `FLUIDCALENDAR_API_KEY` only in the ignored root `.env`. Compose injects
  it only into `calendar-worker`; never place the key in `api/.env`, source,
  prepared analytics, or logs.
- The configured FluidCalendar origin and Google-backed Lucas Calendar Private
  feed were authenticated before writing. The initial 2026-08-23 through
  2026-08-29 Chicago wake-date scan found seven prepared events, including one
  supplemental session. All seven were delivered successfully and the ledger
  retains both FluidCalendar and Google external event IDs.
- An immediate repeat scan claimed zero deliveries, confirming local
  idempotency. The long-running `hcgateway_calendar_worker` container is active;
  historical backfill remains explicitly disabled.

## Analytics behavior worth preserving

- Sleep recordings with at least 80% overlap relative to the shorter session
  are grouped before wake-date assignment. Near-longest candidates may win on
  validated stage quality under the documented 98% duration floor; all device
  recordings and selection reasons remain available for comparison.
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

The current image passes 71 tests covering pipeline, Recovery, and strain behavior,
fingerprints (including HRV), sleep quality selection and cross-date
reconciliation, prepared sleep reads, MongoDB idempotency, stale-worker and
per-revision retry safety, deterministic calendar rendering, FluidCalendar HTTP
classification, durable delivery leases/retries, backward backfill cursors,
calendar-worker orchestration, bearer authentication, user isolation, endpoint
shape, day shaping, sync activity, configuration validation, daily date ranges,
and device-provenance inventory:

```bash
docker exec hcgateway_api sh -lc \
  'TEST_MONGO_URI="$MONGO_URI" python -m unittest discover -s tests -v'
```

After the Proxmox VM disk expansion on 2026-09-19, the full suite passed again
in 1.805 seconds. The earlier capacity failure and its leftover test fixtures
are documented at the top of this file.

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
docker compose logs -f calendar-worker
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

The analytics work and subsequent Android/calendar changes through `55a8c54`
are committed and present on `origin/main`. Later session-closing documentation
commits are local; use `git status --branch` to confirm whether they were pushed.

Recent commits are:

```text
7a892cd docs: close September session with operational audit
55a8c54 chore(android): bump app version to 2.2.2
2a6d483 feat(android): persist sessions and fix date selection
f9c6773 add FluidCalendar sleep export worker
0bcfcd3 refactor sleep analytics reconciliation and time handling
5dfbc88 docs: note raw exports may be unused
764730a docs: refresh session handoff checkpoint
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

1. Read this file and `doc/frontend-data-model.md`, then run `git status` and
   `docker compose ps`.
2. Decide whether to remove the seven remaining `hcgateway_test-api-*`
   databases from the failed 2026-09-19 test setup. Re-check the exact prefix
   first and do not use a broad database deletion pattern. The leaked users are
   already gone.
3. Rebuild Android because `expo-secure-store` is a native dependency, then
   validate upgrade migration from plaintext credentials, cold-start refresh
   after access-token expiry, logout clearing, temporary-network behavior, and
   inclusive custom start/end dates on a physical device.
4. Set the primary user's real `homeTimeZone`, desired sleep target, and
   optional birth date through `PUT /api/v2/analytics/config`; do not guess
   personal configuration.
5. Validate the merged Android sync behavior on a physical Android 14+ device:
   historical permission, paginated reads, malformed-record isolation, local
   day skipping, forced re-upload/reset, background execution, and inventory UI.
6. Replace full-history analytics after every upload with incremental processing
   of affected dates plus the bounded prior windows needed by Recovery, sleep
   debt, consistency, and healthspan. Preserve immutable run/pointer safety.
7. Add Health Connect `heartRateVariabilityRmssd` to the Android permission/read
   list and verify its actual payload shape. Until HRV arrives, Recovery must
   remain visibly `partial`; do not promote the current heuristic to validated.
8. Finish the in-progress frontend migration in
   `/root/health-connect-dashboard-for-fitbit`: use `/api/v2/analytics/day` for
   the day screen, treat the backend timezone and sleep end-date assignment as
   authoritative, render metric statuses/notes, and use `/api/v2/sync/status`
   for the ingestion indicator. That repository was intentionally read-only in
   the backend task, so get explicit authorization before changing it.
9. Once the frontend works end-to-end, consider exposing paginated prepared
   sleep events/device comparisons and expanding the Python port to additional
   raw signals. Keep raw records as the immutable source of truth.
10. Observe ongoing recent-window FluidCalendar delivery. When ready to export
   older history, set `CALENDAR_SLEEP_BACKFILL_ENABLED=true` and monitor one
   bounded backward batch before leaving gradual backfill enabled.

Before any model or UI describes a healthspan value, label it experimental and
non-clinical. There is not enough information here to claim an actual predicted
lifespan or medical diagnosis.
