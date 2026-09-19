# REST read API baseline before GraphQL

Status: design baseline; no GraphQL implementation exists yet  
Audited: 2026-09-19  
Runtime scope: the enabled Flask application in `api/main.py` and
`api/apiVersions/v2/routes.py`

## Purpose

This document records the read API before an additive GraphQL migration. It is
the comparison point for later claims about endpoint consolidation, payload
reduction, and latency improvement.

The intended boundary is:

- existing writes, authentication, commands, and health checks remain REST;
- authenticated reads used by dashboards become candidates for a read-only
  GraphQL API;
- raw encrypted Health Connect records remain the source of truth;
- GraphQL reads prepared analytics or bounded metadata and never grants direct
  database access;
- missing health metrics remain `null`/missing with their status and note,
  never numeric zero;
- Recovery and strain remain provisional, non-clinical models with their
  version, quality, and limitations exposed.

## Headline baseline

The active application has **9 authenticated database-backed REST read
operations**:

- 8 authenticated `GET` operations for prepared analytics, configuration,
  provenance, inventory, and status;
- 1 legacy `POST /api/v2/fetch/{method}` operation that behaves as an
  unbounded raw-data query.

`GET /health` is a tenth read-only HTTP operation, but it is public liveness
infrastructure, does not access MongoDB, and should remain REST. Flask-generated
`HEAD`/`OPTIONS`, the static route, and the disabled v1 blueprint are not
counted.

Two endpoint-reduction claims are therefore possible, but neither should be
made until the corresponding routes are actually retired:

1. Consolidating the 8 dashboard/support reads behind `/graphql` changes that
   surface from 8 REST route-methods to 1 GraphQL endpoint: **7 fewer endpoints
   (87.5% reduction)**.
2. If the raw fetch operation is ultimately retired as well, the complete
   authenticated data-read surface changes from 9 REST route-methods to 1:
   **8 fewer endpoints (88.9% reduction)**.

During compatibility migration, the accurate wording is “the frontend moved
from N REST read operations to one GraphQL transport,” not “N endpoints were
removed.”

## Current authentication and tenant boundary

All v2 reads use the same bearer-token hook. It looks up the token in
`hcgateway.users`, rejects missing, invalid, or expired credentials, and fixes
`g.user` for the request. Data endpoints then derive the database name as
`hcgateway_<authenticated-user-id>`.

The future GraphQL context must preserve this exact ownership boundary:

- authenticate before executing any resolver;
- derive the user and database exclusively from the bearer token;
- never accept a user ID or database name as a query argument;
- pin one prepared `runId` for the whole GraphQL operation so an atomic worker
  update cannot mix two analytics runs in one response.

## Enabled read-operation inventory

