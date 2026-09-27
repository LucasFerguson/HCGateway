import { Db, Document } from "mongodb";

/**
 * Prepared-analytics reading, mirroring api/analytics_engine/store.py's
 * read_snapshot/read_daily/read_sleep_events and the SUMMARIES/DAILY/
 * DEVICE_COMPARISONS collection constants exactly. All `data` fields are
 * plain JS objects post plaintext-BSON-cutover - no decode/decrypt step,
 * per doc/session-handoff.md.
 *
 * Every read here is scoped to one pinned runId, read once per request in
 * context.ts, so two analytics fields in the same GraphQL operation can
 * never straddle two different completed runs even if the worker finishes
 * mid-request (doc/graphql-schema-design.md's Viewer resolver contract).
 */

const RUNS = "_analytics_runs";
const CURRENT = "_analytics_current";
const DAILY = "_analytics_daily";
const SLEEP_EVENTS = "_analytics_sleep_events";
const DEVICE_COMPARISONS = "_analytics_device_comparisons";
const SUMMARIES = "_analytics_summaries";

export interface CurrentRunDoc extends Document {
  _id: "current";
  runId: string;
  algorithmVersion: string;
  sourceFingerprint: string;
  configurationFingerprint: string;
  completedAt: Date;
}

export interface RunMetadata {
  runId: string;
  algorithmVersion: string;
  sourceFingerprint: string;
  configurationFingerprint: string;
  completedAt: Date;
  counts: Record<string, unknown>;
  issueCount: number;
}

interface RunDoc extends Document {
  _id: string;
  counts?: Record<string, unknown>;
  issueCount?: number;
}

export async function readCurrentRun(db: Db): Promise<CurrentRunDoc | null> {
  return db.collection<CurrentRunDoc>(CURRENT).findOne({ _id: "current" } as Partial<CurrentRunDoc>);
}

export async function readCurrentMetadata(db: Db): Promise<RunMetadata | null> {
  const current = await readCurrentRun(db);
  if (!current) return null;
  const run = await db
    .collection<RunDoc>(RUNS)
    .findOne({ _id: current.runId } as Partial<RunDoc>, { projection: { issues: 0 } });
  return {
    runId: current.runId,
    algorithmVersion: current.algorithmVersion,
    sourceFingerprint: current.sourceFingerprint,
    configurationFingerprint: current.configurationFingerprint,
    completedAt: current.completedAt,
    counts: (run?.counts as Record<string, unknown>) ?? {},
    issueCount: (run?.issueCount as number) ?? 0,
  };
}

/** One row from _analytics_daily's `data` field - see store.py's _daily_documents. */
export interface DailyDocument extends Document {
  date: string;
  sleep?: unknown;
  sleepDebt?: unknown;
  sleepConsistency?: unknown;
  healthspan?: unknown;
  steps?: unknown;
  activeCalories?: unknown;
  totalCalories?: unknown;
  restingHeartRate?: unknown;
  heartRateVariability?: unknown;
  weight?: unknown;
  strain?: unknown;
  strainWorkouts?: unknown;
  recovery?: unknown;
  dayView?: Record<string, unknown> | null;
}

export interface DailyDateRange {
  startDate?: string;
  endDateExclusive?: string;
}

export type DailySeriesField =
  | "sleepDebt"
  | "sleepConsistency"
  | "healthspan"
  | "steps"
  | "activeCalories"
  | "totalCalories"
  | "restingHeartRate"
  | "heartRateVariability"
  | "weight"
  | "strain"
  | "recovery";

const DAILY_SERIES_FIELDS: DailySeriesField[] = [
  "sleepDebt",
  "sleepConsistency",
  "healthspan",
  "steps",
  "activeCalories",
  "totalCalories",
  "restingHeartRate",
  "heartRateVariability",
  "weight",
  "strain",
  "recovery",
];

