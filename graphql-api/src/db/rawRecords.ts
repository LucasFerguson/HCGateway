import { Db, Document, Filter } from "mongodb";

/**
 * Raw Health Connect record reading. This mirrors the field NAMES and
 * shapes that api/analytics_engine/repository.py's mapper functions produce
 * (_sleep, _steps, _energy, _resting_heart_rate, _weight,
 * _heart_rate_variability, _heart_rate, _respiratory_rate,
 * _oxygen_saturation, _exercise_session) - it is a straight reshape of the
 * on-disk document into the GraphQL type, not a reimplementation of any
 * analytics/normalization logic (no reconciliation, no stage-kind
 * inference beyond the same fixed lookup table Python uses, no bucketing).
 *
 * Verified against real documents in hcgateway_<userId> collections:
 * sleepSession, steps, activeCaloriesBurned, totalCaloriesBurned,
 * restingHeartRate, weight, heartRate, heartRateVariabilityRmssd,
 * respiratoryRate, oxygenSaturation, exerciseSession all store
 * { id, app, start, end, data, storageFormat, startInstant, endInstant }
 * with `data` as a plain JS object post plaintext-BSON-cutover (no
 * decryption step - see doc/session-handoff.md's "Plaintext BSON cutover
 * completed" section).
 */

export interface TimeRangeArg {
  start: Date;
  endExclusive: Date;
}

// Same fixed lookup table as repository.py's STAGE_KINDS.
const STAGE_KINDS: Record<number, string> = {
  1: "AWAKE",
  2: "ASLEEP",
  3: "UNKNOWN",
  4: "LIGHT",
  5: "DEEP",
  6: "REM",
};

interface RawDoc extends Document {
  _id: string;
  id?: string;
  app?: string;
  start?: string;
  end?: string | null;
  data?: Record<string, unknown>;
  startInstant?: Date;
  endInstant?: Date | null;
}

/**
 * Build a Mongo filter for a point-in-time or interval collection using the
 * native startInstant/endInstant BSON datetime fields added by the
 * plaintext-BSON migration (api/migrations/plaintext_bson.py), which let us
 * range-query natively instead of parsing the legacy string `start`/`end`.
 *
 * match: "overlaps" is for interval records that can straddle a range
 * boundary (sleep sessions, exercise sessions) - overlap means
 * start < endExclusive AND end > rangeStart.
 * match: "starts_within" is for everything else - startInstant falls inside
 * [start, endExclusive).
 */
function rangeFilter(range: TimeRangeArg | undefined, match: "overlaps" | "starts_within"): Filter<RawDoc> {
  if (!range) return {};
  if (match === "overlaps") {
    return {
      startInstant: { $lt: range.endExclusive },
      $or: [{ endInstant: { $gt: range.start } }, { endInstant: null, startInstant: { $gte: range.start } }],
    };
  }
  return { startInstant: { $gte: range.start, $lt: range.endExclusive } };
}

async function findAll(db: Db, collection: string, filter: Filter<RawDoc>): Promise<RawDoc[]> {
  if (!(await collectionExists(db, collection))) return [];
  return db.collection<RawDoc>(collection).find(filter).sort({ startInstant: 1 }).toArray();
}

const collectionExistenceCache = new WeakMap<Db, Set<string>>();

async function collectionExists(db: Db, name: string): Promise<boolean> {
  let cached = collectionExistenceCache.get(db);
  if (!cached) {
    const names = await db.listCollections({}, { nameOnly: true }).toArray();
    cached = new Set(names.map((entry) => entry.name));
    collectionExistenceCache.set(db, cached);
  }
  return cached.has(name);
}

function data(doc: RawDoc): Record<string, unknown> {
  return doc.data ?? {};
}

export interface SleepSessionRecord {
  id: string;
  source: string;
  startAt: Date;
  endAt: Date;
  title: string | null;
  notes: string | null;
  stages: { startAt: Date; endAt: Date; kind: string }[];
  sourceZoneOffsets: { start: number | null; end: number | null } | null;
}

