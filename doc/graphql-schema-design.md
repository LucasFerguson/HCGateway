# GraphQL read API — schema design

Status: design agreed; implementation not yet started
Date: 2026-09-19
Companion document: [`graphql-read-api-audit.md`](graphql-read-api-audit.md) (the REST baseline this replaces)

## Architecture: a new Node/Apollo Server service, not Python

The GraphQL layer is **Apollo Server on Node.js**, running as its own
container alongside the existing `api`, `analytics-worker`, and
`calendar-worker` services — not a library bolted onto the Flask app. This
supersedes an earlier draft of this document that specified Strawberry
(Python); that approach was dropped once Flask's WSGI model turned out to
block `@defer`/`@stream` entirely (Strawberry restricts incremental delivery
to async integrations — ASGI, FastAPI, Quart, etc. — and Flask, sync or
async, is explicitly excluded; see
[Strawberry's defer-and-stream docs](https://strawberry.rocks/docs/types/defer-and-stream)).
Rather than adopt a weaker GraphQL library to stay in Python, or migrate the
whole Flask app to ASGI, the decision is to give the GraphQL surface to the
ecosystem built for it. Apollo Server has first-class, non-experimental
support for `@defer`/`@stream` and subscriptions, and Node has no WSGI/ASGI
split to work around.

**Division of responsibility, and why:**

- **Python remains the only place that understands health-domain
  semantics.** The analytics pipeline, the `MetricValue`/missing-data rules,
  timezone-aware rendering (`localStartAt`/`localEndAt`), sleep-stage
  reconciliation, run identity/fingerprinting — none of that is
  reimplemented in JS. If a new derived field or bucket resolution is
  needed, it is added to the Python pipeline/store first and then merely
  exposed, never computed fresh in the Node layer.
- **Node/Apollo is a thin, typed passthrough.** Resolvers read already-
  prepared documents from `_analytics_daily`, `_analytics_summaries`,
  `_analytics_sleep_events`, `_analytics_device_comparisons`, and raw source
  collections, and map their stored JSON shape onto GraphQL types
  field-for-field. No bucketing, aggregation, or reconciliation logic lives
  in Node.
- **Node connects to MongoDB directly**, with its own `MONGO_URI`, using the
  official MongoDB Node.js driver — the same shared-database-separate-process
  pattern `analytics-worker` and `calendar-worker` already use. No internal
  HTTP hop through the Flask API.
- **Token validation is the one deliberately duplicated piece.** Node
  independently looks up the bearer token against `hcgateway.users` and
  checks expiry — the same one-collection lookup Flask's auth hook does. This
  is intentionally not shared code (there is no shared Node/Python library
  in this project), but it is small, stable, and low-risk to keep in sync,
  unlike health-domain logic.
- Writes stay exactly where they are. Android sync, database-side deletes,
  config updates, and rebuild commands remain on the existing Flask REST
  routes, permanently. Node never writes to MongoDB.

## Scope for phase 1

- **Read-only.** No mutations in the Node/Apollo schema at all.
- **`@defer` is available and should be used** on the fields marked
  `@defer` below (raw samples, stage lists, long timelines) — Apollo
  Server's incremental delivery works over plain HTTP (multipart/mixed),
  needs no WebSocket, and is unrelated to subscriptions. Apollo Client
  renders the initial payload immediately and patches in deferred fields as
  they arrive.
- **Subscriptions: not in phase 1, but cheap to add in phase 2 on this
  stack.** Update cadence here is a few analytics runs a day, not a
  continuous stream, so `pollInterval` against `viewer.ingestion.current.runId`
  is the right-sized mechanism to start with. Unlike the Python/Strawberry
  path, adding subscriptions later needs no second service and no ASGI
  migration — Apollo Server supports `graphql-ws` natively on the same
  process. This makes phase 2 here strictly easier than it would have been
  on Flask.
- **No pagination anywhere.** Every list field returns its full result. Time
  ranges are optional, not mandatory — see "Unbounded queries" below for the
  reasoning and the safety backstop that replaces range-bounding.
- Runs additively alongside the current REST v2 API. Nothing is removed in
  phase 1.

## Root shape

```graphql
type Query {
  viewer: Viewer!
}

type Viewer {
  analytics: Analytics!
  sourceRecords: SourceRecords!
  sources: SourceCatalog!
  ingestion: IngestionStatus!
  config: AnalyticsConfig!
}
```

Everything hangs off `viewer` so user scoping is structurally obvious at every
call site. The resolver for `Query.viewer` is the one and only place that
reads the bearer token and derives the authenticated user + database name —
no field anywhere accepts a user ID or database name as an argument.

`Viewer`'s resolver also pins one `runId` for the whole request (read once
from `_analytics_current`, stored on Apollo Server's per-request `context`
object) so two `analytics` fields in the same query can never straddle two
different completed runs if the worker finishes mid-request. `sourceRecords`,
`sources`, and `ingestion` are not run-scoped — they read live/raw state.

## How literally to take the type definitions below

Everything from here down — every `type`, every field name, every nested
shape — was written from a careful but external read of the Python code
(`day_dashboard.py`, `pipeline.py`, `store.py`, `repository.py`, `service.py`,
`context.py`) at design time. It is the **expected** shape, not a contract to
force the real data into. Treat it as a strong, detailed hypothesis, not a
spec to satisfy at any cost.

When building a resolver, read the actual documents in the actual
collection first. If a field described here doesn't exist, is named
differently, is nested differently, or the real data simply doesn't support
a distinction this document draws (e.g. an enum with fewer or different
values than listed) — **expose what is actually there**, named and shaped to
match reality, rather than reshaping/renaming/coercing it to fit this
document. Note the mismatch in the PR description or a code comment so it's
visible later, but do not treat this document as ground truth over the
database.

The reasoning: the honest long-term fix for any data-shape inconsistency
(the `MetricValue`-envelope-vs-plain-dict asymmetry noted below is the known
current example) is a change to how Python's analytics pipeline writes
`_analytics_*` documents — a MongoDB schema change — after which the GraphQL
layer just follows suit, since it's a thin passthrough by design. Bending
the GraphQL layer to paper over an inconsistency the database itself still
has just creates a second, competing source of truth about the data's real
shape. Expose it as it is now; clean up the schema (Mongo, then GraphQL) as
a deliberate later step if it's still worth doing.

This applies to every section below, not just the ones that call it out
explicitly.

## Shared building blocks

### `MetricValue` — the missing-data envelope

The backend currently has two shapes for "a value that might be missing":
`day_dashboard.py`'s explicit `{status, value, unit, note, source,
qualityFlags}` envelope, and everything else (`pipeline.py`, `recovery.py`,
`strain.py`), which represents "missing" as an absent dict key or a bare
`null` next to a separate `status`/`band`/`category` field. This document
proposes exposing **one envelope everywhere** as the ideal — but per the
note above, this is the "expose it honestly" caveat's clearest test case:
where a plain dict genuinely doesn't map cleanly onto `MetricValue` (there's
no real `unit`, or "missing" doesn't cleanly reduce to one status enum),
prefer a type that matches that domain's real shape over forcing every field
through this envelope. Use `MetricValue` where it's a natural fit (it
already is one, verbatim, for everything under `day_dashboard.py`); don't
contort `strain`/`recovery`/pipeline fields into it if the fit is forced.
Never coerce missing to a numeric zero regardless of which shape is used —
that rule holds no matter how this envelope question resolves.

```graphql
enum MetricStatus {
  AVAILABLE
  PARTIAL
  MISSING
  INSUFFICIENT_DATA
  BLOCKED
  NOT_IMPLEMENTED
}

