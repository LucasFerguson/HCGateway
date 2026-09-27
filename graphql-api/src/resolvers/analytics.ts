import { GraphQLContext } from "../context.js";
import { findUserById } from "../db/mongo.js";
import {
  DailyDocument,
  readCurrentMetadata,
  readCurrentRun,
  readDaily,
  readDailyOne,
  readDeviceComparisons,
  readSleepEvents,
  readSummary,
} from "../db/analytics.js";
import { toMetricStatus } from "./status.js";

interface TimeRangeInput {
  start: Date;
  endExclusive: Date;
}

/**
 * Every dated array below (daily/trend/workouts/sleepEvents) is read in full
 * from an _analytics_* collection already scoped to one pinned runId, then
 * filtered in-process by local calendar `date` (a string) or an ISO instant
 * field against the optional TimeRange. This mirrors the design doc's
 * "every bucketed/derived field stays cheap ... because it reads
 * pre-aggregated _analytics_* documents" - no raw collection scan happens
 * for any Analytics.* field.
 */
function withinDateRange(dateStr: string, range?: TimeRangeInput): boolean {
  if (!range) return true;
  // Dates are local calendar dates (YYYY-MM-DD); compare as UTC midnight
  // instants, consistent with how the design doc treats Date-keyed series.
  const instant = new Date(`${dateStr}T00:00:00Z`).getTime();
  return instant >= range.start.getTime() && instant < range.endExclusive.getTime();
}

function withinInstantRange(value: string | Date | null | undefined, range?: TimeRangeInput): boolean {
  if (!range) return true;
  if (!value) return false;
  const instant = new Date(value).getTime();
  return instant >= range.start.getTime() && instant < range.endExclusive.getTime();
}

/** Resolve the pinned run (context.analyticsRunId, set once by Viewer). */
function requireRunId(context: GraphQLContext): string {
  if (!context.analyticsRunId) {
    throw new Error("analytics run was not pinned - Viewer resolver must run before Analytics fields");
  }
  return context.analyticsRunId;
}

/**
 * No single stored field carries timeZone at the run-metadata level; read it
 * from the user's configuration, matching context_for_user's homeTimeZone
 * resolution order (stored analyticsConfig, then the HEALTH_HOME_TIME_ZONE
 * env var, then "UTC").
 */
async function resolveHomeTimeZone(context: GraphQLContext): Promise<string> {
  const user = await findUserById(context.controlDb, context.user.userId);
  const configured = user?.analyticsConfig ?? {};
  return (configured.homeTimeZone as string) ?? process.env.HEALTH_HOME_TIME_ZONE ?? "UTC";
}

// -- MetricValue-shaped envelope builder for the various plain-dict fields --
function metricValue(raw: Record<string, unknown> | null | undefined) {
  if (!raw) {
    return { status: "MISSING", value: null, unit: null, source: null, note: null, qualityFlags: [] };
  }
  return {
    status: toMetricStatus(raw.status),
    value: typeof raw.value === "number" ? raw.value : null,
    unit: (raw.unit as string) ?? null,
    source: (raw.source as string) ?? null,
    note: (raw.note as string) ?? null,
    qualityFlags: Array.isArray(raw.qualityFlags) ? raw.qualityFlags : [],
  };
}

const STAGE_NAMES = ["deep", "light", "rem", "asleep", "awake", "unknown"] as const;

function stageMinutes(raw: Record<string, unknown> | null | undefined) {
  if (!raw) return null;
  const result: Record<string, number> = {};
  for (const name of STAGE_NAMES) {
    result[name] = typeof raw[name] === "number" ? (raw[name] as number) : 0;
  }
  return result;
}

function sleepEventSummary(raw: Record<string, unknown> | null | undefined) {
  if (!raw) return null;
  return {
    id: raw.id,
    role: raw.role === "main" ? "MAIN" : "SUPPLEMENTAL",
    source: raw.source ?? null,
    startAt: raw.startAt,
    endAt: raw.endAt,
    windowMinutes: raw.windowMinutes,
    sleepMinutes: raw.sleepMinutes,
    stageDataStatus: raw.stageDataStatus,
    qualityFlags: raw.qualityFlags ?? null,
    recordingCount: raw.recordingCount ?? null,
  };
}

