import { GraphQLContext } from "../context.js";
import {
  readActiveCalories,
  readExerciseSessions,
  readHeartRateRecords,
  readHeartRateVariability,
  readOxygenSaturation,
  readRespiratoryRate,
  readRestingHeartRate,
  readSleepSessions,
  readSteps,
  readTotalCalories,
  readWeight,
  TimeRangeArg,
} from "../db/rawRecords.js";
import { toMetricStatus } from "./status.js";

interface TimeRangeInput {
  start: Date;
  endExclusive: Date;
}

function toRange(range?: TimeRangeInput | null): TimeRangeArg | undefined {
  if (!range) return undefined;
  return { start: range.start, endExclusive: range.endExclusive };
}

/**
 * SourceRecords resolvers - fixed, typed, single-signal reads over raw
 * Health Connect collections. No field accepts a caller-supplied Mongo
 * filter; `range` is the only argument, and it is always optional (see
 * doc/graphql-schema-design.md's "Unbounded queries" section - omitting it
 * intentionally returns the signal's entire history, backstopped by the
 * resolver timeout + response-size cap rather than a mandatory bound).
 */
export const sourceRecordsResolvers = {
  SourceRecords: {
    heartRate: async (
      _parent: unknown,
      args: { range?: TimeRangeInput },
      context: GraphQLContext,
    ) => {
      // HeartRateData itself carries status/source/sampleCount computed from
      // the full (or ranged) record set; `records`/`samples` sub-fields
      // re-query lazily so a query that only wants sampleCount never
      // flattens samples.
      const range = toRange(args.range);
      const records = await context.withTimeout(readHeartRateRecords(context.userDb, range), "heartRate");
      const sampleCount = records.reduce((total, record) => total + record.samples.length, 0);
      const bySource = new Map<string, number>();
      for (const record of records) {
        bySource.set(record.source, (bySource.get(record.source) ?? 0) + record.samples.length);
      }
      let selectedSource: string | null = null;
      let max = -1;
      for (const [source, count] of bySource) {
        if (count > max) {
          max = count;
          selectedSource = source;
        }
      }
      return {
        status: toMetricStatus(sampleCount > 0 ? "available" : "missing"),
        source: selectedSource,
        sampleCount,
        __range: range,
      };
    },
    sleepSessions: async (
      _parent: unknown,
      args: { range?: TimeRangeInput; match?: "OVERLAPS" | "STARTS_WITHIN" },
      context: GraphQLContext,
    ) => {
      const match = args.match === "STARTS_WITHIN" ? "starts_within" : "overlaps";
      return context.withTimeout(readSleepSessions(context.userDb, toRange(args.range), match), "sleepSessions");
    },
    steps: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readSteps(context.userDb, toRange(args.range)), "steps"),
    activeCalories: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readActiveCalories(context.userDb, toRange(args.range)), "activeCalories"),
    totalCalories: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readTotalCalories(context.userDb, toRange(args.range)), "totalCalories"),
    restingHeartRate: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readRestingHeartRate(context.userDb, toRange(args.range)), "restingHeartRate"),
    heartRateVariability: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readHeartRateVariability(context.userDb, toRange(args.range)), "heartRateVariability"),
    respiratoryRate: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readRespiratoryRate(context.userDb, toRange(args.range)), "respiratoryRate"),
    oxygenSaturation: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readOxygenSaturation(context.userDb, toRange(args.range)), "oxygenSaturation"),
    weight: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readWeight(context.userDb, toRange(args.range)), "weight"),
    exerciseSessions: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) =>
      context.withTimeout(readExerciseSessions(context.userDb, toRange(args.range)), "exerciseSessions"),
  },
  HeartRateData: {
    records: async (
      parent: { __range?: TimeRangeArg },
      args: { range?: TimeRangeInput },
      context: GraphQLContext,
    ) => {
      // `records(range:)` may narrow further than the parent HeartRateData
      // range; if the field omits its own range, fall back to the range the
      // parent field itself was resolved with.
      const range = toRange(args.range) ?? parent.__range;
      return context.withTimeout(readHeartRateRecords(context.userDb, range), "heartRate.records");
    },
    samples: async (
      parent: { __range?: TimeRangeArg },
      args: { range?: TimeRangeInput },
      context: GraphQLContext,
    ) => {
      const range = toRange(args.range) ?? parent.__range;
      const records = await context.withTimeout(
        readHeartRateRecords(context.userDb, range),
        "heartRate.samples",
      );
      return records.flatMap((record) => record.samples);
    },
  },
};
