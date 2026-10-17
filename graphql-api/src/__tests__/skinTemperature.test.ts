import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { userDatabaseName } from "../db/mongo.js";
import { createTestHarness, TestHarness } from "./testServer.js";

/**
 * Skin temperature is exposed two ways, both straight reads of what the
 * Python side stored: raw SkinTemperature records under sourceRecords, and
 * the prepared nightly `skinTemperature` daily series (written by
 * pipeline.py / store.py) under analytics, plus the day view's
 * skinTemperatureDeviation slot. Absent values stay null - never 0.
 */
describe("skin temperature", () => {
  let harness: TestHarness;
  let user: { userId: string; token: string };
  const runId = "health-analytics-v8.6:sourcefp:configfp";
  const date = "2026-10-16";

  beforeAll(async () => {
    harness = await createTestHarness();
    user = await harness.createUser();
    const db = harness.client.db(userDatabaseName(user.userId));

    // Same envelope routes.py sync() writes for POST /api/v2/sync/SkinTemperature.
    await db.collection("skinTemperature").insertMany([
      {
        _id: "night-1",
        id: "night-1",
        app: "com.google.android.apps.fitness",
        start: "2026-10-16T05:00:00Z",
        end: "2026-10-16T12:30:00Z",
        startInstant: new Date("2026-10-16T05:00:00Z"),
        endInstant: new Date("2026-10-16T12:30:00Z"),
        storageFormat: "plain-bson-v1",
        provenance: { dataOrigin: "com.google.android.apps.fitness" },
        data: {
          startZoneOffset: { totalSeconds: -18000 },
          endZoneOffset: { totalSeconds: -18000 },
          baseline: { inCelsius: 33.9, inFahrenheit: 93.02 },
          deltas: [
            { time: "2026-10-16T06:00:00Z", delta: { inCelsius: -0.3, inFahrenheit: -0.54 } },
            { time: "2026-10-16T07:00:00Z", delta: { inCelsius: 0.1, inFahrenheit: 0.18 } },
          ],
          measurementLocation: 0,
        },
      },
      {
        _id: "night-2",
        id: "night-2",
        app: "com.google.android.apps.fitness",
        start: "2026-10-17T05:00:00Z",
        end: "2026-10-17T12:00:00Z",
        startInstant: new Date("2026-10-17T05:00:00Z"),
        endInstant: new Date("2026-10-17T12:00:00Z"),
        storageFormat: "plain-bson-v1",
        provenance: { dataOrigin: "com.google.android.apps.fitness" },
        data: {
          baseline: null,
          deltas: [
            { time: "2026-10-17T06:00:00Z", delta: { inCelsius: 0.2 } },
            { time: "2026-10-17T07:00:00Z", delta: {} },
          ],
        },
      },
    ] as never);

    await db.collection("_analytics_current").insertOne({
      _id: "current",
      runId,
      algorithmVersion: "health-analytics-v8.6",
      sourceFingerprint: "sourcefp",
      configurationFingerprint: "configfp",
      completedAt: new Date("2026-10-17T00:00:00Z"),
    } as never);
    await db.collection("_analytics_runs").insertOne({
      _id: runId,
      status: "completed",
      completedAt: new Date("2026-10-17T00:00:00Z"),
      counts: {},
      issueCount: 0,
    } as never);

    const skinDay = {
      date,
      value: -0.1,
      source: "com.google.android.apps.fitness",
      bySource: [{ source: "com.google.android.apps.fitness", value: -0.1, observationCount: 1, coverageMinutes: null }],
      qualityFlags: [],
    };
    const supportingMetrics = (skin: Record<string, unknown>) => ({
      hrv: { status: "missing", value: null, unit: "ms", note: "none", qualityFlags: [] },
      restingHeartRate: { status: "missing", value: null, unit: "bpm", note: "none", qualityFlags: [] },
      respiratoryRate: { status: "missing", value: null, unit: "breaths_per_minute", note: "none", qualityFlags: [] },
      oxygenSaturation: { status: "missing", value: null, unit: "percent", note: "none", qualityFlags: [] },
      skinTemperatureDeviation: skin,
      steps: { status: "missing", value: null, unit: "steps", note: "none", qualityFlags: [] },
      calories: { status: "missing", value: null, unit: "kcal", note: "none", qualityFlags: [] },
      zone3AndAbove: { status: "missing", value: null, unit: "minutes", note: "none", qualityFlags: [] },
    });
    const dayView = (day: string, skin: Record<string, unknown>) => ({
      contractVersion: "health-day-v1",
      date: day,
      timeZone: "America/Chicago",
      dayState: "recorded",
      generatedAt: null,
      headlineScores: {},
      supportingMetrics: supportingMetrics(skin),
      timeline: {},
      notes: [],
    });
    await db.collection("_analytics_daily").insertMany([
      {
        runId,
        date,
        storageFormat: "plain-bson-v1",
        data: {
          date,
          skinTemperature: skinDay,
          dayView: dayView(date, {
            status: "available", value: -0.1, unit: "celsius_delta",
            source: "com.google.android.apps.fitness", qualityFlags: [],
          }),
        },
      },
      {
        runId,
        date: "2026-10-18",
        storageFormat: "plain-bson-v1",
        data: {
          date: "2026-10-18",
          skinTemperature: null,
          dayView: dayView("2026-10-18", {
            status: "missing", value: null, unit: "celsius_delta",
            note: "No skin-temperature reading was recorded for this day.", qualityFlags: [],
          }),
        },
      },
    ] as never);
    await db.collection("_analytics_summaries").insertOne({
      runId,
      kind: "metricOverviews",
      storageFormat: "plain-bson-v1",
      data: {
        skinTemperature: {
          unit: "celsius_delta",
          overview: { latest: skinDay, previous: null, average7Day: -0.1, average30Day: -0.1, changeFromPrevious: null, sampleCount: 1 },
          rolling7Day: [{ date, value: -0.1, sampleCount: 1 }],
          monthly: [{ month: "2026-10", value: -0.1, sampleCount: 1 }],
        },
      },
    } as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  it("reads raw records without inventing baselines or zero deltas", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { sourceRecords {
        all: skinTemperature { id source startAt endAt baselineCelsius measurementLocation deltas { observedAt celsius } }
        ranged: skinTemperature(range: { start: "2026-10-16T12:00:00Z", endExclusive: "2026-10-17T00:00:00Z" }) { id }
      } }
    }`);
    expect(result.errors).toBeUndefined();
    const records = (result.body as any).viewer.sourceRecords;
    expect(records.all).toEqual([
      {
        id: "night-1",
        source: "com.google.android.apps.fitness",
        startAt: "2026-10-16T05:00:00.000Z",
        endAt: "2026-10-16T12:30:00.000Z",
        baselineCelsius: 33.9,
        measurementLocation: 0,
        deltas: [
          { observedAt: "2026-10-16T06:00:00.000Z", celsius: -0.3 },
          { observedAt: "2026-10-16T07:00:00.000Z", celsius: 0.1 },
        ],
      },
      {
        id: "night-2",
        source: "com.google.android.apps.fitness",
        startAt: "2026-10-17T05:00:00.000Z",
        endAt: "2026-10-17T12:00:00.000Z",
        baselineCelsius: null,
        measurementLocation: null,
        deltas: [{ observedAt: "2026-10-17T06:00:00.000Z", celsius: 0.2 }],
      },
    ]);
    // Overlap matching: a range covering only the morning still finds night-1.
    expect(records.ranged).toEqual([{ id: "night-1" }]);
  });

  it("exposes the prepared nightly series as a CELSIUS_DELTA MetricSeries", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics { skinTemperature {
        unit
        daily { id date value source }
        overview { latest { date value } average7Day sampleCount }
        rolling7Day { date value }
        monthly { month value }
      } } }
    }`);
    expect(result.errors).toBeUndefined();
    const series = (result.body as any).viewer.analytics.skinTemperature;
    expect(series.unit).toBe("CELSIUS_DELTA");
    expect(series.daily).toEqual([
      { id: `${user.userId}:${runId}:skinTemperature:${date}`, date, value: -0.1, source: "com.google.android.apps.fitness" },
    ]);
    expect(series.overview).toEqual({ latest: { date, value: -0.1 }, average7Day: -0.1, sampleCount: 1 });
    expect(series.rolling7Day).toEqual([{ date, value: -0.1 }]);
    expect(series.monthly).toEqual([{ month: "2026-10", value: -0.1 }]);
  });

  it("passes the day view's skinTemperatureDeviation through, keeping missing days null", async () => {
    const result = await harness.executeAsUser(user.token, `query {
      viewer { analytics {
        available: day(date: "${date}") { supportingMetrics { skinTemperatureDeviation { status value unit source note } } }
        missing: day(date: "2026-10-18") { supportingMetrics { skinTemperatureDeviation { status value unit note } } }
      } }
    }`);
    expect(result.errors).toBeUndefined();
    const analytics = (result.body as any).viewer.analytics;
    expect(analytics.available.supportingMetrics.skinTemperatureDeviation).toEqual({
      status: "AVAILABLE", value: -0.1, unit: "celsius_delta", source: "com.google.android.apps.fitness", note: null,
    });
    expect(analytics.missing.supportingMetrics.skinTemperatureDeviation).toEqual({
      status: "MISSING", value: null, unit: "celsius_delta", note: "No skin-temperature reading was recorded for this day.",
    });
  });
});
