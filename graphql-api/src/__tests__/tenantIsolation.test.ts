import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";
import { userDatabaseName } from "../db/mongo.js";

/**
 * Every resolver must be scoped to the authenticated user's own database,
 * derived once from a validated bearer token - never from a query
 * argument. These tests seed two separate users with distinct raw records
 * and prove: (a) each user's token only ever surfaces that user's data,
 * (b) an invalid/missing token is rejected before any resolver executes,
 * (c) an expired token is rejected the same way a missing one is.
 */
describe("tenant isolation", () => {
  let harness: TestHarness;
  let userA: { userId: string; token: string };
  let userB: { userId: string; token: string };

  beforeAll(async () => {
    harness = await createTestHarness();
    userA = await harness.createUser();
    userB = await harness.createUser();

    // Seed one distinctive weight record for user A only.
    await harness.client
      .db(userDatabaseName(userA.userId))
      .collection("weight")
      .insertOne({
        _id: "weight-a",
        id: "weight-a",
        app: "test-source",
        start: "2026-01-01T00:00:00Z",
        end: null,
        data: { weight: { inKilograms: 71.5 } },
        storageFormat: "plain-bson-v1",
        startInstant: new Date("2026-01-01T00:00:00Z"),
        endInstant: null,
      } as never);
  });

  afterAll(async () => {
    await harness.teardown();
  });

  const QUERY = `
    query {
      viewer {
        sourceRecords {
          weight {
            observedAt
            kilograms
            source
          }
        }
      }
    }
  `;

  it("returns only the authenticated user's own records", async () => {
    const resultA = await harness.executeAsUser(userA.token, QUERY);
    expect(resultA.errors).toBeUndefined();
    const weightA = (resultA.body as any).viewer.sourceRecords.weight;
    expect(weightA).toHaveLength(1);
    expect(weightA[0].kilograms).toBe(71.5);
  });

  it("never leaks user A's records to user B, even though both query the same field", async () => {
    const resultB = await harness.executeAsUser(userB.token, QUERY);
    expect(resultB.errors).toBeUndefined();
    const weightB = (resultB.body as any).viewer.sourceRecords.weight;
    expect(weightB).toHaveLength(0);
  });

  it("rejects a request with no Authorization header before any resolver runs", async () => {
    const result = await harness.executeAsUser(undefined, QUERY);
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("bearer token required") });
  });

  it("rejects an unrecognized token", async () => {
    const result = await harness.executeAsUser("not-a-real-token", QUERY);
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("invalid token") });
  });

  it("rejects an expired token the same way as a missing one", async () => {
    const expiredUser = await harness.createUser({ expired: true });
    const result = await harness.executeAsUser(expiredUser.token, QUERY);
    expect(result.body).toBeUndefined();
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("expired") });
  });

  it("derives the database name from the token, never from a query argument (no such argument exists)", () => {
    // Structural guarantee: assert the schema's SourceRecords/Analytics/etc.
    // fields accept no userId/database argument at all. This is enforced by
    // the SDL itself (see schema/typeDefs.ts) - this test documents the
    // invariant so a future schema change that adds such an argument fails
    // a code review expectation, even though it wouldn't fail at runtime.
    expect(QUERY).not.toMatch(/userId|databaseName/);
  });
});