| Current operation | Data source and behavior | Bounds and cache behavior | Current consumer/tests | Migration disposition |
| --- | --- | --- | --- | --- |
| `GET /health` | No database access. Returns `{ "status": "ok" }` for Compose and external liveness checks. | Fixed response; no explicit cache headers. | Compose healthcheck; no direct test. | **Keep REST permanently.** It is infrastructure, not application data. |
| `POST /api/v2/fetch/{method}` | Looks up the authenticated user, derives the Fernet key, opens the named collection in the user's database, passes `queries` directly to `collection.find`, decrypts every matching `data` field, and returns storage envelopes plus raw values. | No record-type allowlist, projection, date requirement, ordering, cursor, page size, result limit, response-size limit, or cache headers. The collection name and Mongo filter are caller controlled within the user's database. | The legacy frontend calls it six times for the home page. No API tests were found. | **Do not reproduce this generic interface in GraphQL.** Keep temporarily for compatibility, then deprecate. If raw access remains a real requirement, design a separate typed, allowlisted, date-bounded, paginated interface. |
| `GET /api/v2/analytics/inventory` | Scans every non-underscore raw collection. For each signal it counts documents, groups by source package, and finds first/last timestamps without decrypting health values. | Output is bounded by signal/source cardinality, but database work is unbounded and recomputed on every call. No explicit cache headers. | Android inventory UI. OpenAPI description exists; no API test. | GraphQL candidate under a source/provenance area. Materialize or cache it by source fingerprint rather than recomputing it for ordinary dashboard renders. |
| `GET /api/v2/analytics/devices` | Scans every non-underscore raw collection and groups provenance by source, device metadata, and recording method. Returns observed device identities, ambiguity markers, signal counts, and coverage. | Output is usually small; raw aggregation work is unbounded and recomputed. No pagination or explicit cache headers. | Provenance API test covers identity semantics; Android consumption is planned. | GraphQL candidate. Preserve identity-quality warnings and limitations. Materialize or cache it by source fingerprint. |
| `GET /api/v2/analytics/status` | Reads `analytics_jobs` and `sync_status` from the control database plus `_analytics_current` and `_analytics_runs` from the user database. Returns `{ job, current, phoneSync }`. | Fixed documents. No explicit cache headers. `phoneSync.secondsSinceLastUpload` changes with wall-clock time. | Happy-path status behavior is tested. | GraphQL candidate for pipeline/run state. It overlaps `/sync/status`, making it an easy consolidation win. Retain REST during compatibility. |
| `GET /api/v2/sync/status` | Reads one `sync_status` document and computes the 120-second upload heartbeat at request time. | Fixed small response; intentionally time-sensitive; no explicit cache headers. | Frontend day header and Android semantics; happy path and lower-level state functions are tested. | GraphQL candidate for ingestion state. It should remain the small polling/readiness signal; retain REST for Android and operational compatibility. |
| `GET /api/v2/analytics/config` | Reads `analyticsConfig` from the authenticated user and applies environment defaults. Returns timezone, sleep target, optional birth date, and optional heart-rate calibration. | Fixed small response; no explicit cache headers. | OpenAPI documents it; only the `PUT` validation path is tested. | GraphQL candidate for the configuration read. Keep `PUT` as REST. Treat birth date and calibration as sensitive user data. |
| `GET /api/v2/analytics/snapshot` | Resolves `_analytics_current`, loads one encrypted `_analytics_snapshots` blob, decrypts the entire legacy snapshot, and serializes raw sleep sessions plus broad prepared histories. | No field/range selection or pagination. `ETag` is the run ID and `Cache-Control` is `private, no-cache`; the route does not explicitly implement conditional `304` handling. | Snapshot shape, tenant isolation, not-ready, and shared auth behavior are tested. | **Replace, do not port literally.** GraphQL resolvers must not decrypt this monolith and then discard unselected fields. Retire it after frontend compatibility ends. |
| `GET /api/v2/analytics/daily?start&end&limit` | Resolves `_analytics_current`, reads sorted `_analytics_daily` documents, decrypts each full per-date blob, and returns compact metrics **plus the full `dayView`** for every date. | Inclusive dates; default limit 400; runtime clamps to 1–1000. With no date range it returns the oldest matching dates. `ETag` is the run ID, shared across parameter combinations; there is no explicit cache policy or conditional handling. | One successful bounded-range test; validation/not-ready/ETag behavior is not covered. | Replace with bounded typed series/daily GraphQL fields. Storage must separate compact series from detailed day views or GraphQL will save network bytes while retaining unnecessary decryption cost. |
| `GET /api/v2/analytics/day?date&radius` | Reads 1–15 `_analytics_daily` documents for the current run, returns one full `health-day-v1` day plus compact nearby-day summaries, and synthesizes explicit empty-day contracts for missing dates. | Date defaults to today in the configured timezone; radius is 0–7. `ETag` is the run ID and caching is `private, no-cache`. | The migrated `/day` frontend uses it. Contract shape and missing notes are tested. | Strong GraphQL candidate, approximately preserving `day(date)` plus bounded nearby context. Keep the REST route during migration because it is already fast and stable. |

