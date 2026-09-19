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
  recovery?: unknown;
  dayView?: Record<string, unknown> | null;
}

export async function readDaily(
  db: Db,
  runId: string,
  startDate?: string,
  endDate?: string,
): Promise<DailyDocument[]> {
  const query: Record<string, unknown> = { runId };
  if (startDate || endDate) {
    const dateFilter: Record<string, string> = {};
    if (startDate) dateFilter.$gte = startDate;
    if (endDate) dateFilter.$lte = endDate;
    query.date = dateFilter;
  }
  const docs = await db
    .collection(DAILY)
    .find(query)
    .sort({ date: 1 })
    .toArray();
  return docs.map((doc) => doc.data as DailyDocument);
}

export async function readDailyOne(db: Db, runId: string, date: string): Promise<DailyDocument | null> {
  const doc = await db.collection(DAILY).findOne({ runId, date });
  return (doc?.data as DailyDocument) ?? null;
}

export async function readSleepEvents(
  db: Db,
  runId: string,
  startDate?: string,
  endDate?: string,
): Promise<Record<string, unknown>[]> {
  const query: Record<string, unknown> = { runId };
  if (startDate || endDate) {
    const dateFilter: Record<string, string> = {};
    if (startDate) dateFilter.$gte = startDate;
    if (endDate) dateFilter.$lte = endDate;
    query.date = dateFilter;
  }
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