function sleepDurationMetric(raw: Record<string, unknown> | null | undefined) {
  if (!raw) {
    return { status: "MISSING", value: null, unit: null, note: null, source: null };
  }
  return {
    status: toMetricStatus(raw.status),
    value: (raw.value as number) ?? null,
    unit: (raw.unit as string) ?? null,
    note: (raw.note as string) ?? null,
    source: (raw.source as string) ?? null,
    window: (raw.window as { startAt: string; endAt: string } | undefined) ?? null,
    windowScope: (raw.windowScope as string) ?? null,
    valueScope: (raw.valueScope as string) ?? null,
    stageMinutes: stageMinutes(raw.stageMinutes as Record<string, unknown> | undefined),
    stageDataStatus: (raw.stageDataStatus as string) ?? null,
    unclassifiedSleepMinutes: (raw.unclassifiedSleepMinutes as number) ?? null,
    eventCount: (raw.eventCount as number) ?? null,
    recordingCount: (raw.recordingCount as number) ?? null,
    mainEvent: sleepEventSummary(raw.mainEvent as Record<string, unknown> | undefined),
    events: Array.isArray(raw.events) ? raw.events.map(sleepEventSummary) : [],
  };
}

function recoveryMetric(raw: Record<string, unknown> | null | undefined) {
  if (!raw) {
    return { status: "MISSING", value: null, unit: null, note: null, source: null, qualityFlags: [] };
  }
  return {
    status: toMetricStatus(raw.status),
    value: (raw.value as number) ?? null,
    unit: (raw.unit as string) ?? null,
    note: (raw.note as string) ?? null,
    source: (raw.source as string) ?? null,
    qualityFlags: Array.isArray(raw.qualityFlags) ? raw.qualityFlags : [],
    modelVersion: (raw.modelVersion as string) ?? null,
    provisional: (raw.provisional as boolean) ?? null,
    band: raw.band ? String(raw.band).toUpperCase() : null,
    components: raw.components ?? null,
    quality: raw.quality ?? null,
  };
}

function strainMetric(raw: Record<string, unknown> | null | undefined) {
  if (!raw) {
    return { status: "MISSING", value: null, unit: null, note: null, source: null, qualityFlags: [] };
  }
  return {
    status: toMetricStatus(raw.status),
    value: (raw.value as number) ?? null,
    unit: (raw.unit as string) ?? null,
    note: (raw.note as string) ?? null,
    source: (raw.source as string) ?? null,
    qualityFlags: Array.isArray(raw.qualityFlags) ? raw.qualityFlags : [],
    modelVersion: (raw.modelVersion as string) ?? null,
    quality: raw.quality ?? null,
  };
}

function dayFromView(dateArg: string, dayView: Record<string, unknown> | null | undefined) {
  if (!dayView) return null;
  const headline = (dayView.headlineScores as Record<string, unknown>) ?? {};
  const supporting = (dayView.supportingMetrics as Record<string, unknown>) ?? {};
  const timeline = (dayView.timeline as Record<string, unknown>) ?? {};
  const heartRateTimeline = (timeline.heartRate as Record<string, unknown>) ?? {};
  const now = (timeline.now as Record<string, unknown>) ?? {};
  return {
    contractVersion: dayView.contractVersion ?? "health-day-v1",
    date: dayView.date ?? dateArg,
    timeZone: dayView.timeZone,
    dayState: String(dayView.dayState ?? "recorded").toUpperCase(),
    generatedAt: dayView.generatedAt ?? null,
    headlineScores: {
      sleepDuration: sleepDurationMetric(headline.sleepDuration as Record<string, unknown>),
      sleepNeed: metricValue(headline.sleepNeed as Record<string, unknown>),
      recovery: recoveryMetric(headline.recovery as Record<string, unknown>),
      strain: strainMetric(headline.strain as Record<string, unknown>),
      strainTarget: metricValue(headline.strainTarget as Record<string, unknown>),
    },
    supportingMetrics: {
      hrv: metricValue(supporting.hrv as Record<string, unknown>),
      restingHeartRate: metricValue(supporting.restingHeartRate as Record<string, unknown>),
      respiratoryRate: metricValue(supporting.respiratoryRate as Record<string, unknown>),
      oxygenSaturation: metricValue(supporting.oxygenSaturation as Record<string, unknown>),
      skinTemperatureDeviation: metricValue(supporting.skinTemperatureDeviation as Record<string, unknown>),
      steps: metricValue(supporting.steps as Record<string, unknown>),
      calories: metricValue(supporting.calories as Record<string, unknown>),
      zone3AndAbove: metricValue(supporting.zone3AndAbove as Record<string, unknown>),
    },
    timeline: {
      heartRate: {
        status: toMetricStatus(heartRateTimeline.status),
        source: heartRateTimeline.source ?? null,
        sampleCount: heartRateTimeline.sampleCount ?? 0,
        observedMinuteCount: heartRateTimeline.observedMinuteCount ?? 0,
        hours: Array.isArray(heartRateTimeline.hours)
          ? (heartRateTimeline.hours as Array<Record<string, unknown>>).map((hour) => ({
              hour: hour.hour,
              status: toMetricStatus(hour.status),
              sampleCount: hour.sampleCount ?? 0,
              min: hour.min ?? null,
              p25: hour.p25 ?? null,
              mean: hour.mean ?? null,
              p75: hour.p75 ?? null,
              max: hour.max ?? null,
            }))
          : [],
        note: heartRateTimeline.note ?? null,
      },
      strain: timeline.strain ?? [],
      sleepStages: timeline.sleepStages ?? [],
      steps: Array.isArray(timeline.steps)
        ? (timeline.steps as Array<Record<string, unknown>>).map((bucket) => ({
            hour: bucket.hour,
            count: bucket.count,
            status: toMetricStatus(bucket.status),
          }))
        : [],
      // DEVIATION: day_dashboard.py's workouts use key "type" (not
      // "exerciseType") and always populate typeLabel - see schema comment.
      workouts: timeline.workouts ?? [],
      schedule: metricValue(timeline.schedule as Record<string, unknown>),
      targetWakeTime: metricValue(timeline.targetWakeTime as Record<string, unknown>),
      targetBedTime: metricValue(timeline.targetBedTime as Record<string, unknown>),
      now: {
        status: toMetricStatus(now.status),
        latestObservedAt: now.latestObservedAt ?? null,
        note: now.note ?? null,
      },
    },
    heartRateZones: metricValue(dayView.heartRateZones as Record<string, unknown>),
    notes: dayView.notes ?? [],
  };
}