type MetricValue {
  status: MetricStatus!
  value: Float
  unit: String
  source: String
  note: String
  qualityFlags: [String!]!
}
```

Never coerce missing to `0`. A `MISSING`/`INSUFFICIENT_DATA`/`BLOCKED` status
always pairs with `value: null`. This is a hard rule carried over from
`AGENTS.md` and the REST contract; it is not a style preference.

### `TimeRange` — half-open, optional

```graphql
input TimeRange {
  start: DateTime!
  endExclusive: DateTime!
}
```

`start` inclusive, `endExclusive` exclusive, so adjacent requests never
double-count a boundary instant. Every field that accepts a range makes the
argument **optional** — omitting it means "everything." See "Unbounded
queries" below.

### Scalars

`DateTime` (ISO 8601 instant, always UTC on the wire — the API's canonical
timezone handling is unchanged: raw storage is UTC, `Day.timeZone` and
`localStartAt`/`localEndAt` fields carry the configured IANA zone for
rendering). `Date` (`YYYY-MM-DD`, local calendar date).

## Unbounded queries — the actual safety model

Per explicit product decision, raw-record fields do **not** require a
`TimeRange`. Omitting `range` returns the signal's entire history. This is a
deliberate choice given GraphQL's context here: authenticated, single-user
scoped, depth/complexity limited — a materially different risk profile than
the REST audit's actual complaint, which was an *unauthenticated-adjacent,
arbitrary-collection, arbitrary-filter* endpoint with no bound of any kind.

The backstop is a server-side circuit breaker, not a range requirement:

- A resolver-level wall-clock timeout (target: ~25s) that aborts and returns
  a partial response with a GraphQL error on the unfinished field, rather
  than blocking a gunicorn worker thread indefinitely.
- A response-size cap (target: ~100MB serialized) enforced while streaming
  the response, same failure mode.
- Both limits are configurable via environment variables so they can be
  tuned without a schema change.
- Query depth, alias count, and a computed complexity score (cost roughly
  proportional to potential row count: unbounded raw-sample fields cost more
  than a bounded `Day`) are still limited unconditionally — this defends
  against a pathological *query shape* (e.g. 50 aliased unbounded
  `heartRateRecords` selections in one request), which a timeout alone does
  not.

Every *bucketed/derived* field (series, summaries, day views) stays cheap
regardless of range because it reads pre-aggregated `_analytics_*` documents,
never raw collections directly.

## `SourceRecords` — raw Health Connect data

One field per signal. Dense per-observation signals (heart rate) get three
granularities so the frontend chooses cost explicitly; everything else gets
the plain record list plus, where it's genuinely useful, a bucketed series.

```graphql
type SourceRecords {
  heartRate(range: TimeRange): HeartRateData!
  sleepSessions(range: TimeRange, match: RangeMatch = OVERLAPS): [SleepSession!]!
  steps(range: TimeRange): [StepRecord!]!
  activeCalories(range: TimeRange): [EnergyRecord!]!
  totalCalories(range: TimeRange): [EnergyRecord!]!
  restingHeartRate(range: TimeRange): [RestingHeartRateRecord!]!
  heartRateVariability(range: TimeRange): [HeartRateVariabilityRecord!]!
  respiratoryRate(range: TimeRange): [RespiratoryRateRecord!]!
  oxygenSaturation(range: TimeRange): [OxygenSaturationRecord!]!
  weight(range: TimeRange): [WeightRecord!]!
  exerciseSessions(range: TimeRange): [ExerciseSession!]!
}