export async function readSleepSessions(
  db: Db,
  range: TimeRangeArg | undefined,
  match: "overlaps" | "starts_within",
): Promise<SleepSessionRecord[]> {
  const docs = await findAll(db, "sleepSession", rangeFilter(range, match));
  return docs.map((doc) => {
    const d = data(doc);
    const stages = Array.isArray(d.stages)
      ? (d.stages as Array<Record<string, unknown>>).map((stage) => ({
          startAt: new Date(String(stage.startTime)),
          endAt: new Date(String(stage.endTime)),
          kind: STAGE_KINDS[Number(stage.stage)] ?? "UNKNOWN",
        }))
      : [];
    stages.sort((a, b) => a.startAt.getTime() - b.startAt.getTime());
    const startZoneOffset = d.startZoneOffset as { totalSeconds?: number } | null | undefined;
    const endZoneOffset = d.endZoneOffset as { totalSeconds?: number } | null | undefined;
    return {
      id: String(doc.id ?? doc._id),
      source: String(doc.app),
      startAt: doc.startInstant ?? new Date(String(doc.start)),
      endAt: doc.endInstant ?? new Date(String(doc.end)),
      title: (d.title as string | null) ?? null,
      notes: (d.notes as string | null) ?? null,
      stages,
      sourceZoneOffsets:
        startZoneOffset != null || endZoneOffset != null
          ? { start: startZoneOffset?.totalSeconds ?? null, end: endZoneOffset?.totalSeconds ?? null }
          : null,
    };
  });
}

export interface StepRecord {
  id: string;
  source: string;
  startAt: Date;
  endAt: Date;
  count: number;
}

export async function readSteps(db: Db, range: TimeRangeArg | undefined): Promise<StepRecord[]> {
  const docs = await findAll(db, "steps", rangeFilter(range, "overlaps"));
  return docs.map((doc) => ({
    id: String(doc.id ?? doc._id),
    source: String(doc.app),
    startAt: doc.startInstant ?? new Date(String(doc.start)),
    endAt: doc.endInstant ?? new Date(String(doc.end)),
    count: Number((data(doc).count as number) ?? 0),
  }));
}

export interface EnergyRecord {
  id: string;
  source: string;
  startAt: Date;
  endAt: Date;
  energyKcal: number;
}

async function readEnergy(db: Db, collection: string, range: TimeRangeArg | undefined): Promise<EnergyRecord[]> {
  const docs = await findAll(db, collection, rangeFilter(range, "overlaps"));
  return docs.map((doc) => {
    const energy = (data(doc).energy as { inKilocalories?: number } | undefined) ?? {};
    return {
      id: String(doc.id ?? doc._id),
      source: String(doc.app),
      startAt: doc.startInstant ?? new Date(String(doc.start)),
      endAt: doc.endInstant ?? new Date(String(doc.end)),
      energyKcal: Number(energy.inKilocalories ?? 0),
    };
  });
}

export const readActiveCalories = (db: Db, range: TimeRangeArg | undefined) =>
  readEnergy(db, "activeCaloriesBurned", range);
export const readTotalCalories = (db: Db, range: TimeRangeArg | undefined) =>
  readEnergy(db, "totalCaloriesBurned", range);

export interface PointRecord {
  id: string;
  source: string;
  observedAt: Date;
}

export interface RestingHeartRateRecord extends PointRecord {
  bpm: number;
}