## Local performance and payload baseline

These measurements were taken through the local API container on 2026-09-19
against the primary local account. Only status, elapsed time, and byte counts
were printed. Tokens, record identifiers, and health values were not printed.
Responses were read without browser rendering and without relying on frontend
caches, so these numbers isolate local API serialization/transfer rather than
the complete user-perceived page time.

| Operation | Response bytes | Local elapsed time |
| --- | ---: | ---: |
| `GET /api/v2/analytics/inventory` | 5,466 | 1.5761 s |
| `GET /api/v2/analytics/devices` | 13,501 | 1.3760 s |
| `GET /api/v2/analytics/status` | 1,930 | 0.0036 s |
| `GET /api/v2/sync/status` | 448 | 0.0027 s |
| `GET /api/v2/analytics/config` | 137 | 0.0019 s |
| `GET /api/v2/analytics/snapshot` | 10,165,176 | 0.4736 s |
| `GET /api/v2/analytics/daily` for 90 days | 2,612,545 | 0.1404 s |
| `GET /api/v2/analytics/day` with radius 7 | 34,116 | 0.0093 s |
| `POST /api/v2/fetch/sleepSession` with an empty filter | 2,980,691 | 0.1218 s |
| `POST /api/v2/fetch/steps` with an empty filter | 8,380,687 | 1.2943 s |
| `POST /api/v2/fetch/activeCaloriesBurned` with an empty filter | 164,158 | 0.0281 s |
| `POST /api/v2/fetch/totalCaloriesBurned` with an empty filter | 76,029,160 | 9.6148 s |
| `POST /api/v2/fetch/restingHeartRate` with an empty filter | 79,307 | 0.0163 s |
| `POST /api/v2/fetch/weight` with an empty filter | 2,309 | 0.0035 s |

The six raw calls used by the legacy home page transferred **87,636,312 bytes
(87.64 MB decimal)** before frontend validation and local analytics. This is
the primary current bottleneck. The 10.17 MB prepared snapshot is much faster
than regenerating analytics but remains too large for routine page bootstrap or
polling. The focused-day contract demonstrates the intended scale: about 34 KB
and 9 ms locally.

Inventory and device responses are small but take roughly 1.4–1.6 seconds
because they aggregate live raw collections. GraphQL field selection alone will
not fix that execution cost; those results should be prepared or cached when
their source fingerprint changes.

## Storage implications for GraphQL

Prepared analytics currently use:

- `_analytics_current` and `_analytics_runs` for atomic run selection and
  metadata;
- `_analytics_snapshots` for the encrypted legacy monolith;
- `_analytics_daily` for encrypted per-date documents;
- `_analytics_sleep_events` for reconciled sleep events;
- `_analytics_device_comparisons` for prepared sleep-source comparisons;
- `_analytics_summaries` for compact overview structures.

The existing summary collection is a useful GraphQL source. The current daily
collection is not yet ideal: each encrypted blob contains both compact daily
metrics and its detailed `dayView`. MongoDB cannot project fields inside the
encrypted payload, so a chart resolver must currently load and decrypt details
it does not need.

Before claiming a GraphQL performance improvement, implement one of these
storage strategies:

1. add an encrypted compact date-indexed series collection and retain detailed
   day views separately; or
2. split `dayView` out of `_analytics_daily`, with an additive migration that
   preserves already-selected immutable runs until replacements are complete.

Resolvers should load a bounded range once per request and share it between
selected fields. They should never run the analytics pipeline or load raw
history during a dashboard query.

## Initial migration classification

### GraphQL read candidates

- analytics run/job readiness;
- ingestion heartbeat;
- effective analytics configuration;
- source inventory;
- observed devices and provenance;
- compact overview and bounded metric series;
- focused day and nearby-day summaries;
- prepared sleep events and device comparisons when the UI needs them.

