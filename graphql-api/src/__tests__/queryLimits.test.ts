import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";

/**
 * Query depth limit, alias limit, and complexity/cost calculator - all
 * three are GraphQL validation rules that must reject a pathological query
 * before any resolver executes (doc/graphql-schema-design.md's checklist).
 * Uses a small max* configuration so the tests don't need to construct an
 * enormous query to prove the limiter engages.
 */
describe("query-shape safety limits", () => {
  let harness: TestHarness;
  let user: { userId: string; token: string };

  beforeAll(async () => {
    harness = await createTestHarness({ maxQueryDepth: 4, maxAliasCount: 5, maxQueryComplexity: 20 });
    user = await harness.createUser();
  });

  afterAll(async () => {
    await harness.teardown();
  });

  it("rejects a query deeper than the configured depth limit", async () => {
    // viewer(1) > sourceRecords(2) > heartRate(3) > records(4) > samples(5) > bpm(6)
    // exceeds maxQueryDepth: 4.
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          sourceRecords {
            heartRate {
              records {
                samples { bpm }
              }
            }
          }
        }
      }`,
    );
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("depth") });
  });

  it("rejects a query with more aliases than the configured alias limit", async () => {
    const aliases = Array.from({ length: 8 }, (_, i) => `h${i}: sleepTargetMinutes`).join(" ");
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          config { ${aliases} }
        }
      }`,
    );
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("alias count") });
  });

  it("rejects a query whose computed complexity exceeds the configured ceiling", async () => {
    // SourceRecords.heartRate is weighted 400 in the production weight
    // table (src/security/validationRules.ts); with maxQueryComplexity: 20
    // for this test, a single unbounded heartRate selection is already
    // far over budget.
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          sourceRecords {
            heartRate { sampleCount }
          }
        }
      }`,
    );
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("too complex") });
  });

  it("allows a small, shallow, unaliased query under all three limits", async () => {
    const result = await harness.executeAsUser(
      user.token,
      `query {
        viewer {
          config { homeTimeZone }
        }
      }`,
    );
    expect(result.errors).toBeUndefined();
    expect((result.body as any).viewer.config.homeTimeZone).toBe("UTC");
  });
});