enum RangeMatch { OVERLAPS, STARTS_WITHIN }
```

`RangeMatch` matters only for interval-shaped records that can straddle a
range boundary (sleep sessions spanning midnight); point-in-time signals
(`restingHeartRate`, `weight`, etc.) don't need it — `observedAt` is either in
range or it isn't.

### Heart rate — three granularities on one type

```graphql
type HeartRateData {
  status: MetricStatus!
  source: String
  sampleCount: Int!
  records: [HeartRateRecord!]!             # session-grouped, full detail
  series(resolution: SeriesResolution!): [SeriesPoint!]!   # bucketed, cheap
  samples(range: TimeRange): [HeartRateSample!]! @defer     # flattened raw, expensive, deferred
}

enum SeriesResolution { FIVE_MINUTES, FIFTEEN_MINUTES, HOURLY, DAILY }

type SeriesPoint {
  bucketStart: DateTime!
  count: Int!
  min: Float
  p25: Float
  mean: Float
  p75: Float
  max: Float
}

type HeartRateRecord {
  id: ID!
  source: String!
  startAt: DateTime!
  endAt: DateTime!
  samples: [HeartRateSample!]! @defer
}

type HeartRateSample {
  observedAt: DateTime!
  bpm: Float!
}
```

A dashboard chart selects `series(resolution: HOURLY)` and never touches
`samples` — the 610,787-record primary-account collection never gets
flattened for that query. A detail/zoom view can select `samples` on the same
request; `@defer` means it streams in after the cheap fields without blocking
them. `series` reads from the existing `timeline.heartRate.hours` shape in
`dayView` where the range fits within already-materialized days, and falls
back to an on-the-fly aggregation query for ranges/resolutions that don't.

### Other raw record types

Mirrors `repository.py`'s normalized shapes directly — GraphQL fields use the
same field names, camelCase already matches:

```graphql
type SleepSession {
  id: ID!
  source: String!
  startAt: DateTime!
  endAt: DateTime!
  title: String
  notes: String
  stages: [SleepStage!]! @defer
  sourceZoneOffsets: ZoneOffsets
}

type SleepStage { startAt: DateTime!, endAt: DateTime!, kind: SleepStageKind! }
enum SleepStageKind { AWAKE, ASLEEP, UNKNOWN, LIGHT, DEEP, REM }
type ZoneOffsets { start: Int, end: Int }