/**
 * Mirrors day_dashboard.py's `empty_day()` exactly: a date with no prepared
 * dayView still returns a fully-shaped Day (never null) with every metric
 * explicitly MISSING/INSUFFICIENT_DATA/BLOCKED/NOT_IMPLEMENTED and its note,
 * distinguishing a future date (dayState FUTURE) from a recorded date with
 * no prepared data (dayState RECORDED, with a "no_day_data" note) - matching
 * GET /api/v2/analytics/day's behavior for out-of-range or ungenerated dates.
 */
function synthesizeEmptyDay(date: string, homeTimeZone: string, processedAt: Date | null) {
  const todayLocal = new Date().toLocaleDateString("en-CA", { timeZone: homeTimeZone || "UTC" }); // en-CA => YYYY-MM-DD
  const state = date > todayLocal ? "FUTURE" : "RECORDED";
  const missingMetric = (unit: string | null, note: string) => ({
    status: "MISSING",
    value: null,
    unit,
    note,
    source: null,
    qualityFlags: [] as string[],
  });
  return {
    contractVersion: "health-day-v1",
    date,
    timeZone: homeTimeZone,
    dayState: state,
    generatedAt: processedAt,
    headlineScores: {
      sleepDuration: {
        status: "MISSING",
        value: null,
        unit: "minutes",
        note: "No sleep ending on this date was recorded.",
        source: null,
        window: null,
        windowScope: null,
        valueScope: null,
        stageMinutes: null,
        stageDataStatus: null,
        unclassifiedSleepMinutes: null,
        eventCount: null,
        recordingCount: null,
        mainEvent: null,
        events: [],
      },
      sleepNeed: missingMetric("percent", "No sleep record is available to compare with the configured target."),
      recovery: {
        status: "INSUFFICIENT_DATA",
        value: null,
        unit: "score_0_100",
        note: "The provisional Recovery model needs sleep plus a calibrated RHR or HRV baseline.",
        source: null,
        qualityFlags: [],
        modelVersion: null,
        provisional: null,
        band: null,
        components: null,
        quality: null,
      },
      strain: {
        status: "MISSING",
        value: null,
        unit: "score_0_21",
        note: "No usable heart-rate strain result is available for this day.",
        source: null,
        qualityFlags: [],
        modelVersion: null,
        quality: null,
      },
      strainTarget: {
        status: "BLOCKED",
        value: null,
        unit: "score_0_21",
        note: "A strain target depends on a trustworthy Recovery score.",
        source: null,
        qualityFlags: [],
      },
    },
    supportingMetrics: {
      hrv: missingMetric("ms", "No heart-rate-variability RMSSD measurement was recorded for this day."),
      restingHeartRate: missingMetric("bpm", "No resting-heart-rate measurement was recorded for this day."),
      respiratoryRate: missingMetric("breaths_per_minute", "No respiratory-rate measurement was recorded for this day."),
      oxygenSaturation: missingMetric("percent", "No oxygen-saturation measurement was recorded for this day."),
      skinTemperatureDeviation: missingMetric("celsius_delta", "Skin temperature is not currently present in the database."),
      steps: missingMetric("steps", "No steps were recorded for this day."),
      calories: missingMetric("kcal", "No calorie total was recorded for this day."),
      zone3AndAbove: missingMetric("minutes", "Heart-rate zones are not calibrated for this day."),
    },
    timeline: {
      heartRate: {
        status: "MISSING",
        source: null,
        sampleCount: 0,
        observedMinuteCount: 0,
        hours: [],
        note: "No heart-rate samples were recorded for this day.",
      },
      strain: [],
      sleepStages: [],
      steps: [],
      workouts: [],
      schedule: {
        status: "NOT_IMPLEMENTED",
        value: null,
        unit: null,
        note: "No calendar, location inference, or user-declared routine source is connected.",
        source: null,
        qualityFlags: [],
      },
      targetWakeTime: missingMetric("local_time", "No wake-time preference or phone-alarm integration exists."),
      targetBedTime: missingMetric("local_time", "No bedtime preference exists; sleep target duration is stored separately."),
      now: {
        status: "MISSING",
        latestObservedAt: null,
        note: "Raw records do not include a server receipt timestamp, so transport lag cannot be measured.",
      },
    },
    heartRateZones: {
      status: "MISSING",
      value: null,
      unit: null,
      note: "No personal zone thresholds or lactate-threshold test date are configured.",
      source: null,
      qualityFlags: [],
    },
    notes: state === "RECORDED" ? [{ code: "no_day_data", message: "No prepared metrics exist for this date." }] : [],
  };
}