### REST operations that remain outside the GraphQL migration

- `GET /health`;
- login, refresh, and revoke;
- Android Health Connect uploads and database-side deletes;
- phone push/delete commands;
- analytics rebuild commands;
- analytics configuration updates;
- calendar delivery operations.

Phase one should contain GraphQL queries only—no mutations or subscriptions.

### Raw fetch decision

The generic raw endpoint is a read, but translating it directly would undermine
the prepared-analytics architecture and recreate the current performance and
security problems. The recommended path is:

1. remove all dashboard dependence on it;
2. measure whether any non-dashboard client still requires it;
3. deprecate and eventually remove it; and
4. only if raw access is genuinely required, design a separate typed,
   allowlisted, date-bounded, cursor-paginated contract.

This is an explicit design-session decision, not an implementation decision
already made by this audit.

## Documentation and test gaps discovered

All 9 active v2 database-read operations appear in
`doc/api-documentation.yml`, but several schemas and behaviors are incomplete:

- analytics status, configuration, inventory, and devices have little or no
  response schema detail;
- raw fetch has no bounds, allowlist, pagination, or API tests;
- inventory and configuration `GET` have no direct API tests;
- daily has only a successful-range test, not validation, not-ready, tenant,
  cache, or response-budget coverage;
- `/health` is not in the OpenAPI document;
- documented login success is `200`, while the implementation returns `201`;
- OpenAPI documents `POST /revoke`, while the implementation uses `DELETE`;
- database-side `DELETE /api/v2/sync/{method}` is implemented but missing from
  OpenAPI;
- daily's documented limit constraint implies validation, while the runtime
  silently clamps values;
- the frontend data-model document still calls the snapshot the primary
  dashboard bootstrap even though it is now a legacy performance liability.

These are baseline findings. They should not be mixed into the GraphQL change
without tests that make each deliberate contract decision explicit.

## GraphQL design questions to resolve before implementation

The schema design session should settle these in order:

1. **Scope:** migrate all 8 dashboard/support reads in phase one, or begin only
   with prepared analytics and add configuration/provenance/status later?
2. **Raw access:** deprecate raw fetch without a GraphQL replacement, or design
   a separate bounded raw-record capability?
3. **Root organization:** group fields under the authenticated viewer, such as
   `viewer.analytics`, `viewer.ingestion`, and `viewer.sources`, or expose
   flatter top-level fields?
4. **Schema style:** use curated domain types with reusable availability
   fields, or a generic metric enum/value model?
5. **Home contract:** which cards and charts load initially, and what default
   and maximum date ranges are required?
6. **Day contract:** expose a focused day and nearby days as one object, or as
   independent fields?
7. **Errors:** how should analytics-not-ready, partial data, invalid dates, and
   field-level failures appear without confusing missing health data with
   execution errors?
8. **Caching:** confirm 60-second private caching, refresh on `runId` change,
   and whether persisted GET queries are desirable later.
9. **Compatibility:** define the frontend migration milestone and the evidence
   required before each REST read is deprecated or removed.

## Success criteria for the eventual migration

- The home page performs no raw-history fetch and no local analytics run.
- Initial home data is bounded and stays within an agreed response budget; a
  provisional target is 100–250 KB rather than 10–88 MB.
- A focused-day query remains in the current order of magnitude (tens of KB
  and tens of milliseconds locally).
- Every list has an explicit date/point/page bound.
- One operation cannot mix analytics runs.
- Missing values and model limitations preserve current semantics.
- Authenticated users can access only their own data.
- GraphQL query depth, aliases, tokens, cost, execution time, and response size
  are limited and tested.
- Logs and errors do not contain tokens, raw health values, or record IDs.
- REST routes remain available until their real consumers have migrated and
  compatibility tests pass.
- The final endpoint-reduction headline counts routes actually removed, while
  separately reporting the number of frontend REST calls consolidated.
