import { createHash } from "node:crypto";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";
import { CONTROL_DB_NAME } from "../db/mongo.js";

/**
 * Read-only API keys (api/api_keys.py): looked up by sha256 in
 * hcgateway.api_keys, scoped to the owner's data like a session.
 */
describe("API keys", () => {
  let harness: TestHarness;
  let owner: string;
  let other: string;
  const QUERY = "{ viewer { config { homeTimeZone } } }";

  beforeAll(async () => {
    harness = await createTestHarness();
    ({ userId: owner } = await harness.createUser());
    ({ userId: other } = await harness.createUser());
  });

  afterAll(async () => {
    await harness.teardown();
  });

  const keys = () => harness.client.db(CONTROL_DB_NAME).collection<{ _id: string }>("api_keys");

  function keyFor(userId: string, key: string, lastUsedAt: Date | null = null) {
    return keys().insertOne({
      _id: `key-${key}`,
      userId,
      name: "test",
      keyHash: createHash("sha256").update(key).digest("hex"),
      prefix: key.slice(0, 10),
      scope: "read",
      createdAt: new Date(),
      lastUsedAt,
    } as never);
  }

  it("accepts a key and reads as its owner", async () => {
    await keyFor(owner, "hcg_owner-key");
    const result = await harness.executeAsUser("hcg_owner-key", QUERY);
    expect(result.errors).toBeUndefined();
  });

  it("stops working as soon as it's revoked", async () => {
    await keyFor(owner, "hcg_revoked-key");
    await keys().deleteOne({ _id: "key-hcg_revoked-key" });
    const result = await harness.executeAsUser("hcg_revoked-key", QUERY);
    expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("invalid token") });
  });

  it("records when it was last used, at most once a minute", async () => {
    await keyFor(other, "hcg_fresh-key");
    await harness.executeAsUser("hcg_fresh-key", QUERY);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const first = (await keys().findOne({ _id: "key-hcg_fresh-key" })) as { lastUsedAt: Date | null } | null;
    expect(first?.lastUsedAt).toBeInstanceOf(Date);

    await harness.executeAsUser("hcg_fresh-key", QUERY);
    await new Promise((resolve) => setTimeout(resolve, 50));
    const second = (await keys().findOne({ _id: "key-hcg_fresh-key" })) as { lastUsedAt: Date | null } | null;
    expect(second?.lastUsedAt?.getTime()).toBe(first?.lastUsedAt?.getTime());
  });

  it("refuses anything but a query, plainly", async () => {
    for (const operation of ["mutation { anything }", "subscription { anything }"]) {
      const result = await harness.executeAsUser("hcg_owner-key", operation);
      // Apollo reports every validation error as GRAPHQL_VALIDATION_FAILED.
      expect(result.errors?.[0]).toMatchObject({ message: expect.stringContaining("This API is read-only") });
    }
  });
});
