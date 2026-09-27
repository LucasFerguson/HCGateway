import { CommandStartedEvent } from "mongodb";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { userDatabaseName } from "../db/mongo.js";
import { createTestHarness, TestHarness } from "./testServer.js";

describe("prepared analytics GraphQL contract", () => {
  let harness: TestHarness;
  let user: { userId: string; token: string };
  const runId = "health-analytics-v8.4:sourcefp:configfp";
  const date = "2026-01-05";

  beforeAll(async () => {
    harness = await createTestHarness();
    user = await harness.createUser();
    const db = harness.client.db(userDatabaseName(user.userId));

    await db.collection("_analytics_current").insertOne({
      _id: "current",
      runId,
      algorithmVersion: "health-analytics-v8.4",
      sourceFingerprint: "sourcefp",
      configurationFingerprint: "configfp",
      completedAt: new Date("2026-01-06T00:00:00Z"),
    } as never);
    await db.collection("_analytics_runs").insertOne({
      _id: runId,
      status: "completed",
      completedAt: new Date("2026-01-06T00:00:00Z"),
      counts: {},
      issueCount: 0,
    } as never);

    const workout = {
      id: "workout-1",
      startAt: "2026-01-05T23:30:00Z",
      endAt: "2026-01-06T00:30:00Z",
      score: 4.2,
      loadMinutes: 30,
      zoneMinutes: { belowZone1: 0, zone1: 5, zone2: 10, zone3: 10, zone4: 5, zone5: 0 },
      timeline: [],
      quality: {
        publishable: true,
        coverageRatio: 1,
        observedMinutes: 60,
        spanMinutes: 60,
        maxGapMinutes: 1,
        invalidSampleCount: 0,
        reasons: [],
      },
    };
    const nextDayWorkout = {
      ...workout,
      id: "workout-2",
      startAt: "2026-01-06T00:15:00Z",
      endAt: "2026-01-06T00:45:00Z",
    };
    const idlessWorkout = {
      ...workout,
      id: undefined,
      startAt: "2026-01-05T20:00:00Z",
      endAt: "2026-01-05T20:30:00Z",
      score: 2.1,
    };

    await db.collection("_analytics_daily").insertMany([
      {
        runId,
        date,
        storageFormat: "plain-bson-v1",
        data: {
          date,
          dayView: {
            date,
            contractVersion: "health-day-v1",
            dayState: "recorded",
            timeZone: "UTC",
            timeline: {
              sleepStages: [
                {
                  startAt: "2026-01-04T22:00:00Z",
                  endAt: "2026-01-04T22:15:00Z",
                  kind: "awake",
                },
                {
                  startAt: "2026-01-04T22:15:00Z",
                  endAt: "2026-01-04T23:00:00Z",
                  kind: "light",
                },
              ],
            },
          },
          sleepDebt: {
            date,
            sleepMinutes: 450,
            targetMinutes: 480,
            debtMinutes: 30,
            surplusMinutes: 0,
            category: "low",
          },
          sleepConsistency: {
            date,
            source: "test.source",
            bedtimeAt: "2026-01-04T22:00:00Z",
            wakeAt: "2026-01-05T06:00:00Z",
            bedtimeMinutesLocal: 1320,
            wakeMinutesLocal: 360,
            baselineNightCount: 4,
            score: 90,
            category: "optimal",
            qualityFlags: [],
          },
          healthspan: {
            date,
            factors: [{
              key: "resting_heart_rate",
              label: "Resting heart rate",
              value: 55,
              unit: "bpm",
              referenceValue: 60,
              ageImpactYears: -0.5,
              coverageDays: 12,
            }],
            qualityFlags: [],
          },
          steps: { date, value: 1000, source: "test.source", bySource: [], qualityFlags: [] },
          weight: { date, value: 75, source: "test.source", bySource: [], qualityFlags: [] },
          strain: {
            date,
            score: 4.2,
            loadMinutes: 30,
            zoneMinutes: workout.zoneMinutes,
            timeline: [],
            quality: workout.quality,
          },
          strainWorkouts: [workout, idlessWorkout],
          recovery: {
            date,
            score: 80,
            band: "green",
            status: "available",
            provisional: true,
            components: {},
            quality: {
              publishable: true,
              complete: true,
              estimateBasis: "complete",
              availableWeight: 1,
              baselineWindowDays: 30,
              minimumBaselineDays: 7,
              reasons: [],
              qualityFlags: [],
            },
          },
        },
      },
      {
        runId,
        date: "2026-01-06",
        storageFormat: "plain-bson-v1",
        data: {
          date: "2026-01-06",
          steps: { date: "2026-01-06", value: 2000, source: "test.source", bySource: [], qualityFlags: [] },
          strainWorkouts: [workout, idlessWorkout, nextDayWorkout],
        },
      },
    ] as never);

    await db.collection("_analytics_summaries").insertMany([
      {
        runId,
        kind: "healthspan",
        storageFormat: "plain-bson-v1",
        data: {
          modelVersion: "test",
          status: "ready",
          birthDateConfigured: true,
          methodology: "test",
          calibrationReasons: [],
          paceWindowDays: 180,
        },
      },
      {
        runId,
        kind: "metricOverviews",
        storageFormat: "plain-bson-v1",
        data: {
          steps: {
            unit: "steps",
            overview: {
              latest: { date, value: 1000, source: "test.source", bySource: [], qualityFlags: [] },
              previous: null,
              sampleCount: 1,
            },
            rolling7Day: [{ date, value: 1000, sampleCount: 1 }],
            monthly: [{ month: "2026-01", value: 1000, sampleCount: 1 }],
          },
          weight: {
            unit: "kg",
            overview: { latest: { date, value: 75, source: "test.source", bySource: [], qualityFlags: [] }, sampleCount: 1 },
            rolling7Day: [{ date, value: 75, sampleCount: 1 }],
            monthly: [{ month: "2026-01", value: 75, sampleCount: 1 }],
          },
          heartRateVariability: {
            unit: "ms",
            overview: { sampleCount: 0 },
            rolling7Day: [],
            monthly: [],
          },
        },
      },
      {
        runId,
        kind: "strain",
        storageFormat: "plain-bson-v1",
        data: {
          algorithmVersion: "test",
          status: "available",
          methodology: "test",
          limitations: [],
          provisional: true,
          timeZone: "UTC",
          calibration: { available: true, reasons: [] },
          availability: { available: true, reasons: [] },
        },
      },
    ] as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  it("returns typed enums, non-null writer invariants, and collision-safe run-scoped IDs", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics {
        id runId
        day(date: "${date}") { id date }
        days(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { id date }
        sleepDebt { daily { id date } }
        sleepConsistency { daily { id source bedtimeAt wakeAt bedtimeMinutesLocal wakeMinutesLocal } }
        healthspan {
          status
          trend { id date factors { id key unit referenceValue ageImpactYears coverageDays } }
        }
        steps {
          unit
          daily { id date source }
          overview { latest { id } }
          rolling7Day { id value }
          monthly { id }
        }
        weight { unit daily { id } }
        heartRateVariability { unit }
        strain { daily { id date } }
        recovery { daily { id date quality { estimateBasis } } }
      } }
    }`);

    expect(result.errors).toBeUndefined();
    const analytics = (result.body as any).viewer.analytics;
    const identityPrefix = `${user.userId}:${runId}`;
    expect(analytics.id).toBe(identityPrefix);
    expect(analytics.day.id).toBe(`${identityPrefix}:${date}`);
    expect(analytics.days[0].id).toBe(analytics.day.id);
    expect(analytics.sleepDebt.daily[0].id).toBe(`${identityPrefix}:${date}`);
    expect(analytics.sleepConsistency.daily[0]).toMatchObject({
      id: `${identityPrefix}:${date}`,
      source: "test.source",
      bedtimeMinutesLocal: 1320,
      wakeMinutesLocal: 360,
    });
    expect(analytics.healthspan.status).toBe("READY");
    expect(analytics.healthspan.trend[0].id).toBe(`${identityPrefix}:${date}`);
    expect(analytics.healthspan.trend[0].factors[0]).toMatchObject({
      id: `${identityPrefix}:${date}:resting_heart_rate`,
      key: "RESTING_HEART_RATE",
      unit: "BPM",
      referenceValue: 60,
      ageImpactYears: -0.5,
      coverageDays: 12,
    });
    expect(analytics.steps.unit).toBe("STEPS");
    expect(analytics.weight.unit).toBe("KG");
    expect(analytics.heartRateVariability.unit).toBe("MS");
    expect(analytics.steps.daily[0].id).toBe(`${identityPrefix}:steps:${date}`);
    expect(analytics.steps.overview.latest.id).toBe(analytics.steps.daily[0].id);
    expect(analytics.weight.daily[0].id).toBe(`${identityPrefix}:weight:${date}`);
    expect(analytics.weight.daily[0].id).not.toBe(analytics.steps.daily[0].id);
    expect(analytics.steps.rolling7Day[0].id).toBe(`${identityPrefix}:steps:${date}`);
    expect(analytics.steps.monthly[0].id).toBe(`${identityPrefix}:steps:2026-01`);
    expect(analytics.strain.daily[0].id).toBe(`${identityPrefix}:${date}`);
    expect(analytics.recovery.daily[0].id).toBe(`${identityPrefix}:${date}`);
    expect(analytics.recovery.daily[0].quality.estimateBasis).toBe("COMPLETE");
  });

  it("serializes prepared day sleep-stage kinds as GraphQL enums", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics {
        days(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) {
          timeline { sleepStages { startAt endAt kind } }
        }
      } }
    }`);

    expect(result.errors).toBeUndefined();
    expect((result.body as any).viewer.analytics.days[0].timeline.sleepStages).toEqual([
      {
        startAt: "2026-01-04T22:00:00.000Z",
        endAt: "2026-01-04T22:15:00.000Z",
        kind: "AWAKE",
      },
      {
        startAt: "2026-01-04T22:15:00.000Z",
        endAt: "2026-01-04T23:00:00.000Z",
        kind: "LIGHT",
      },
    ]);
  });

  it("namespaces identities by account and preserves canonical units when summaries are absent", async () => {
    const other = await harness.createUser();
    const db = harness.client.db(userDatabaseName(other.userId));
    await db.collection("_analytics_current").insertOne({
      _id: "current",
      runId,
      algorithmVersion: "health-analytics-v8.4",
      sourceFingerprint: "sourcefp",
      configurationFingerprint: "configfp",
      completedAt: new Date("2026-01-06T00:00:00Z"),
    } as never);
    await db.collection("_analytics_runs").insertOne({
      _id: runId,
      status: "completed",
      completedAt: new Date("2026-01-06T00:00:00Z"),
      counts: {},
      issueCount: 0,
    } as never);
    await db.collection("_analytics_daily").insertOne({
      runId,
      date,
      storageFormat: "plain-bson-v1",
      data: {
        date,
        dayView: { date, contractVersion: "health-day-v1", dayState: "recorded", timeZone: "UTC" },
      },
    } as never);

    const result = await harness.executeAsUser(other.token, `query {
      viewer { analytics {
        id
        day(date: "${date}") { id }
        steps { unit }
        activeCalories { unit }
        totalCalories { unit }
        restingHeartRate { unit }
        heartRateVariability { unit }
        weight { unit }
      } }
    }`);

    expect(result.errors).toBeUndefined();
    const analytics = (result.body as any).viewer.analytics;
    expect(analytics.id).toBe(`${other.userId}:${runId}`);
    expect(analytics.id).not.toBe(`${user.userId}:${runId}`);
    expect(analytics.day.id).toBe(`${other.userId}:${runId}:${date}`);
    expect({
      steps: analytics.steps.unit,
      activeCalories: analytics.activeCalories.unit,
      totalCalories: analytics.totalCalories.unit,
      restingHeartRate: analytics.restingHeartRate.unit,
      heartRateVariability: analytics.heartRateVariability.unit,
      weight: analytics.weight.unit,
    }).toEqual({
      steps: "STEPS",
      activeCalories: "KCAL",
      totalCalories: "KCAL",
      restingHeartRate: "BPM",
      heartRateVariability: "MS",
      weight: "KG",
    });
  });

  it("deduplicates identified and id-less workouts spanning daily documents", async () => {
    const commands: CommandStartedEvent[] = [];
    const listener = (event: CommandStartedEvent) => commands.push(event);
    harness.client.on("commandStarted", listener);
    try {
      const result = await harness.executeAsUser(user.token, `query {
        viewer { analytics { strain {
          workouts(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) {
            id startAt endAt score
          }
        } } }
      }`);
      expect(result.errors).toBeUndefined();
      expect((result.body as any).viewer.analytics.strain.workouts).toEqual([
        {
          id: "workout-1",
          startAt: "2026-01-05T23:30:00.000Z",
          endAt: "2026-01-06T00:30:00.000Z",
          score: 4.2,
        },
        {
          id: null,
          startAt: "2026-01-05T20:00:00.000Z",
          endAt: "2026-01-05T20:30:00.000Z",
          score: 2.1,
        },
      ]);

      const workoutFinds = commands.filter(
        (event) =>
          event.command.find === "_analytics_daily" && event.command.projection?.["data.strainWorkouts"] === 1,
      );
      expect(workoutFinds).toHaveLength(1);
      expect(workoutFinds[0].command.filter.date).toEqual({ $gte: "2026-01-04", $lt: "2026-01-07" });
      expect(workoutFinds[0].command.projection?.["data.dayView"]).toBeUndefined();
    } finally {
      harness.client.off("commandStarted", listener);
    }
  });

  it("finds global latest rows without treating missing projected fields as values", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics {
        sleepDebt { latest { date } }
        sleepConsistency { latest { date } }
        healthspan { latest { date } }
      } }
    }`);

    expect(result.errors).toBeUndefined();
    expect((result.body as any).viewer.analytics).toMatchObject({
      sleepDebt: { latest: { date } },
      sleepConsistency: { latest: { date } },
      healthspan: { latest: { date } },
    });
  });

  it("pins one complete current run across aliased analytics roots", async () => {
    const commands: CommandStartedEvent[] = [];
    const listener = (event: CommandStartedEvent) => commands.push(event);
    harness.client.on("commandStarted", listener);
    try {
      const result = await harness.executeAsUser(user.token, `query {
        viewer {
          first: analytics {
            runId algorithmVersion processedAt
            day(date: "2026-01-04") { generatedAt }
          }
          second: analytics { runId algorithmVersion processedAt }
        }
      }`);

      expect(result.errors).toBeUndefined();
      const expected = {
        runId,
        algorithmVersion: "health-analytics-v8.4",
        processedAt: "2026-01-06T00:00:00.000Z",
      };
      expect((result.body as any).viewer.first).toMatchObject({
        ...expected,
        day: { generatedAt: expected.processedAt },
      });
      expect((result.body as any).viewer.second).toEqual(expected);

      const currentFinds = commands.filter((event) => event.command.find === "_analytics_current");
      expect(currentFinds).toHaveLength(1);
    } finally {
      harness.client.off("commandStarted", listener);
    }
  });

  it("preserves non-midnight date membership and isolates distinct range aliases", async () => {
    const commands: CommandStartedEvent[] = [];
    const listener = (event: CommandStartedEvent) => commands.push(event);
    harness.client.on("commandStarted", listener);
    try {
      const result = await harness.executeAsUser(user.token, `query {
        viewer { analytics { steps {
          first: daily(range: {
            start: "2026-01-04T12:00:00Z"
            endExclusive: "2026-01-05T12:00:00Z"
          }) { date }
          second: daily(range: {
            start: "2026-01-05T12:00:00Z"
            endExclusive: "2026-01-06T12:00:00Z"
          }) { date }
        } } }
      }`);

      expect(result.errors).toBeUndefined();
      expect((result.body as any).viewer.analytics.steps).toEqual({
        first: [{ date: "2026-01-05" }],
        second: [{ date: "2026-01-06" }],
      });

      const seriesFinds = commands.filter(
        (event) => event.command.find === "_analytics_daily" && event.command.projection?.["data.steps"] === 1,
      );
      expect(seriesFinds).toHaveLength(2);
      expect(seriesFinds.map((event) => event.command.filter.date)).toEqual(expect.arrayContaining([
        { $gte: "2026-01-05", $lt: "2026-01-06" },
        { $gte: "2026-01-06", $lt: "2026-01-07" },
      ]));
      expect(seriesFinds.every((event) => event.command.filter.runId === runId)).toBe(true);
    } finally {
      harness.client.off("commandStarted", listener);
    }
  });

  it("coalesces bounded series reads and keeps day views out of their Mongo projection", async () => {
    const commands: CommandStartedEvent[] = [];
    const listener = (event: CommandStartedEvent) => commands.push(event);
    harness.client.on("commandStarted", listener);
    try {
      const result = await harness.executeAsUser(user.token, `query {
        viewer { analytics {
          days(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date }
          sleepDebt { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          sleepConsistency { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          healthspan { trend(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          steps { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          weight { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          strain { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
          recovery { daily(range: { start: "2026-01-05T00:00:00Z", endExclusive: "2026-01-06T00:00:00Z" }) { date } }
        } }
      }`);

      expect(result.errors).toBeUndefined();
      const analytics = (result.body as any).viewer.analytics;
      expect(analytics.days.map((row: any) => row.date)).toEqual([date]);
      for (const rows of [
        analytics.sleepDebt.daily,
        analytics.sleepConsistency.daily,
        analytics.healthspan.trend,
        analytics.steps.daily,
        analytics.weight.daily,
        analytics.strain.daily,
        analytics.recovery.daily,
      ]) {
        expect(rows.map((row: any) => row.date)).toEqual([date]);
      }

      const dailyFinds = commands.filter((event) => event.command.find === "_analytics_daily");
      expect(dailyFinds).toHaveLength(2);
      for (const event of dailyFinds) {
        expect(event.command.filter.runId).toBe(runId);
        expect(event.command.filter.date).toEqual({ $gte: date, $lt: "2026-01-06" });
      }
      const seriesFind = dailyFinds.find((event) => event.command.projection?.["data.steps"] === 1);
      const dayViewFind = dailyFinds.find((event) => event.command.projection?.["data.dayView"] === 1);
      expect(seriesFind).toBeDefined();
      expect(seriesFind?.command.projection?.["data.dayView"]).toBeUndefined();
      expect(dayViewFind).toBeDefined();
      expect(dayViewFind?.command.projection?.["data.steps"]).toBeUndefined();

      const metricOverviewFinds = commands.filter(
        (event) => event.command.find === "_analytics_summaries" && event.command.filter?.kind === "metricOverviews",
      );
      expect(metricOverviewFinds).toHaveLength(1);
      expect(metricOverviewFinds[0].command.filter.runId).toBe(runId);
      expect(
        commands
          .filter((event) => event.command.find === "_analytics_summaries")
          .every((event) => event.command.filter.runId === runId),
      ).toBe(true);
    } finally {
      harness.client.off("commandStarted", listener);
    }
  });
});
