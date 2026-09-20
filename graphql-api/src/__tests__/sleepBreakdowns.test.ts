import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";
import { userDatabaseName } from "../db/mongo.js";

/**
 * SleepDebtSummary.breakdown30Day and SleepConsistencySummary.breakdown30Day
 * used to be typed as the opaque JSON scalar even though their Python
 * source (api/analytics_engine/pipeline.py's calculate_sleep_debt /
 * calculate_sleep_consistency) always emits a fixed set of keys - a count
 * of qualifying days plus one count per fixed category
 * (categorize_debt: none/low/moderate/high;
 * categorize_consistency: optimal/sufficient/poor).
 *
 * These tests seed a completed analytics run with a stored
 * _analytics_summaries document for each kind (exactly where
 * api/analytics_engine/store.py's save_analytics writes breakdown30Day,
 * under summaries["sleepDebt"] / summaries["sleepConsistency"]) and prove
 * the field is now a properly-shaped, field-selectable GraphQL object with
 * the exact counts stored - not an opaque blob.
 */
describe("sleep breakdown30Day typing", () => {
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

    await db.collection("_analytics_summaries").insertMany([
      {
        runId,
        kind: "sleepDebt",
        storageFormat: "plain-bson-v1",
        data: {
          targetMinutes: 480,
          methodology: "test methodology",
          average7DayMinutes: 12.5,
          average30DayMinutes: 15,
          previous30DayAverageMinutes: 20,
          breakdown30Day: { recordedDays: 28, none: 10, low: 8, moderate: 6, high: 4 },
        },
      },
      {
        runId,
        kind: "sleepConsistency",
        storageFormat: "plain-bson-v1",
        data: {
          baselineWindowDays: 14,
          minimumBaselineNights: 3,
          methodology: "test methodology",
          average7DayScore: 82,
          average30DayScore: 78,
          previous30DayAverageScore: 75,
          breakdown30Day: { scoredDays: 25, optimal: 12, sufficient: 9, poor: 4 },
        },
      },
    ] as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  it("resolves SleepDebtSummary.breakdown30Day as a typed, field-selectable object with the stored counts", async () => {
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          analytics {
            sleepDebt {
              breakdown30Day {
                recordedDays
                none
                low
                moderate
                high
              }
            }
          }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    const breakdown = (result.body as any).viewer.analytics.sleepDebt.breakdown30Day;
    expect(breakdown).toEqual({ recordedDays: 28, none: 10, low: 8, moderate: 6, high: 4 });
  });

  it("resolves SleepConsistencySummary.breakdown30Day as a typed, field-selectable object with the stored counts", async () => {
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          analytics {
            sleepConsistency {
              breakdown30Day {
                scoredDays
                optimal
                sufficient
                poor
              }
            }
          }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    const breakdown = (result.body as any).viewer.analytics.sleepConsistency.breakdown30Day;
    expect(breakdown).toEqual({ scoredDays: 25, optimal: 12, sufficient: 9, poor: 4 });
  });

  it("returns null (never a partial object or error) when the run is completed but no summary document was stored yet for that kind", async () => {
    // A completed run with no _analytics_summaries row at all is the real
    // shape readSummary() sees mid-rollout / before the summaries writer
    // runs for this kind - it returns null, and the resolver passes that
    // straight through as breakdown30Day: null, never a zero-filled stand-in.
    const freshUser = await harness.createUser();
    const freshDb = harness.client.db(userDatabaseName(freshUser.userId));
    const freshRunId = "test-algo-v2:sourcefp2:configfp2";
    await freshDb.collection("_analytics_current").insertOne({
      _id: "current",
      runId: freshRunId,
      algorithmVersion: "test-algo-v2",
      sourceFingerprint: "sourcefp2",
      configurationFingerprint: "configfp2",
      completedAt: new Date("2026-01-01T00:00:00Z"),
    } as never);
    await freshDb.collection("_analytics_runs").insertOne({
      _id: freshRunId,
      status: "completed",
      completedAt: new Date("2026-01-01T00:00:00Z"),
      counts: {},
      issueCount: 0,
    } as never);

    const result = await harness.executeAsUser(
      freshUser.token,
      `query {
        viewer {
          analytics {
            sleepDebt { breakdown30Day { recordedDays none low moderate high } }
            sleepConsistency { breakdown30Day { scoredDays optimal sufficient poor } }
          }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    const analytics = (result.body as any).viewer.analytics;
    expect(analytics.sleepDebt.breakdown30Day).toBeNull();
    expect(analytics.sleepConsistency.breakdown30Day).toBeNull();
  });
});