type StepRecord { id: ID!, source: String!, startAt: DateTime!, endAt: DateTime!, count: Int! }

type EnergyRecord { id: ID!, source: String!, startAt: DateTime!, endAt: DateTime!, energyKcal: Float! }

type RestingHeartRateRecord { id: ID!, source: String!, observedAt: DateTime!, bpm: Float! }

type HeartRateVariabilityRecord { id: ID!, source: String!, observedAt: DateTime!, milliseconds: Float! }

type RespiratoryRateRecord { id: ID!, source: String!, observedAt: DateTime!, breathsPerMinute: Float! }

type OxygenSaturationRecord { id: ID!, source: String!, observedAt: DateTime!, percentage: Float! }

type WeightRecord { id: ID!, source: String!, observedAt: DateTime!, kilograms: Float! }

type ExerciseSession {
  id: ID!
  source: String!
  startAt: DateTime!
  endAt: DateTime!
  exerciseType: Int!
  typeLabel: String
  title: String
  notes: String
  sourceZoneOffsets: ZoneOffsets
}
```

## `Analytics` — prepared, run-scoped data

```graphql
type Analytics {
  runId: ID!
  algorithmVersion: String!
  timeZone: String!
  processedAt: DateTime!

  day(date: Date!, radius: Int = 0): Day!
  days(range: TimeRange): [Day!]!

  sleepEvents(range: TimeRange): [SleepEvent!]!
  sleepDebt: SleepDebtSummary!
  sleepConsistency: SleepConsistencySummary!
  healthspan: HealthspanSummary!
  deviceSleepComparisons: [DeviceSleepComparison!]!

  steps: MetricSeries!
  activeCalories: MetricSeries!
  totalCalories: MetricSeries!
  restingHeartRate: MetricSeries!
  heartRateVariability: MetricSeries!
  weight: MetricSeries!

  strain: StrainSummary!
  recovery: RecoverySummary!
}
```

`radius` on `day()` preserves the existing `/analytics/day?radius=` nearby-day
context in one field rather than a second round trip. `days(range)` replaces
`/analytics/daily` for genuinely multi-day views (trend charts); it returns
full `Day` objects, not a separate compact shape, because `Day`'s own fields
already let the frontend select only what it needs — no separate "compact
daily" type is required once field selection replaces the REST response's
fixed shape.

### `Day` — mirrors `health-day-v1` directly

```graphql
type Day {
  contractVersion: String!
  date: Date!
  timeZone: String!
  dayState: DayState!
  generatedAt: DateTime

  headlineScores: HeadlineScores!
  supportingMetrics: SupportingMetrics!
  timeline: DayTimeline!
  heartRateZones: MetricValue!
  notes: [DayNote!]!
}

enum DayState { FUTURE, RECORDED }
type DayNote { code: String!, message: String! }

type HeadlineScores {
  sleepDuration: SleepDurationMetric!
  sleepNeed: MetricValue!
  recovery: RecoveryMetric!
  strain: StrainMetric!
  strainTarget: MetricValue!
}

type SleepDurationMetric {
  status: MetricStatus!
  value: Float
  unit: String
  note: String
  source: String
  window: TimeWindow
  windowScope: String
  valueScope: String
  stageMinutes: StageMinutes
  stageDataStatus: String
  unclassifiedSleepMinutes: Float
  eventCount: Int
  recordingCount: Int
  mainEvent: SleepEventSummary
  events: [SleepEventSummary!]!
}

type TimeWindow { startAt: DateTime!, endAt: DateTime! }
type StageMinutes { deep: Float!, light: Float!, rem: Float!, asleep: Float!, awake: Float!, unknown: Float! }

type SleepEventSummary {
  id: ID!
  role: SleepEventRole!
  source: String
  startAt: DateTime!
  endAt: DateTime!
  windowMinutes: Float!
  sleepMinutes: Float!
  stageDataStatus: String!
  qualityFlags: [String!]
  recordingCount: Int
}
enum SleepEventRole { MAIN, SUPPLEMENTAL }

type RecoveryMetric {
  status: MetricStatus!
  value: Float
  unit: String
  note: String
  source: String
  qualityFlags: [String!]!
  modelVersion: String
  provisional: Boolean
  band: RecoveryBand
  components: RecoveryComponents
  quality: RecoveryQuality
}
enum RecoveryBand { LOW, MODERATE, HIGH }