function dailyQuery(runId: string, range: DailyDateRange = {}): Record<string, unknown> {
  const query: Record<string, unknown> = { runId };
  if (range.startDate || range.endDateExclusive) {
    const dateFilter: Record<string, string> = {};
    if (range.startDate) dateFilter.$gte = range.startDate;
    if (range.endDateExclusive) dateFilter.$lt = range.endDateExclusive;
    query.date = dateFilter;
  }
  return query;
}

function dailyData(doc: Document): DailyDocument {
  return { ...((doc.data as DailyDocument | undefined) ?? {}), date: String(doc.date) };
}

/**
 * Read the compact prepared series shared by the nested daily/trend fields.
 * The deliberately fixed projection lets one request-scoped read serve all
 * series without ever materializing the much larger dayView or workouts.
 */
export async function readDailySeries(
  db: Db,
  runId: string,
  range: DailyDateRange = {},
): Promise<DailyDocument[]> {
  const projection: Record<string, 0 | 1> = { _id: 0, date: 1 };
  for (const field of DAILY_SERIES_FIELDS) projection[`data.${field}`] = 1;
  const docs = await db
    .collection(DAILY)
    .find(dailyQuery(runId, range), { projection })
    .sort({ date: 1 })
    .toArray();
  return docs.map(dailyData);
}

export async function readDayViews(
  db: Db,
  runId: string,
  range: DailyDateRange = {},
): Promise<DailyDocument[]> {
  const docs = await db
    .collection(DAILY)
    .find(dailyQuery(runId, range), { projection: { _id: 0, date: 1, "data.dayView": 1 } })
    .sort({ date: 1 })
    .toArray();
  return docs.map(dailyData);
}

export async function readDayViewOne(db: Db, runId: string, date: string): Promise<DailyDocument | null> {
  const doc = await db.collection(DAILY).findOne(
    { runId, date },
    { projection: { _id: 0, date: 1, "data.dayView": 1 } },
  );
  return doc ? dailyData(doc) : null;
}

export async function readStrainWorkoutDays(
  db: Db,
  runId: string,
  range: DailyDateRange = {},
): Promise<DailyDocument[]> {
  const docs = await db
    .collection(DAILY)
    .find(dailyQuery(runId, range), { projection: { _id: 0, date: 1, "data.strainWorkouts": 1 } })
    .sort({ date: 1 })
    .toArray();
  return docs.map(dailyData);
}

export async function readLatestDailyField(
  db: Db,
  runId: string,
  field: DailySeriesField,
  requiredNestedField?: string,
): Promise<Record<string, unknown> | null> {
  const query: Record<string, unknown> = { runId, [`data.${field}`]: { $exists: true, $ne: null } };
  if (requiredNestedField) {
    query[`data.${field}.${requiredNestedField}`] = { $exists: true, $ne: null };
  }
  const doc = await db.collection(DAILY).findOne(query, {
    projection: { _id: 0, date: 1, [`data.${field}`]: 1 },
    sort: { date: -1 },
  });
  if (!doc) return null;
  return ((doc.data as Record<string, unknown> | undefined)?.[field] as Record<string, unknown> | undefined) ?? null;
}

export async function readSleepEvents(
  db: Db,
  runId: string,
  range: DailyDateRange = {},
): Promise<Record<string, unknown>[]> {
  const query = dailyQuery(runId, range);
  const docs = await db.collection(SLEEP_EVENTS).find(query).sort({ date: 1 }).toArray();
  return docs.map((doc) => doc.data as Record<string, unknown>);
}

export async function readDeviceComparisons(db: Db, runId: string): Promise<Record<string, unknown>[]> {
  const docs = await db.collection(DEVICE_COMPARISONS).find({ runId, metric: "sleep" }).toArray();
  return docs.map((doc) => doc.data as Record<string, unknown>);
}

export type SummaryKind =
  | "sleepDebt"
  | "sleepConsistency"
  | "healthspan"
  | "metricOverviews"
  | "strain"
  | "recovery";

export async function readSummary(db: Db, runId: string, kind: SummaryKind): Promise<Record<string, unknown> | null> {
  const doc = await db.collection(SUMMARIES).findOne({ runId, kind });
  return (doc?.data as Record<string, unknown>) ?? null;
}
