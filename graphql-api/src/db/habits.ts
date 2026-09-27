import { Db, Document } from "mongodb";

const DEFINITIONS = "habitDefinitions";
const ENTRIES = "habitEntries";

export interface HabitDefinitionDocument extends Document {
  _id: string;
  id: string;
  source: string;
  question: string;
  firstSeenDate: string;
  lastSeenDate: string;
  entryCount: number;
}

export interface HabitEntryDocument extends Document {
  _id: string;
  id: string;
  source: string;
  questionId: string;
  question: string;
  date: string;
  cycleStartAt: Date;
  cycleEndAt: Date;
  cycleStartLocal: string;
  cycleEndLocal: string;
  sourceUtcOffsetMinutes: number;
  answeredYes: boolean;
  notes?: string | null;
}

export interface HabitRange {
  start: Date;
  endExclusive: Date;
}

export async function readHabitDefinitions(db: Db): Promise<HabitDefinitionDocument[]> {
  return db
    .collection<HabitDefinitionDocument>(DEFINITIONS)
    .find({}, {
      projection: {
        _id: 1,
        id: 1,
        source: 1,
        question: 1,
        firstSeenDate: 1,
        lastSeenDate: 1,
        entryCount: 1,
      },
    })
    .sort({ question: 1, _id: 1 })
    .toArray();
}

export async function readHabitEntries(db: Db, range?: HabitRange): Promise<HabitEntryDocument[]> {
  const filter: Record<string, unknown> = {};
  if (range) {
    filter.cycleEndAt = { $gte: range.start, $lt: range.endExclusive };
  }
  return db
    .collection<HabitEntryDocument>(ENTRIES)
    .find(filter, {
      projection: {
        _id: 1,
        id: 1,
        source: 1,
        questionId: 1,
        question: 1,
        date: 1,
        cycleStartAt: 1,
        cycleEndAt: 1,
        cycleStartLocal: 1,
        cycleEndLocal: 1,
        sourceUtcOffsetMinutes: 1,
        answeredYes: 1,
        notes: 1,
      },
    })
    .sort({ cycleEndAt: 1, questionId: 1 })
    .toArray();
}
