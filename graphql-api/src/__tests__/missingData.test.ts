import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";
import { userDatabaseName } from "../db/mongo.js";

/**
 * Hard rule carried over from AGENTS.md and the REST contract: a missing
 * health value must never be coerced to a numeric zero. A
 * MISSING/INSUFFICIENT_DATA/BLOCKED status always pairs with value: null.
 * These tests seed a user with a completed analytics run that has NO data
 * for a given date (a real, common case - e.g. a date before the user's
 * first recorded data) and verify every MetricValue-shaped field reports
 * an explicit status with a null value, never 0.
 */
describe("missing-data representation", () => {
  let harness: TestHarness;
  let user: { userId: string; token: string };

  beforeAll(async () => {
    harness = await createTestHarness();
    user = await harness.createUser();
    const db = harness.client.db(userDatabaseName(user.userId));

    const runId = "test-algo-v1:sourcefp:configfp";
    await db.collection("_analytics_current").insertOne({
      _id: "current",
      runId,
      algorithmVersion: "test-algo-v1",
      sourceFingerprint: "sourcefp",
      configurationFingerprint: "configfp",
      completedAt: new Date("2026-01-01T00:00:00Z"),
    } as never);
    await db.collection("_analytics_runs").insertOne({
      _id: runId,
      status: "completed",
      completedAt: new Date("2026-01-01T00:00:00Z"),
      counts: {},
      issueCount: 0,
    } as never);

    // A date with a stored dayView that has no data at all - the exact
    // shape day_dashboard.py's empty_day() produces for a "recorded" date
    // with zero prepared metrics.
    await db.collection("_analytics_daily").insertOne({
      runId,
      date: "2026-01-05",
      storageFormat: "plain-bson-v1",
      data: {
        date: "2026-01-05",
        dayView: {
          contractVersion: "health-day-v1",
          date: "2026-01-05",
          timeZone: "UTC",
          dayState: "recorded",
          generatedAt: null,
          headlineScores: {
            sleepDuration: { status: "missing", value: null, unit: "minutes", note: "No sleep ending on this date was recorded." },
            sleepNeed: { status: "missing", value: null, unit: "percent", note: "No sleep record is available to compare with the configured target.", qualityFlags: [] },
            recovery: { status: "insufficient_data", value: null, unit: "score_0_100", note: "needs more data", qualityFlags: [] },
            strain: { status: "missing", value: null, unit: "score_0_21", note: "No usable heart-rate strain result is available for this day.", qualityFlags: [] },
            strainTarget: { status: "blocked", value: null, unit: "score_0_21", note: "A strain target depends on a trustworthy Recovery score.", qualityFlags: [] },
          },
          supportingMetrics: {
            hrv: { status: "missing", value: null, unit: "ms", note: "No HRV recorded.", qualityFlags: [] },
            restingHeartRate: { status: "missing", value: null, unit: "bpm", note: "No RHR recorded.", qualityFlags: [] },
            respiratoryRate: { status: "missing", value: null, unit: "breaths_per_minute", note: "none", qualityFlags: [] },
            oxygenSaturation: { status: "missing", value: null, unit: "percent", note: "none", qualityFlags: [] },
            skinTemperatureDeviation: { status: "missing", value: null, unit: "celsius_delta", note: "not present", qualityFlags: [] },
            steps: { status: "missing", value: null, unit: "steps", note: "No steps recorded.", qualityFlags: [] },
            calories: { status: "missing", value: null, unit: "kcal", note: "none", qualityFlags: [] },
            zone3AndAbove: { status: "missing", value: null, unit: "minutes", note: "not calibrated", qualityFlags: [] },
          },
          timeline: {
            heartRate: { status: "missing", source: null, sampleCount: 0, observedMinuteCount: 0, hours: [], note: "No heart-rate samples." },
            strain: [],
            sleepStages: [],
            steps: [],
            workouts: [],
            schedule: { status: "not_implemented", value: null, note: "not connected", qualityFlags: [] },
            targetWakeTime: { status: "missing", value: null, unit: "local_time", note: "none", qualityFlags: [] },
            targetBedTime: { status: "missing", value: null, unit: "local_time", note: "none", qualityFlags: [] },
            now: { status: "missing", latestObservedAt: null, note: "no receipt timestamp" },
          },
          heartRateZones: { status: "missing", value: null, note: "not configured", qualityFlags: [] },
          notes: [{ code: "no_day_data", message: "No prepared metrics exist for this date." }],
        },
      },
    } as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  it("reports MISSING/INSUFFICIENT_DATA/BLOCKED status with a null value, never 0, for every headline and supporting metric", async () => {
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          analytics {
            day(date: "2026-01-05") {
              headlineScores {
                sleepDuration { status value }
                sleepNeed { status value }
                recovery { status value }
                strain { status value }
                strainTarget { status value }
              }
              supportingMetrics {
                hrv { status value }
                restingHeartRate { status value }
                steps { status value }
                calories { status value }
              }
            }
          }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    const day = (result.body as any).viewer.analytics.day;

    for (const metric of Object.values(day.headlineScores) as Array<{ status: string; value: unknown }>) {
      expect(metric.value).toBeNull();
      expect(["MISSING", "INSUFFICIENT_DATA", "BLOCKED"]).toContain(metric.status);
      // The specific hard rule under test: never a numeric zero standing in
      // for "missing".
      expect(metric.value).not.toBe(0);
    }
    for (const metric of Object.values(day.supportingMetrics) as Array<{ status: string; value: unknown }>) {
      expect(metric.value).toBeNull();
      expect(metric.status).toBe("MISSING");
      expect(metric.value).not.toBe(0);
    }
  });

  it("synthesizes a fully-missing Day (never null, never zero-filled) for a date with no stored dayView at all", async () => {
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          analytics {
            day(date: "2099-01-01") {
              dayState
              headlineScores { sleepDuration { status value } }
              supportingMetrics { steps { status value } }
            }
          }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    const day = (result.body as any).viewer.analytics.day;
    expect(day).not.toBeNull();
    expect(day.dayState).toBe("FUTURE");
    expect(day.headlineScores.sleepDuration.value).toBeNull();
    expect(day.supportingMetrics.steps.value).toBeNull();
  });
});
