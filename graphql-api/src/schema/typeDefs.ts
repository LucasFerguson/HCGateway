/**
 * GraphQL SDL for the HCGateway read API.
 *
 * Adapted from doc/graphql-schema-design.md, but verified and corrected
 * against real documents in the running MongoDB instance and the Python
 * source that writes them (analytics_engine/{store,repository,service,
 * sync_status,context,day_dashboard,pipeline,sleep,strain,recovery}.py).
 * Deviations from the design doc's hypothesis are called out inline as
 * comments at the point of divergence; see also the final implementation
 * report for a consolidated list.
 *
 * DEVIATION (schema-wide): MetricStatus. The design doc proposed six values
 * (AVAILABLE, PARTIAL, MISSING, INSUFFICIENT_DATA, BLOCKED, NOT_IMPLEMENTED).
 * A full-repo scan of every literal passed as a metric "status" turned up
 * additional real values the backend actually emits: "unavailable" (strain.py),
 * "sample_time_only" (day_dashboard.py's `now` marker), "invalid"/"detailed"/
 * "generic" (sleep.py's stageDataStatus - already typed as plain String
 * elsewhere in this schema, unaffected), and "calibrating"/"ready"
 * (pipeline.py's healthspan status, already typed as plain String). The enum
 * below is extended with UNAVAILABLE and SAMPLE_TIME_ONLY so real `MetricValue`-
 * shaped fields (recovery, strain, timeline.now) don't need to be coerced to
 * an approximate existing value.
 */