type StrainMetric {
  status: MetricStatus!
  value: Float
  unit: String
  note: String
  source: String
  qualityFlags: [String!]!
  modelVersion: String
  quality: StrainQuality
}

type SupportingMetrics {
  hrv: MetricValue!
  restingHeartRate: MetricValue!
  respiratoryRate: MetricValue!
  oxygenSaturation: MetricValue!
  skinTemperatureDeviation: MetricValue!
  steps: MetricValue!
  calories: MetricValue!
  zone3AndAbove: MetricValue!
}

type DayTimeline {
  heartRate: HeartRateTimeline!
  strain: [StrainTimelinePoint!]!
  sleepStages: [SleepStage!]!
  steps: [HourlyStepBucket!]!
  workouts: [WorkoutSummary!]!
  schedule: MetricValue!
  targetWakeTime: MetricValue!
  targetBedTime: MetricValue!
  now: NowMarker!
}

type HeartRateTimeline {
  status: MetricStatus!
  source: String
  sampleCount: Int!
  observedMinuteCount: Int!
  hours: [HourlyHeartRate!]!
  note: String
}
type HourlyHeartRate {
  hour: Int!
  status: MetricStatus!
  sampleCount: Int!
  min: Float
  p25: Float
  mean: Float
  p75: Float
  max: Float
}

type StrainTimelinePoint { at: DateTime!, loadMinutes: Float!, strain: Float }
type HourlyStepBucket { hour: Int!, count: Int!, status: MetricStatus! }
type WorkoutSummary {
  id: ID!
  startAt: DateTime!
  endAt: DateTime!
  exerciseType: Int!
  typeLabel: String!
  source: String
  strainContribution: Float
  strainQuality: StrainQuality
}
type NowMarker { status: MetricStatus!, latestObservedAt: DateTime, note: String }
```

### Sleep, sleep debt, consistency, healthspan

```graphql
type SleepEvent {
  id: ID!
  date: Date!
  timeZone: String!
  localStartAt: DateTime!
  localEndAt: DateTime!
  role: SleepEventRole!
  primary: SleepSession!
  recordings: [SleepSession!]!
  recordingCount: Int!
  windowMinutes: Float!
  sleepMinutes: Float!
  stageMinutes: StageMinutes!
  stageCoverageMinutes: Float!
  stageCoverageRatio: Float!
  detailedStageCoverageRatio: Float!
  stageDataStatus: String!
  hasCredibleStageTimeline: Boolean!
  hasCredibleDetailedStages: Boolean!
  qualityFlags: [String!]!
  primarySelection: PrimarySelection!
}
type PrimarySelection {
  method: String!
  version: String!
  minimumWindowRatio: Float!
  longestWindowMinutes: Float!
  selectedWindowMinutes: Float!
  selectedStageDataStatus: String!
  selectedHasCredibleDetailedStages: Boolean!
  eligibleRecordingCount: Int!
}

type SleepDebtSummary {
  targetMinutes: Int!
  methodology: String!
  daily(range: TimeRange): [SleepDebtDay!]!
  latest: SleepDebtDay
  average7DayMinutes: Float
  average30DayMinutes: Float
  previous30DayAverageMinutes: Float
  breakdown30Day: JSON
}
type SleepDebtDay {
  date: Date!
  sleepMinutes: Float!
  targetMinutes: Int!
  debtMinutes: Float!
  surplusMinutes: Float!
  category: DebtCategory!
  rolling7DayAverageMinutes: Float
  rolling7DayTotalMinutes: Float
  rolling30DayAverageMinutes: Float
}
enum DebtCategory { NONE, LOW, MODERATE, HIGH }

type SleepConsistencySummary {
  baselineWindowDays: Int!
  minimumBaselineNights: Int!
  methodology: String!
  daily(range: TimeRange): [SleepConsistencyDay!]!
  latest: SleepConsistencyDay
  average7DayScore: Float
  average30DayScore: Float
  previous30DayAverageScore: Float
  breakdown30Day: JSON
}
type SleepConsistencyDay {
  date: Date!
  source: String
  bedtimeAt: DateTime
  wakeAt: DateTime
  bedtimeMinutesLocal: Float
  wakeMinutesLocal: Float
  baselineBedtimeMinutesLocal: Float
  baselineWakeMinutesLocal: Float
  bedtimeDeviationMinutes: Float
  wakeDeviationMinutes: Float
  baselineNightCount: Int!
  score: Float
  category: ConsistencyCategory
  rolling7DayAverageScore: Float
  rolling30DayAverageScore: Float
  qualityFlags: [String!]!
}
enum ConsistencyCategory { OPTIMAL, SUFFICIENT, POOR }

