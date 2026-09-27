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

    await db.collection("_analytics_daily").insertMany([
      {
        runId,
        date,
        storageFormat: "plain-bson-v1",
        data: {
          date,
          dayView: { date, contractVersion: "health-day-v1", dayState: "recorded", timeZone: "UTC" },
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
          strainWorkouts: [workout],
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
        data: { date: "2026-01-06", strainWorkouts: [workout] },
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
        recovery { daily { id date } }
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

  it("deduplicates a persisted workout spanning two daily documents", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics { strain { workouts { id startAt endAt score } } } }
    }`);
    expect(result.errors).toBeUndefined();
    expect((result.body as any).viewer.analytics.strain.workouts).toEqual([
      {
        id: "workout-1",
        startAt: "2026-01-05T23:30:00.000Z",
        endAt: "2026-01-06T00:30:00.000Z",
        score: 4.2,
      },
    ]);
  });
});
