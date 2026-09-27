import { GraphQLError } from "graphql";
import { GraphQLContext } from "../context.js";
import { HabitEntryDocument, HabitRange, readHabitDefinitions, readHabitEntries } from "../db/habits.js";

interface TimeRangeInput {
  start: Date;
  endExclusive: Date;
}

function validateRange(range?: TimeRangeInput): HabitRange | undefined {
  if (!range) return undefined;
  if (range.start.getTime() >= range.endExclusive.getTime()) {
    throw new GraphQLError("Habit range start must be before endExclusive.", {
      extensions: { code: "BAD_USER_INPUT" },
    });
  }
  return range;
}

export const habitResolvers = {
  Viewer: {
    habits: async (_parent: unknown, args: { range?: TimeRangeInput }, context: GraphQLContext) => {
      const range = validateRange(args.range);
      const [definitions, entries] = await Promise.all([
        context.withTimeout(readHabitDefinitions(context.userDb), "viewer.habits.definitions"),
        context.withTimeout(readHabitEntries(context.userDb, range), "viewer.habits.entries"),
      ]);
      const byQuestion = new Map<string, HabitEntryDocument[]>();
      for (const entry of entries) {
        const group = byQuestion.get(entry.questionId);
        if (group) group.push(entry);
        else byQuestion.set(entry.questionId, [entry]);
      }
      return definitions.map((definition) => ({
        ...definition,
        entries: byQuestion.get(definition.id) ?? [],
      }));
    },
  },
  Habit: {
    id: (parent: { id?: string; _id: string }) => parent.id ?? parent._id,
  },
  HabitEntry: {
    id: (parent: { id?: string; _id: string }) => parent.id ?? parent._id,
  },
};