type HealthspanSummary {
  modelVersion: String!
  status: String!
  birthDateConfigured: Boolean!
  methodology: String!
  calibrationReasons: [String!]!
  trend(range: TimeRange): [HealthspanDay!]!
  latest: HealthspanDay
  paceOfAging: Float
  paceWindowDays: Int
}
type HealthspanDay {
  date: Date!
  chronologicalAgeYears: Float
  healthAgeYears: Float
  ageDeltaYears: Float
  paceOfAging: Float
  factors: [HealthspanFactor!]!
  qualityFlags: [String!]!
}
type HealthspanFactor {
  key: String!, label: String!, value: Float!, unit: String!
  referenceValue: Float, ageImpactYears: Float, coverageDays: Int
}

type DeviceSleepComparison {
  source: String!
  recordingCount: Int!
  averageSleepMinutes: Float!
  comparisonCount: Int!
  averageDifferenceMinutes: Float!
}
```

### Metric series (steps, calories, RHR, HRV, weight)

One shared shape for every `build_metric_analytics` output — steps and
calories are interval metrics, RHR/HRV/weight are point metrics; the GraphQL
type is the same, only which fields a given metric populates differs, which
field selection already handles for free.

```graphql
type MetricSeries {
  unit: String!
  daily(range: TimeRange): [MetricDay!]!
  overview: MetricOverview!
  rolling7Day(range: TimeRange): [RollingPoint!]!
  monthly: [MonthlyPoint!]!
}
type MetricDay {
  date: Date!
  value: Float!
  source: String
  bySource: [SourceContribution!]!
  qualityFlags: [String!]!
}
type SourceContribution { source: String!, value: Float!, observationCount: Int!, coverageMinutes: Float }
type MetricOverview {
  latest: MetricDay
  previous: MetricDay
  average7Day: Float
  average30Day: Float
  changeFromPrevious: Float
  sampleCount: Int!
}
type RollingPoint { date: Date!, value: Float, sampleCount: Int! }
type MonthlyPoint { month: String!, value: Float!, sampleCount: Int! }
```

### Strain and recovery (full detail, beyond the `Day` headline)

```graphql
type StrainSummary {
  algorithmVersion: String!
  status: String!
  methodology: String!
  limitations: [String!]!
  provisional: Boolean!
  timeZone: String!
  calibration: StrainCalibration!
  daily(range: TimeRange): [StrainDay!]!
  workouts(range: TimeRange): [StrainWorkout!]!
  availability: Availability!
  source: String
}
type StrainCalibration {
  available: Boolean!
  method: String
  reasons: [String!]!
  thresholds: [Float!]
  sampleCount: Int
  dateCount: Int
  invalidSampleCount: Int
  empiricalHighBpm: Float
  confidence: String
}
type StrainDay {
  date: Date!
  score: Float
  loadMinutes: Float!
  zoneMinutes: ZoneMinutes!
  timeline: [StrainTimelinePoint!]! @defer
  quality: StrainQuality!
}
type StrainWorkout {
  id: ID!, startAt: DateTime!, endAt: DateTime!
  score: Float, loadMinutes: Float!, zoneMinutes: ZoneMinutes!
  timeline: [StrainTimelinePoint!]! @defer
  quality: StrainQuality!
}
type ZoneMinutes { belowZone1: Float!, zone1: Float!, zone2: Float!, zone3: Float!, zone4: Float!, zone5: Float! }
type StrainQuality {
  publishable: Boolean!
  coverageRatio: Float!
  observedMinutes: Float!
  spanMinutes: Float!
  maxGapMinutes: Float
  invalidSampleCount: Int!
  reasons: [String!]!
}
type Availability { available: Boolean!, reasons: [String!]! }