function sleepEventType(raw: Record<string, unknown>) {
  return {
    id: raw.id,
    date: raw.date,
    timeZone: raw.timeZone,
    localStartAt: raw.localStartAt,
    localEndAt: raw.localEndAt,
    role: raw.role === "main" ? "MAIN" : "SUPPLEMENTAL",
    primary: sleepSessionFromEmbedded(raw.primary as Record<string, unknown>),
    recordings: Array.isArray(raw.recordings) ? raw.recordings.map(sleepSessionFromEmbedded) : [],
    recordingCount: raw.recordingCount,
    windowMinutes: raw.windowMinutes,
    sleepMinutes: raw.sleepMinutes,
    stageMinutes: stageMinutes(raw.stageMinutes as Record<string, unknown>),
    stageCoverageMinutes: raw.stageCoverageMinutes,
    stageCoverageRatio: raw.stageCoverageRatio,
    detailedStageCoverageRatio: raw.detailedStageCoverageRatio,
    stageDataStatus: raw.stageDataStatus,
    hasCredibleStageTimeline: raw.hasCredibleStageTimeline,
    hasCredibleDetailedStages: raw.hasCredibleDetailedStages,
    qualityFlags: raw.qualityFlags ?? [],
    primarySelection: raw.primarySelection,
  };
}

// Sleep sessions embedded inside a prepared SleepEvent (primary/recordings)
// already carry the same shape repository.py's _sleep() mapper produces
// (id/source/startAt/endAt/title/notes/stages/sourceZoneOffsets), captured
// verbatim at analytics-build time - reshape stage `kind` to the enum only.
function sleepSessionFromEmbedded(raw: Record<string, unknown> | undefined) {
  if (!raw) return null;
  const stages = Array.isArray(raw.stages)
    ? (raw.stages as Array<Record<string, unknown>>).map((stage) => ({
        startAt: stage.startAt,
        endAt: stage.endAt,
        kind: String(stage.kind ?? "unknown").toUpperCase(),
      }))
    : [];
  return {
    id: raw.id,
    source: raw.source,
    startAt: raw.startAt,
    endAt: raw.endAt,
    title: raw.title ?? null,
    notes: raw.notes ?? null,
    stages,
    sourceZoneOffsets: raw.sourceZoneOffsets ?? null,
  };
}

function debtCategory(raw: unknown) {
  return String(raw ?? "none").toUpperCase();
}

function consistencyCategory(raw: unknown) {
  return raw ? String(raw).toUpperCase() : null;
}

function analyticsNodePrefix(context: GraphQLContext): string {
  return `${context.user.userId}:${requireRunId(context)}`;
}

function datedNodeId(context: GraphQLContext, parent: { date: unknown }): string {
  return `${analyticsNodePrefix(context)}:${String(parent.date)}`;
}

function metricNodeId(
  context: GraphQLContext,
  parent: { __metricKey?: unknown; date?: unknown; month?: unknown },
): string {
  const bucket = parent.date ?? parent.month;
  return `${analyticsNodePrefix(context)}:${String(parent.__metricKey)}:${String(bucket)}`;
}

function withMetricKey(raw: unknown, metricKey: string): Record<string, unknown> | null {
  if (!raw || typeof raw !== "object") return null;
  return { ...(raw as Record<string, unknown>), __metricKey: metricKey };
}