export async function readRestingHeartRate(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<RestingHeartRateRecord[]> {
  const docs = await findAll(db, "restingHeartRate", rangeFilter(range, "starts_within"));
  return docs.map((doc) => ({
    id: String(doc.id ?? doc._id),
    source: String(doc.app),
    observedAt: doc.startInstant ?? new Date(String(doc.start)),
    bpm: Number(data(doc).beatsPerMinute ?? 0),
  }));
}

export interface HeartRateVariabilityRecord extends PointRecord {
  milliseconds: number;
}

export async function readHeartRateVariability(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<HeartRateVariabilityRecord[]> {
  const docs = await findAll(db, "heartRateVariabilityRmssd", rangeFilter(range, "starts_within"));
  return docs.map((doc) => ({
    id: String(doc.id ?? doc._id),
    source: String(doc.app),
    observedAt: doc.startInstant ?? new Date(String(doc.start)),
    milliseconds: Number(data(doc).heartRateVariabilityMillis ?? 0),
  }));
}

export interface RespiratoryRateRecord extends PointRecord {
  breathsPerMinute: number;
}

export async function readRespiratoryRate(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<RespiratoryRateRecord[]> {
  const docs = await findAll(db, "respiratoryRate", rangeFilter(range, "starts_within"));
  return docs.map((doc) => ({
    id: String(doc.id ?? doc._id),
    source: String(doc.app),
    observedAt: doc.startInstant ?? new Date(String(doc.start)),
    breathsPerMinute: Number(data(doc).rate ?? 0),
  }));
}

export interface OxygenSaturationRecord extends PointRecord {
  percentage: number;
}

export async function readOxygenSaturation(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<OxygenSaturationRecord[]> {
  const docs = await findAll(db, "oxygenSaturation", rangeFilter(range, "starts_within"));
  return docs.map((doc) => ({
    id: String(doc.id ?? doc._id),
    source: String(doc.app),
    observedAt: doc.startInstant ?? new Date(String(doc.start)),
    percentage: Number(data(doc).percentage ?? 0),
  }));
}

export interface WeightRecord extends PointRecord {
  kilograms: number;
}

export async function readWeight(db: Db, range: TimeRangeArg | undefined): Promise<WeightRecord[]> {
  const docs = await findAll(db, "weight", rangeFilter(range, "starts_within"));
  return docs.map((doc) => {
    const weight = (data(doc).weight as { inKilograms?: number } | undefined) ?? {};
    return {
      id: String(doc.id ?? doc._id),
      source: String(doc.app),
      observedAt: doc.startInstant ?? new Date(String(doc.start)),
      kilograms: Number(weight.inKilograms ?? 0),
    };
  });
}

export interface ExerciseSessionRecord {
  id: string;
  source: string;
  startAt: Date;
  endAt: Date;
  exerciseType: number;
  title: string | null;
  notes: string | null;
  sourceZoneOffsets: { start: number | null; end: number | null } | null;
}

export async function readExerciseSessions(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<ExerciseSessionRecord[]> {
  const docs = await findAll(db, "exerciseSession", rangeFilter(range, "overlaps"));
  return docs.map((doc) => {
    const d = data(doc);
    const startZoneOffset = d.startZoneOffset as { totalSeconds?: number } | null | undefined;
    const endZoneOffset = d.endZoneOffset as { totalSeconds?: number } | null | undefined;
    return {
      id: String(doc.id ?? doc._id),
      source: String(doc.app),
      startAt: doc.startInstant ?? new Date(String(doc.start)),
      endAt: doc.endInstant ?? new Date(String(doc.end)),
      exerciseType: Number(d.exerciseType ?? 0),
      title: (d.title as string | null) ?? null,
      notes: (d.notes as string | null) ?? null,
      sourceZoneOffsets:
        startZoneOffset != null || endZoneOffset != null
          ? { start: startZoneOffset?.totalSeconds ?? null, end: endZoneOffset?.totalSeconds ?? null }
          : null,
    };
  });
}

export interface HeartRateSample {
  observedAt: Date;
  bpm: number;
}
export interface HeartRateRecord {
  id: string;
  source: string;
  startAt: Date;
  endAt: Date;
  samples: HeartRateSample[];
}

export async function readHeartRateRecords(
  db: Db,
  range: TimeRangeArg | undefined,
): Promise<HeartRateRecord[]> {
  const docs = await findAll(db, "heartRate", rangeFilter(range, "overlaps"));
  return docs.map((doc) => {
    const d = data(doc);
    const samples = Array.isArray(d.samples)
      ? (d.samples as Array<Record<string, unknown>>).map((sample) => ({
          observedAt: new Date(String(sample.time)),
          bpm: Number(sample.beatsPerMinute ?? 0),
        }))
      : [];
    return {
      id: String(doc.id ?? doc._id),
      source: String(doc.app),
      startAt: doc.startInstant ?? new Date(String(doc.start)),
      endAt: doc.endInstant ?? new Date(String(doc.end)),
      samples,
    };
  });
}