type RecoverySummary {
  algorithmVersion: String!
  status: String!
  provisional: Boolean!
  methodology: String!
  limitations: [String!]!
  weights: RecoveryWeights!
  daily(range: TimeRange): [RecoveryDay!]!
  availability: RecoveryAvailability!
}
type RecoveryWeights { sleep: Float!, hrv: Float!, restingHeartRate: Float!, sleepConsistency: Float! }
type RecoveryDay {
  date: Date!
  score: Int
  band: RecoveryBand
  status: String!
  provisional: Boolean!
  components: RecoveryComponents!
  quality: RecoveryDayQuality!
}
type RecoveryComponents {
  sleep: RecoveryComponent
  restingHeartRate: RecoveryComponent
  hrv: RecoveryComponent
  sleepConsistency: RecoveryComponent
}
type RecoveryComponent { score: Float!, value: Float!, baseline: Float, unit: String!, baselineDays: Int }
type RecoveryDayQuality {
  publishable: Boolean!, complete: Boolean!, availableWeight: Float!
  baselineWindowDays: Int!, minimumBaselineDays: Int!, reasons: [String!]!
}
type RecoveryQuality { publishable: Boolean, complete: Boolean, reasons: [String!] }
type RecoveryAvailability { available: Boolean!, publishableDayCount: Int!, completeDayCount: Int!, reasons: [String!]! }
```

`components` fields are nullable per-component (mirrors the backend: a
component dict key is simply absent when that component isn't available) —
`null` there means "this component wasn't computed," which is distinct from
`RecoveryDay.score: null` meaning "no overall score."

`JSON` above is a standard opaque-scalar escape hatch for
`sleepDebt.breakdown30Day` / `sleepConsistency.breakdown30Day` — their
internal shape is a reporting/diagnostic breakdown not yet worth fully typing;
revisit if the frontend needs to query into it structurally.

## `SourceCatalog` — inventory and devices

```graphql
type SourceCatalog {
  inventory: SignalInventory!
  devices: DeviceCatalog!
}

type SignalInventory {
  totalRecords: Int!
  earliest: DateTime
  latest: DateTime
  signals: [SignalInventoryEntry!]!
  sources: [SourceSummary!]!
}
type SignalInventoryEntry {
  collection: String!
  records: Int!
  earliest: DateTime
  latest: DateTime
  bySource: [SourceCount!]!
}
type SourceCount { source: String!, count: Int! }
type SourceSummary { source: String!, label: String!, records: Int! }

type DeviceCatalog {
  count: Int!
  devices: [ObservedDevice!]!
  limitations: [String!]!
}
type ObservedDevice {
  id: ID!
  description: String!
  sourcePackage: String!
  sourceLabel: String!
  device: DeviceIdentity
  identityQuality: IdentityQuality!
  mayCombinePhysicalDevices: Boolean!
  records: Int!
  earliest: DateTime
  latest: DateTime
  signals: [SignalCount!]!
  recordingMethods: [RecordingMethodCount!]!
  association: DeviceAssociation!
}
enum IdentityQuality { EXPLICIT_MODEL, DEVICE_TYPE, SOURCE_ONLY }
type DeviceIdentity { manufacturer: String, model: String, type: Int, typeLabel: String }
type SignalCount { collection: String!, count: Int! }
type RecordingMethodCount { value: Int, label: String!, records: Int! }
type DeviceAssociation { sourcePackage: Boolean!, deviceMetadata: Boolean!, legacyWithoutDeviceMetadata: Boolean! }
```

Both `inventory` and `devices` scan raw collections live in the current REST
implementation (1.4–1.6s locally per the audit). Phase 1 keeps that behavior;
caching by source fingerprint (already flagged in the audit as a follow-up)
is an optimization independent of the GraphQL move and can land either
inside these resolvers or upstream in `service.py` without a schema change.

## `IngestionStatus` — sync and job state

```graphql
type IngestionStatus {
  phoneSync: PhoneSyncStatus!
  analyticsJob: AnalyticsJobStatus
  current: CurrentRun
}

type PhoneSyncStatus {
  observedActive: Boolean!
  state: SyncState!
  lastUploadAt: DateTime
  activeUntil: DateTime
  secondsSinceLastUpload: Int
  lastRecordType: String
  lastRecordCount: Int
  totalUploadRequests: Int!
  totalRecordsReceived: Int!
  activityWindowSeconds: Int!
  note: String!
}
enum SyncState { NEVER_OBSERVED, RECEIVING, IDLE }

type AnalyticsJobStatus {
  userId: ID!
  status: String!
  reason: String
  requestedRevision: Int
  requestedAt: DateTime
  startedAt: DateTime
  completedAt: DateTime
  error: JSON
  result: JSON
}

