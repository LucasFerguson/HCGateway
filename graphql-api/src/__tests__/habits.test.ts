import { CommandStartedEvent } from "mongodb";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { userDatabaseName } from "../db/mongo.js";
import { createTestHarness, TestHarness } from "./testServer.js";

describe("WHOOP habit journal", () => {
  let harness: TestHarness;
  let userA: { userId: string; token: string };
  let userB: { userId: string; token: string };

  beforeAll(async () => {
    harness = await createTestHarness();
    userA = await harness.createUser();
    userB = await harness.createUser();
    const db = harness.client.db(userDatabaseName(userA.userId));
    await db.collection("habitDefinitions").insertMany([
      {
        _id: "whoop:caffeine",
        id: "whoop:caffeine",
        source: "whoop",
        question: "Consumed caffeine?",
        firstSeenDate: "2026-01-02",
        lastSeenDate: "2026-01-03",
        entryCount: 2,
      },
      {
        _id: "whoop:headache",
        id: "whoop:headache",
        source: "whoop",
        question: "Experienced a headache?",
        firstSeenDate: "2026-01-02",
        lastSeenDate: "2026-01-02",
        entryCount: 1,
      },
    ] as never);
    await db.collection("habitEntries").insertMany([
      {
        _id: "whoop:entry-1",
        id: "whoop:entry-1",
        source: "whoop",
        questionId: "whoop:caffeine",
        question: "Consumed caffeine?",
        date: "2026-01-02",
        cycleStartAt: new Date("2026-01-02T05:00:00Z"),
        cycleEndAt: new Date("2026-01-02T13:00:00Z"),
        cycleStartLocal: "2026-01-01T23:00:00",
        cycleEndLocal: "2026-01-02T07:00:00",
        sourceUtcOffsetMinutes: -360,
        answeredYes: true,
        notes: "afternoon coffee",
      },
      {
        _id: "whoop:entry-2",
        id: "whoop:entry-2",
        source: "whoop",
        questionId: "whoop:caffeine",
        question: "Consumed caffeine?",
        date: "2026-01-03",
        cycleStartAt: new Date("2026-01-03T05:30:00Z"),
        cycleEndAt: new Date("2026-01-03T13:30:00Z"),
        cycleStartLocal: "2026-01-02T23:30:00",
        cycleEndLocal: "2026-01-03T07:30:00",
        sourceUtcOffsetMinutes: -360,
        answeredYes: false,
        notes: null,
      },
    ] as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  const query = `query Habits($range: TimeRange) {
    viewer {
      habits(range: $range) {
        id source question firstSeenDate lastSeenDate entryCount
        entries {
          id date cycleStartAt cycleEndAt cycleStartLocal cycleEndLocal
          sourceUtcOffsetMinutes answeredYes notes
        }
      }
    }
  }`;

  it("returns all known questions with only responses in the requested range", async () => {
    const commands: CommandStartedEvent[] = [];
    const listener = (event: CommandStartedEvent) => commands.push(event);
    harness.client.on("commandStarted", listener);
    try {
      const result = await harness.executeAsUser(userA.token, query, {
        range: {
          start: "2026-01-02T00:00:00Z",
          endExclusive: "2026-01-03T00:00:00Z",
        },
      });
      expect(result.errors).toBeUndefined();
      const habits = (result.body as any).viewer.habits;
      expect(habits.map((habit: any) => habit.question)).toEqual([
        "Consumed caffeine?",
        "Experienced a headache?",
      ]);
      expect(habits[0].entries).toEqual([
        {
          id: "whoop:entry-1",
          date: "2026-01-02",
          cycleStartAt: "2026-01-02T05:00:00.000Z",
          cycleEndAt: "2026-01-02T13:00:00.000Z",
          cycleStartLocal: "2026-01-01T23:00:00",
          cycleEndLocal: "2026-01-02T07:00:00",
          sourceUtcOffsetMinutes: -360,
          answeredYes: true,
          notes: "afternoon coffee",
        },
      ]);
      expect(habits[1].entries).toEqual([]);

      const find = commands.find((event) => event.command.find === "habitEntries");
      expect(find?.command.filter.cycleEndAt).toEqual({
        $gte: new Date("2026-01-02T00:00:00Z"),
        $lt: new Date("2026-01-03T00:00:00Z"),
      });
    } finally {
      harness.client.off("commandStarted", listener);
    }
  });

  it("keeps imported habit history isolated by authenticated account", async () => {
    const result = await harness.executeAsUser(userB.token, query);
    expect(result.errors).toBeUndefined();
    expect((result.body as any).viewer.habits).toEqual([]);
  });

  it("rejects an empty or reversed response range", async () => {
    const result = await harness.executeAsUser(userA.token, query, {
      range: {
        start: "2026-01-03T00:00:00Z",
        endExclusive: "2026-01-03T00:00:00Z",
      },
    });
    expect(result.errors?.[0]).toMatchObject({
      message: "Habit range start must be before endExclusive.",
      extensions: { code: "BAD_USER_INPUT" },
    });
  });
});