export const analyticsResolvers = {
  HealthspanStatus: {
    CALIBRATING: "calibrating",
    PARTIAL: "partial",
    READY: "ready",
  },
  HealthspanFactorKey: {
    SLEEP_DURATION: "sleep_duration",
    SLEEP_CONSISTENCY: "sleep_consistency",
    STEPS: "steps",
    RESTING_HEART_RATE: "resting_heart_rate",
  },
  HealthspanFactorUnit: {
    MINUTES: "minutes",
    PERCENT: "percent",
    STEPS: "steps",
    BPM: "bpm",
  },
  MetricUnit: {
    STEPS: "steps",
    KCAL: "kcal",
    BPM: "bpm",
    KG: "kg",
    MS: "ms",
  },
  Viewer: {
    analytics: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      // Pin runId once per request here, per the design doc's Viewer
      // contract, so every Analytics.* field below reads the same run.
      if (!context.analyticsRunId) {
        const current = await context.withTimeout(readCurrentRun(context.userDb), "viewer.analytics");
        if (!current) {
          throw new Error("Analytics are not ready for this account yet (no completed run found).");
        }
        context.analyticsRunId = current.runId;
      }
      return {};
    },
  },
  Analytics: {
    id: (_parent: unknown, _args: unknown, context: GraphQLContext) => analyticsNodePrefix(context),
    runId: (_parent: unknown, _args: unknown, context: GraphQLContext) => requireRunId(context),
    algorithmVersion: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const meta = await context.withTimeout(readCurrentMetadata(context.userDb), "analytics.algorithmVersion");
      return meta?.algorithmVersion ?? "";
    },
    timeZone: async (_parent: unknown, _args: unknown, context: GraphQLContext) => resolveHomeTimeZone(context),
    processedAt: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const meta = await context.withTimeout(readCurrentMetadata(context.userDb), "analytics.processedAt");
      return meta?.completedAt ?? null;
    },
    day: async (
      _parent: unknown,
      args: { date: string; radius?: number },
      context: GraphQLContext,
    ) => {
      const runId = requireRunId(context);
      const radius = args.radius ?? 0;
      if (radius < 0 || radius > 7) {
        throw new Error("radius must be from 0 to 7");
      }
      const center = new Date(`${args.date}T00:00:00Z`);
      const start = new Date(center);
      start.setUTCDate(start.getUTCDate() - radius);
      const end = new Date(center);
      end.setUTCDate(end.getUTCDate() + radius);
      const startDate = start.toISOString().slice(0, 10);
      const endDate = end.toISOString().slice(0, 10);
      const meta = await context.withTimeout(readCurrentMetadata(context.userDb), "analytics.day");
      const homeTimeZone = await resolveHomeTimeZone(context);
      const processedAt = meta?.completedAt ?? null;
      let dayView: Record<string, unknown> | null | undefined;
      if (radius === 0) {
        const doc = await context.withTimeout(readDailyOne(context.userDb, runId, args.date), "analytics.day");
        dayView = doc?.dayView;
      } else {
        const docs = await context.withTimeout(readDaily(context.userDb, runId, startDate, endDate), "analytics.day");
        const byDate = new Map(docs.map((doc) => [doc.date, doc.dayView]));
        dayView = byDate.get(args.date);
      }
      // Mirrors GET /api/v2/analytics/day: a date with no prepared dayView
      // still returns a fully-shaped Day (never null), matching
      // day_dashboard.py's empty_day() fallback exactly.
      return dayFromView(args.date, dayView) ?? synthesizeEmptyDay(args.date, homeTimeZone, processedAt);
    },
    days: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const range = args.range;
      const startDate = range ? isoDate(range.start) : undefined;
      // endExclusive is exclusive; _analytics_daily's date filter is
      // inclusive ($lte), so subtract a day when translating.
      const endDate = range ? isoDate(new Date(range.endExclusive.getTime() - 86_400_000)) : undefined;
      const docs = await context.withTimeout(readDaily(context.userDb, runId, startDate, endDate), "analytics.days");
      return docs.map((doc) => dayFromView(doc.date, doc.dayView)).filter((day) => day !== null);
    },
    sleepEvents: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const events = await context.withTimeout(readSleepEvents(context.userDb, runId), "analytics.sleepEvents");
      return events
        .filter((event) => withinDateRange(event.date as string, args.range))
        .map(sleepEventType);
    },
    sleepDebt: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const summary = await context.withTimeout(readSummary(context.userDb, runId, "sleepDebt"), "analytics.sleepDebt");
      return sleepDebtSummary(summary, runId, context);
    },
    sleepConsistency: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const summary = await context.withTimeout(
        readSummary(context.userDb, runId, "sleepConsistency"),
        "analytics.sleepConsistency",
      );
      return sleepConsistencySummary(summary, runId, context);
    },
    healthspan: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const summary = await context.withTimeout(readSummary(context.userDb, runId, "healthspan"), "analytics.healthspan");
      return healthspanSummary(summary, runId, context);
    },
    deviceSleepComparisons: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      return context.withTimeout(readDeviceComparisons(context.userDb, runId), "analytics.deviceSleepComparisons");
    },
    steps: async (_parent: unknown, _args: unknown, context: GraphQLContext) => metricSeriesRoot(context, "steps"),
    activeCalories: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      metricSeriesRoot(context, "activeCalories"),
    totalCalories: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      metricSeriesRoot(context, "totalCalories"),
    restingHeartRate: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      metricSeriesRoot(context, "restingHeartRate"),
    heartRateVariability: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      metricSeriesRoot(context, "heartRateVariability"),
    weight: async (_parent: unknown, _args: unknown, context: GraphQLContext) => metricSeriesRoot(context, "weight"),
    strain: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const summary = await context.withTimeout(readSummary(context.userDb, runId, "strain"), "analytics.strain");
      return strainSummary(summary, runId, context);
    },
    recovery: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const runId = requireRunId(context);
      const summary = await context.withTimeout(readSummary(context.userDb, runId, "recovery"), "analytics.recovery");
      return recoverySummary(summary, runId, context);
    },
  },
  Day: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  SleepDebtDay: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  SleepConsistencyDay: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  HealthspanDay: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  HealthspanFactor: {
    id: (
      parent: { __date: unknown; key: unknown },
      _args: unknown,
      context: GraphQLContext,
    ) => `${analyticsNodePrefix(context)}:${String(parent.__date)}:${String(parent.key)}`,
  },
  MetricDay: {
    id: (
      parent: { __metricKey?: unknown; date?: unknown },
      _args: unknown,
      context: GraphQLContext,
    ) => metricNodeId(context, parent),
  },
  RollingPoint: {
    id: (
      parent: { __metricKey?: unknown; date?: unknown },
      _args: unknown,
      context: GraphQLContext,
    ) => metricNodeId(context, parent),
  },
  MonthlyPoint: {
    id: (
      parent: { __metricKey?: unknown; month?: unknown },
      _args: unknown,
      context: GraphQLContext,
    ) => metricNodeId(context, parent),
  },
  StrainDay: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  RecoveryDay: {
    id: (parent: { date: unknown }, _args: unknown, context: GraphQLContext) => datedNodeId(context, parent),
  },
  SleepDebtSummary: {
    daily: (parent: { __daily: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__daily.filter((day) => withinDateRange(day.date as string, args.range)).map(sleepDebtDay),
    latest: (parent: { __daily: Record<string, unknown>[] }) => {
      const latest = parent.__daily[parent.__daily.length - 1];
      return latest ? sleepDebtDay(latest) : null;
    },
  },
  SleepConsistencySummary: {
    daily: (parent: { __daily: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__daily.filter((day) => withinDateRange(day.date as string, args.range)).map(sleepConsistencyDay),
    latest: (parent: { __daily: Record<string, unknown>[] }) => {
      const latest = [...parent.__daily].reverse().find((day) => day.score != null);
      return latest ? sleepConsistencyDay(latest) : null;
    },
  },
  HealthspanSummary: {
    trend: (parent: { __trend: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__trend.filter((day) => withinDateRange(day.date as string, args.range)).map(healthspanDay),
  },
  StrainSummary: {
    daily: (parent: { __daily: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__daily.filter((day) => withinDateRange(day.date as string, args.range)).map(strainDay),
    workouts: (parent: { __workouts: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__workouts
        .filter((workout) => withinInstantRange(workout.startAt as string, args.range))
        .map(strainWorkout),
  },
  RecoverySummary: {
    daily: (parent: { __daily: Record<string, unknown>[] }, args: { range?: TimeRangeInput }) =>
      parent.__daily.filter((day) => withinDateRange(day.date as string, args.range)).map(recoveryDay),
  },
  MetricSeries: {
    daily: (
      parent: { __daily: Record<string, unknown>[]; __metricKey: string },
      args: { range?: TimeRangeInput },
    ) => parent.__daily
      .filter((day) => withinDateRange(day.date as string, args.range))
      .map((day) => ({ ...day, __metricKey: parent.__metricKey })),
    rolling7Day: (
      parent: { __rolling: Record<string, unknown>[]; __metricKey: string },
      args: { range?: TimeRangeInput },
    ) => parent.__rolling
      .filter((day) => withinDateRange(day.date as string, args.range))
      .map((day) => ({ ...day, __metricKey: parent.__metricKey })),
  },
};

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function sleepDebtDay(raw: Record<string, unknown>) {
  return {
    date: raw.date,
    sleepMinutes: raw.sleepMinutes,
    targetMinutes: raw.targetMinutes,
    debtMinutes: raw.debtMinutes,
    surplusMinutes: raw.surplusMinutes,
    category: debtCategory(raw.category),
    rolling7DayAverageMinutes: raw.rolling7DayAverageMinutes ?? null,
    rolling7DayTotalMinutes: raw.rolling7DayTotalMinutes ?? null,
    rolling30DayAverageMinutes: raw.rolling30DayAverageMinutes ?? null,
  };
}

function sleepConsistencyDay(raw: Record<string, unknown>) {
  return {
    date: raw.date,
    source: raw.source ?? null,
    bedtimeAt: raw.bedtimeAt ?? null,
    wakeAt: raw.wakeAt ?? null,
    bedtimeMinutesLocal: raw.bedtimeMinutesLocal ?? null,
    wakeMinutesLocal: raw.wakeMinutesLocal ?? null,
    baselineBedtimeMinutesLocal: raw.baselineBedtimeMinutesLocal ?? null,
    baselineWakeMinutesLocal: raw.baselineWakeMinutesLocal ?? null,
    bedtimeDeviationMinutes: raw.bedtimeDeviationMinutes ?? null,
    wakeDeviationMinutes: raw.wakeDeviationMinutes ?? null,
    baselineNightCount: raw.baselineNightCount,
    score: raw.score ?? null,
    category: consistencyCategory(raw.category),
    rolling7DayAverageScore: raw.rolling7DayAverageScore ?? null,
    rolling30DayAverageScore: raw.rolling30DayAverageScore ?? null,
    qualityFlags: raw.qualityFlags ?? [],
  };
}

function healthspanDay(raw: Record<string, unknown>) {
  return {
    date: raw.date,
    chronologicalAgeYears: raw.chronologicalAgeYears ?? null,
    healthAgeYears: raw.healthAgeYears ?? null,
    ageDeltaYears: raw.ageDeltaYears ?? null,
    paceOfAging: raw.paceOfAging ?? null,
    factors: Array.isArray(raw.factors)
      ? (raw.factors as Record<string, unknown>[]).map((factor) => ({ ...factor, __date: raw.date }))
      : [],
    qualityFlags: raw.qualityFlags ?? [],
  };
}

function strainDay(raw: Record<string, unknown>) {
  return {
    date: raw.date,
    score: raw.score ?? null,
    loadMinutes: raw.loadMinutes,
    zoneMinutes: raw.zoneMinutes,
    timeline: raw.timeline ?? [],
    quality: raw.quality,
  };
}

function strainWorkout(raw: Record<string, unknown>) {
  return {
    id: raw.id ?? null,
    startAt: raw.startAt,
    endAt: raw.endAt,
    score: raw.score ?? null,
    loadMinutes: raw.loadMinutes,
    zoneMinutes: raw.zoneMinutes,
    timeline: raw.timeline ?? [],
    quality: raw.quality,
  };
}

function recoveryDay(raw: Record<string, unknown>) {
  return {
    date: raw.date,
    score: raw.score ?? null,
    band: raw.band ? String(raw.band).toUpperCase() : null,
    status: raw.status,
    provisional: raw.provisional,
    components: raw.components ?? {},
    quality: raw.quality,
  };
}

async function sleepDebtSummary(summary: Record<string, unknown> | null, runId: string, context: GraphQLContext) {
  const daily = await context.withTimeout(readDaily(context.userDb, runId), "analytics.sleepDebt.daily");
  const dailyRows = daily.map((doc) => doc.sleepDebt).filter(Boolean) as Record<string, unknown>[];
  return {
    targetMinutes: summary?.targetMinutes ?? 0,
    methodology: summary?.methodology ?? "",
    average7DayMinutes: summary?.average7DayMinutes ?? null,
    average30DayMinutes: summary?.average30DayMinutes ?? null,
    previous30DayAverageMinutes: summary?.previous30DayAverageMinutes ?? null,
    breakdown30Day: summary?.breakdown30Day ?? null,
    __daily: dailyRows,
  };
}

async function sleepConsistencySummary(summary: Record<string, unknown> | null, runId: string, context: GraphQLContext) {
  const daily = await context.withTimeout(readDaily(context.userDb, runId), "analytics.sleepConsistency.daily");
  const dailyRows = daily.map((doc) => doc.sleepConsistency).filter(Boolean) as Record<string, unknown>[];
  return {
    baselineWindowDays: summary?.baselineWindowDays ?? 0,
    minimumBaselineNights: summary?.minimumBaselineNights ?? 0,
    methodology: summary?.methodology ?? "",
    average7DayScore: summary?.average7DayScore ?? null,
    average30DayScore: summary?.average30DayScore ?? null,
    previous30DayAverageScore: summary?.previous30DayAverageScore ?? null,
    breakdown30Day: summary?.breakdown30Day ?? null,
    __daily: dailyRows,
  };
}

async function healthspanSummary(summary: Record<string, unknown> | null, runId: string, context: GraphQLContext) {
  const daily = await context.withTimeout(readDaily(context.userDb, runId), "analytics.healthspan.trend");
  const trendRows = daily.map((doc) => doc.healthspan).filter(Boolean) as Record<string, unknown>[];
  return {
    modelVersion: summary?.modelVersion ?? "",
    status: summary?.status ?? "calibrating",
    birthDateConfigured: summary?.birthDateConfigured ?? false,
    methodology: summary?.methodology ?? "",
    calibrationReasons: summary?.calibrationReasons ?? [],
    latest: trendRows.length ? healthspanDay(trendRows[trendRows.length - 1]) : null,
    paceOfAging: summary?.paceOfAging ?? null,
    paceWindowDays: summary?.paceWindowDays ?? null,
    __trend: trendRows,
  };
}

async function strainSummary(summary: Record<string, unknown> | null, runId: string, context: GraphQLContext) {
  const daily = await context.withTimeout(readDaily(context.userDb, runId), "analytics.strain.daily");
  const dailyRows = daily.map((doc) => doc.strain).filter(Boolean) as Record<string, unknown>[];
  // Each _analytics_daily document now carries a `strainWorkouts` list
  // (store.py's _strain_workouts_by_date) - full per-workout strain detail
  // grouped onto every local date the workout's start or end touches, the
  // same membership rule day_dashboard.py's Day.timeline.workouts already
  // uses. A workout spanning local midnight therefore appears on two
  // documents; dedupe by id (falling back to identity) so this whole-history
  // field returns each workout exactly once regardless of date span.
  const seen = new Set<unknown>();
  const workoutRows: Record<string, unknown>[] = [];
  for (const doc of daily) {
    const workouts = (doc.strainWorkouts as Record<string, unknown>[] | undefined) ?? [];
    for (const workout of workouts) {
      const key = workout.id ?? workout;
      if (seen.has(key)) continue;
      seen.add(key);
      workoutRows.push(workout);
    }
  }
  return {
    algorithmVersion: summary?.algorithmVersion ?? "",
    status: summary?.status ?? "unavailable",
    methodology: summary?.methodology ?? "",
    limitations: summary?.limitations ?? [],
    provisional: summary?.provisional ?? true,
    timeZone: summary?.timeZone ?? "UTC",
    calibration: summary?.calibration ?? { available: false, reasons: [] },
    availability: summary?.availability ?? { available: false, reasons: [] },
    source: summary?.source ?? null,
    __daily: dailyRows,
    __workouts: workoutRows,
  };
}

async function recoverySummary(summary: Record<string, unknown> | null, runId: string, context: GraphQLContext) {
  const daily = await context.withTimeout(readDaily(context.userDb, runId), "analytics.recovery.daily");
  const dailyRows = daily.map((doc) => doc.recovery).filter(Boolean) as Record<string, unknown>[];
  return {
    algorithmVersion: summary?.algorithmVersion ?? "",
    status: summary?.status ?? "insufficient_data",
    provisional: summary?.provisional ?? true,
    methodology: summary?.methodology ?? "",
    limitations: summary?.limitations ?? [],
    weights: summary?.weights ?? { sleep: 0, hrv: 0, restingHeartRate: 0, sleepConsistency: 0 },
    availability: summary?.availability ?? { available: false, publishableDayCount: 0, completeDayCount: 0, reasons: [] },
    __daily: dailyRows,
  };
}

async function metricSeriesRoot(context: GraphQLContext, key: string) {
  const runId = requireRunId(context);
  const overviews = await context.withTimeout(readSummary(context.userDb, runId, "metricOverviews"), `analytics.${key}`);
  const metricOverview = (overviews?.[key] as Record<string, unknown>) ?? {};
  const daily = await context.withTimeout(readDaily(context.userDb, runId), `analytics.${key}.daily`);
  const dailyRows = daily.map((doc) => (doc as unknown as Record<string, unknown>)[key]).filter(Boolean) as Record<
    string,
    unknown
  >[];
  const overview = (metricOverview.overview as Record<string, unknown> | undefined) ?? { sampleCount: 0 };
  const canonicalUnits: Record<string, string> = {
    steps: "steps",
    activeCalories: "kcal",
    totalCalories: "kcal",
    restingHeartRate: "bpm",
    heartRateVariability: "ms",
    weight: "kg",
  };
  return {
    // A completed run normally always has metricOverviews, including empty
    // series. Retain the resolver's existing tolerance for a missing summary
    // without inventing a measured value: the unit is structural metadata
    // fixed by the selected metric field itself.
    unit: metricOverview.unit ?? canonicalUnits[key],
    overview: {
      ...overview,
      latest: withMetricKey(overview.latest, key),
      previous: withMetricKey(overview.previous, key),
    },
    monthly: Array.isArray(metricOverview.monthly)
      ? (metricOverview.monthly as Record<string, unknown>[]).map((month) => ({ ...month, __metricKey: key }))
      : [],
    __metricKey: key,
    __daily: dailyRows,
    __rolling: metricOverview.rolling7Day ?? [],
  };
}