type CurrentRun {
  runId: ID!
  algorithmVersion: String!
  sourceFingerprint: String!
  configurationFingerprint: String!
  completedAt: DateTime!
  counts: JSON!
  issueCount: Int!
}
```

This is the field the frontend polls (`pollInterval`, replacing the current
60-second full-snapshot refresh): `viewer.ingestion.current.runId`. A changed
`runId` tells the client to refetch `viewer.analytics`; an unchanged one costs
one cheap document read.

## `AnalyticsConfig`

```graphql
type AnalyticsConfig {
  homeTimeZone: String!
  sleepTargetMinutes: Int!
  birthDate: Date
  heartRateZoneThresholds: [Float!]
  heartRateZoneTestDate: Date
}
```

Read-only mirror of `GET /api/v2/analytics/config`. `PUT` stays REST — no
mutation type in phase 1.

## Authentication, authorization, and safety checklist

Carried over from the audit's non-negotiables, restated as concrete
implementation requirements for whoever builds this:

- [ ] Bearer token validated before any resolver executes (Apollo Server's
      `context` function, once per request — not per-field checks).
- [ ] User / database name derived once in `context`, from the token, never
      from a query argument.
- [ ] One `runId` pinned per request for every `Analytics` field.
- [ ] Query depth limit, alias limit, and a complexity/cost calculator that
      weights unbounded raw-sample fields heavily.
- [ ] Per-request resolver timeout and response-size cap (env-configurable).
- [ ] Production: GraphiQL/introspection playground disabled; introspection
      itself may stay on (needed for Apollo codegen) unless that becomes a
      concern later.
- [ ] Every missing-data path returns `MetricStatus` + `null` value, never a
      numeric zero.
- [ ] No raw collection is queryable by arbitrary caller-supplied filter —
      every `SourceRecords` field is a fixed, typed, single-signal read, never
      a `find()` passthrough. This is the one REST behavior (`POST
      /fetch/{method}`) that must never be reproduced as-is.
- [ ] Logs/errors never contain tokens, raw health values, or record IDs.

## Implementation notes for the build

- **Stack:** Node.js + TypeScript, `@apollo/server` with `@apollo/server/express4`
  (Express as the thin HTTP host Apollo needs) or the standalone integration —
  either is fine, Express is the more common/documented pairing. Schema
  defined with `@graphql-tools/schema` from the SDL in this document, or
  written as SDL directly and loaded — do not hand-translate the SDL into a
  code-first builder; keep this document's type definitions as the source of
  truth and diff against them.
- **New service, new container:** add e.g. `graphql-api` to
  `docker-compose.yml`, its own `Dockerfile` under a new `graphql-api/`
  directory (sibling to `api/`), its own `package.json`/lockfile, and its own
  `MONGO_URI` env var pointed at the same MongoDB instance/credentials the
  Python services already use. Pick a port that doesn't collide with `6644`
  (the Flask API) — e.g. `6645`.
- **MongoDB driver:** the official `mongodb` npm package. Read `hcgateway`
  (control db, for `users`/`analytics_jobs`/`sync_status`) and
  `hcgateway_<userId>` (per-user data) exactly as the Python services derive
  those names — do not reimplement that naming convention differently.
- **Where resolvers read from:** the existing `_analytics_summaries`,
  `_analytics_daily`, `_analytics_sleep_events`, `_analytics_device_comparisons`
  collections (already plaintext BSON post-migration — plain JS objects once
  read via the driver, no decrypt step). `SourceRecords` resolvers read raw
  collections directly and map their stored field names onto GraphQL types;
  they must not reimplement `repository.py`'s normalization logic (e.g. stage
  `kind` enum mapping, sleep reconciliation) — if a raw field's on-disk shape
  doesn't already match what this schema wants, that is a signal the Python
  side needs to materialize it first, not that Node should transform it.
- **`@defer`:** implement using Apollo Server's built-in incremental delivery
  support on the fields marked `@defer` in this document. Confirm the Apollo
  Client version in the frontend repo supports `@defer` before wiring it up
  end-to-end (the `HttpLink`/`ApolloClient` setup needs
  `incrementalDelivery` at the recent versions this uses — verify at
  implementation time rather than assume).
- **Auth duplication:** implement the bearer-token lookup against
  `hcgateway.users` (check existence + expiry) directly in Apollo's `context`
  function. Keep it to that one lookup — do not grow parallel copies of any
  other Python logic in Node.
- **Testing:** Node-side tests (e.g. Vitest or Jest) against a real or
  in-memory MongoDB instance, one per resolver family, proving (a) tenant
  isolation (user A's token cannot see user B's data via any field), (b)
  missing data renders as `MetricStatus`, never zero, (c) the complexity/depth
  limiter rejects a pathological query, (d) `runId` pinning holds across a
  simulated in-flight worker completion. These tests live in the new
  `graphql-api/` directory, separate from the Python `api/tests/` suite, and
  are not part of the existing `python -m unittest discover` run.