export const typeDefs = /* GraphQL */ `
  """
  Declared so client operations may apply @defer to a fragment wrapping an
  expensive selection (e.g. \`... on HeartRateData { samples { ... } } @defer\`).
  Per GraphQL's spec, @defer/@stream are QUERY-side directives valid only on
  FRAGMENT_SPREAD/INLINE_FRAGMENT, never on a schema FIELD_DEFINITION -
  the design doc's "@defer on samples/stages/timeline" fields is read here as
  "these are the fields worth wrapping in a deferred fragment", not literal
  SDL syntax (attaching @defer directly to a field definition does not
  parse). See the KNOWN GAP note below this block for the current runtime
  caveat on incremental delivery.
  """
  directive @defer(if: Boolean = true, label: String) on FRAGMENT_SPREAD | INLINE_FRAGMENT
  directive @stream(if: Boolean = true, label: String, initialCount: Int = 0) on FIELD

  """
  KNOWN GAP (as of 2026-09-19): @apollo/server's current stable, rc, and
  alpha releases (v4 and v5 alike) all declare a graphql peer range of
  ^16.x - none support graphql-js 17's now-stable (non-alpha) incremental
  execution. Apollo's own incremental-delivery polyfill
  (@apollo/server/dist/esm/incrementalDeliveryPolyfill.js) only activates
  real streaming execution when graphql-js reports itself as the exact
  build "17.0.0-alpha.9"; on any other graphql version (including the
  now-released stable 17.0.x, and the 16.x this service runs to satisfy
  @apollo/server's peer dependency) it silently falls back to plain
  \`execute()\`, returning one complete JSON response instead of a
  multipart/mixed incremental stream. The directives above are declared so
  schema/query validation accepts @defer/@stream and so this is a additive,
  forward-compatible no-op today; queries using them still execute
  correctly, just without the latency benefit of incremental delivery,
  until @apollo/server ships a release whose peer range includes a stable
  graphql 17.
  """
  scalar DateTime
  scalar Date
  scalar JSON

  enum MetricStatus {
    AVAILABLE
    PARTIAL
    MISSING
    INSUFFICIENT_DATA
    BLOCKED
    NOT_IMPLEMENTED
    UNAVAILABLE
    SAMPLE_TIME_ONLY
  }

  type MetricValue {
    status: MetricStatus!
    value: Float
    unit: String
    source: String
    note: String
    qualityFlags: [String!]!
  }

  input TimeRange {
    start: DateTime!
    endExclusive: DateTime!
  }

  enum RangeMatch {
    OVERLAPS
    STARTS_WITHIN
  }

  type Query {
    viewer: Viewer!
  }

  type Viewer {
    analytics: Analytics!
    sourceRecords: SourceRecords!
    sources: SourceCatalog!
    ingestion: IngestionStatus!
    config: AnalyticsConfig!
    habits(range: TimeRange): [Habit!]!
  }

  # ---------------------------------------------------------------------
  # Habits - imported WHOOP journal questions and per-cycle responses.
  # These are source records, not derived analytics: answeredYes preserves
  # the question's literal yes/no response and must not be relabeled as a
  # generic success/completion (some questions describe symptoms).
  # ---------------------------------------------------------------------

  type Habit {
    id: ID!
    source: String!
    question: String!
    firstSeenDate: Date!
    lastSeenDate: Date!
    entryCount: Int!
    entries: [HabitEntry!]!
  }

  type HabitEntry {
    id: ID!
    source: String!
    date: Date!
    cycleStartAt: DateTime!
    cycleEndAt: DateTime!
    cycleStartLocal: String!
    cycleEndLocal: String!
    sourceUtcOffsetMinutes: Int!
    answeredYes: Boolean!
    notes: String
  }

  # ---------------------------------------------------------------------
  # SourceRecords - raw Health Connect data, one field per signal.
  # DEVIATION: "restingHeartRates" (plural), "heartRateVariabilities" are the
  # actual Mongo collection names' target keys, but the exposed GraphQL field
  # names below match the design doc's naming and repository.py's normalized
  # record shapes exactly (verified field-for-field against real documents).
  # ---------------------------------------------------------------------

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

  type HeartRateData {
    status: MetricStatus!
    source: String
    sampleCount: Int!
    records(range: TimeRange): [HeartRateRecord!]!
    samples(range: TimeRange): [HeartRateSample!]!  # deferrable via client-side @defer on a fragment wrapping this selection
  }

  type HeartRateRecord {
    id: ID!
    source: String!
    startAt: DateTime!
    endAt: DateTime!
    samples: [HeartRateSample!]!  # deferrable via client-side @defer
  }

  type HeartRateSample {
    observedAt: DateTime!
    bpm: Float!
  }

  type SleepSession {
    id: ID!
    source: String!
    startAt: DateTime!
    endAt: DateTime!
    title: String
    notes: String
    stages: [SleepStage!]!  # deferrable via client-side @defer
    sourceZoneOffsets: ZoneOffsets
  }

  type SleepStage {
    startAt: DateTime!
    endAt: DateTime!
    kind: SleepStageKind!
  }

  enum SleepStageKind {
    AWAKE
    ASLEEP
    UNKNOWN
    LIGHT
    DEEP
    REM
  }

  type ZoneOffsets {
    start: Int
    end: Int
  }

  type StepRecord {
    id: ID!
    source: String!
    startAt: DateTime!
    endAt: DateTime!
    count: Int!
  }

  type EnergyRecord {
    id: ID!
    source: String!
    startAt: DateTime!
    endAt: DateTime!
    energyKcal: Float!
  }

  type RestingHeartRateRecord {
    id: ID!
    source: String!
    observedAt: DateTime!
    bpm: Float!
  }

  type HeartRateVariabilityRecord {
    id: ID!
    source: String!
    observedAt: DateTime!
    milliseconds: Float!
  }

  type RespiratoryRateRecord {
    id: ID!
    source: String!
    observedAt: DateTime!
    breathsPerMinute: Float!
  }

  type OxygenSaturationRecord {
    id: ID!
    source: String!
    observedAt: DateTime!
    percentage: Float!
  }

  type WeightRecord {
    id: ID!
    source: String!
    observedAt: DateTime!
    kilograms: Float!
  }

  type ExerciseSession {
    id: ID!
    source: String!
    startAt: DateTime!
    endAt: DateTime!
    exerciseType: Int!
    title: String
    notes: String
    sourceZoneOffsets: ZoneOffsets
  }

  # ---------------------------------------------------------------------
  # Analytics - prepared, run-scoped data. Verified against real
  # _analytics_current / _analytics_daily / _analytics_summaries /
  # _analytics_sleep_events / _analytics_device_comparisons documents.
  # ---------------------------------------------------------------------

  type Analytics {
    id: ID!
    runId: ID!
    algorithmVersion: String!
    timeZone: String!
    processedAt: DateTime

    day(date: Date!, radius: Int = 0): Day
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

  enum DayState {
    FUTURE
    RECORDED
  }

  type DayNote {
    code: String!
    message: String!
  }

  type Day {
    id: ID!
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

  type TimeWindow {
    startAt: DateTime!
    endAt: DateTime!
  }

  type StageMinutes {
    deep: Float!
    light: Float!
    rem: Float!
    asleep: Float!
    awake: Float!
    unknown: Float!
  }

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

  enum SleepEventRole {
    MAIN
    SUPPLEMENTAL
  }

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

  enum RecoveryBand {
    LOW
    MODERATE
    HIGH
  }

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

  type StrainTimelinePoint {
    at: DateTime!
    loadMinutes: Float!
    strain: Float
  }

  type HourlyStepBucket {
    hour: Int!
    count: Int!
    status: MetricStatus!
  }

  # DEVIATION: day_dashboard.py's WorkoutSummary in timeline.workouts uses key
  # "type" (int) not "exerciseType", and typeLabel is always populated
  # (title or a synthesized "Health Connect type N" string), never null.
  type WorkoutSummary {
    id: ID!
    startAt: DateTime!
    endAt: DateTime!
    type: Int!
    typeLabel: String!
    source: String
    strainContribution: Float
    strainQuality: StrainQuality
  }

  # NowMarker's real status values are "missing" and "sample_time_only" (see
  # MetricStatus deviation note above) - not the full MetricValue envelope
  # (no unit/qualityFlags), so it stays its own small type as in the design doc.
  type NowMarker {
    status: MetricStatus!
    latestObservedAt: DateTime
    note: String
  }

  # ---------------------------------------------------------------------
  # Sleep, sleep debt, consistency, healthspan
  # ---------------------------------------------------------------------

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
    breakdown30Day: SleepDebtBreakdown
  }

  type SleepDebtBreakdown {
    recordedDays: Int!
    none: Int!
    low: Int!
    moderate: Int!
    high: Int!
  }

  type SleepDebtDay {
    id: ID!
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

  enum DebtCategory {
    NONE
    LOW
    MODERATE
    HIGH
  }

  type SleepConsistencySummary {
    baselineWindowDays: Int!
    minimumBaselineNights: Int!
    methodology: String!
    daily(range: TimeRange): [SleepConsistencyDay!]!
    latest: SleepConsistencyDay
    average7DayScore: Float
    average30DayScore: Float
    previous30DayAverageScore: Float
    breakdown30Day: SleepConsistencyBreakdown
  }

  type SleepConsistencyBreakdown {
    scoredDays: Int!
    optimal: Int!
    sufficient: Int!
    poor: Int!
  }

  type SleepConsistencyDay {
    id: ID!
    date: Date!
    source: String!
    bedtimeAt: DateTime!
    wakeAt: DateTime!
    bedtimeMinutesLocal: Float!
    wakeMinutesLocal: Float!
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

  enum ConsistencyCategory {
    OPTIMAL
    SUFFICIENT
    POOR
  }

  type HealthspanSummary {
    modelVersion: String!
    status: HealthspanStatus!
    birthDateConfigured: Boolean!
    methodology: String!
    calibrationReasons: [String!]!
    trend(range: TimeRange): [HealthspanDay!]!
    latest: HealthspanDay
    paceOfAging: Float
    paceWindowDays: Int
  }

  enum HealthspanStatus {
    CALIBRATING
    PARTIAL
    READY
  }

  type HealthspanDay {
    id: ID!
    date: Date!
    chronologicalAgeYears: Float
    healthAgeYears: Float
    ageDeltaYears: Float
    paceOfAging: Float
    factors: [HealthspanFactor!]!
    qualityFlags: [String!]!
  }

  type HealthspanFactor {
    id: ID!
    key: HealthspanFactorKey!
    label: String!
    value: Float!
    unit: HealthspanFactorUnit!
    referenceValue: Float!
    ageImpactYears: Float!
    coverageDays: Int!
  }

  enum HealthspanFactorKey {
    SLEEP_DURATION
    SLEEP_CONSISTENCY
    STEPS
    RESTING_HEART_RATE
  }

  enum HealthspanFactorUnit {
    MINUTES
    PERCENT
    STEPS
    BPM
  }

  type DeviceSleepComparison {
    source: String!
    recordingCount: Int!
    averageSleepMinutes: Float!
    comparisonCount: Int!
    averageDifferenceMinutes: Float!
  }

  # ---------------------------------------------------------------------
  # Metric series (steps, calories, RHR, HRV, weight)
  # ---------------------------------------------------------------------

  type MetricSeries {
    unit: MetricUnit!
    daily(range: TimeRange): [MetricDay!]!
    overview: MetricOverview!
    rolling7Day(range: TimeRange): [RollingPoint!]!
    monthly: [MonthlyPoint!]!
  }

  enum MetricUnit {
    STEPS
    KCAL
    BPM
    KG
    MS
  }

  type MetricDay {
    id: ID!
    date: Date!
    value: Float!
    source: String!
    bySource: [SourceContribution!]!
    qualityFlags: [String!]!
  }

  type SourceContribution {
    source: String!
    value: Float!
    observationCount: Int!
    coverageMinutes: Float
  }

  type MetricOverview {
    latest: MetricDay
    previous: MetricDay
    average7Day: Float
    average30Day: Float
    changeFromPrevious: Float
    sampleCount: Int!
  }

  type RollingPoint {
    id: ID!
    date: Date!
    value: Float!
    sampleCount: Int!
  }

  type MonthlyPoint {
    id: ID!
    month: String!
    value: Float!
    sampleCount: Int!
  }

  # ---------------------------------------------------------------------
  # Strain and recovery (full detail, beyond the Day headline)
  # ---------------------------------------------------------------------

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
    id: ID!
    date: Date!
    score: Float
    loadMinutes: Float!
    zoneMinutes: ZoneMinutes!
    timeline: [StrainTimelinePoint!]!  # deferrable via client-side @defer
    quality: StrainQuality!
  }

  type StrainWorkout {
    id: ID
    startAt: DateTime!
    endAt: DateTime!
    score: Float
    loadMinutes: Float!
    zoneMinutes: ZoneMinutes!
    timeline: [StrainTimelinePoint!]!  # deferrable via client-side @defer
    quality: StrainQuality!
  }

  type ZoneMinutes {
    belowZone1: Float!
    zone1: Float!
    zone2: Float!
    zone3: Float!
    zone4: Float!
    zone5: Float!
  }

  type StrainQuality {
    publishable: Boolean!
    coverageRatio: Float!
    observedMinutes: Float!
    spanMinutes: Float!
    maxGapMinutes: Float
    invalidSampleCount: Int!
    reasons: [String!]!
  }

  type Availability {
    available: Boolean!
    reasons: [String!]!
  }

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

  type RecoveryWeights {
    sleep: Float!
    hrv: Float!
    restingHeartRate: Float!
    sleepConsistency: Float!
  }

  type RecoveryDay {
    id: ID!
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

  type RecoveryComponent {
    score: Float!
    value: Float!
    baseline: Float
    unit: String!
    baselineDays: Int
  }

  type RecoveryDayQuality {
    publishable: Boolean!
    complete: Boolean!
    estimateBasis: RecoveryEstimateBasis!
    availableWeight: Float!
    baselineWindowDays: Int!
    minimumBaselineDays: Int!
    reasons: [String!]!
  }

  enum RecoveryEstimateBasis {
    COMPLETE
    PHYSIOLOGY_PARTIAL
    SLEEP_CONSISTENCY_PARTIAL
    INSUFFICIENT_DATA
  }

  type RecoveryQuality {
    publishable: Boolean
    complete: Boolean
    reasons: [String!]
  }

  type RecoveryAvailability {
    available: Boolean!
    publishableDayCount: Int!
    completeDayCount: Int!
    reasons: [String!]!
  }

  # ---------------------------------------------------------------------
  # SourceCatalog - inventory and devices
  # ---------------------------------------------------------------------

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

  type SourceCount {
    source: String!
    count: Int!
  }

  type SourceSummary {
    source: String!
    label: String!
    records: Int!
  }

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

  enum IdentityQuality {
    EXPLICIT_MODEL
    DEVICE_TYPE
    SOURCE_ONLY
  }

  type DeviceIdentity {
    manufacturer: String
    model: String
    type: Int
    typeLabel: String
  }

  type SignalCount {
    collection: String!
    count: Int!
  }

  type RecordingMethodCount {
    value: Int
    label: String!
    records: Int!
  }

  type DeviceAssociation {
    sourcePackage: Boolean!
    deviceMetadata: Boolean!
    legacyWithoutDeviceMetadata: Boolean!
  }

  # ---------------------------------------------------------------------
  # IngestionStatus - sync and job state
  # ---------------------------------------------------------------------

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

  enum SyncState {
    NEVER_OBSERVED
    RECEIVING
    IDLE
  }

  # DEVIATION: analytics_jobs.status is a free-form job-lifecycle string
  # ("queued" | "running" | "completed" | "failed"), not one of the
  # MetricStatus enum values - kept as plain String, matching the design doc.
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

  # ---------------------------------------------------------------------
  # AnalyticsConfig
  # ---------------------------------------------------------------------

  type AnalyticsConfig {
    homeTimeZone: String!
    sleepTargetMinutes: Int!
    birthDate: Date
    heartRateZoneThresholds: [Float!]
    heartRateZoneTestDate: Date
  }
`;
