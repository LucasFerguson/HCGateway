import { createHash } from "node:crypto";
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { createTestHarness, TestHarness } from "./testServer.js";
import { CONTROL_DB_NAME } from "../db/mongo.js";

/**
 * Per-device sessions (api/auth_sessions.py): the token is looked up by its
 * sha256 in hcgateway.sessions, independent of the legacy token on the user.
 */
describe("session tokens", () => {
  let harness: TestHarness;
  let userId: string;
  const QUERY = "{ viewer { config { homeTimeZone } } }";

  beforeAll(async () => {
    harness = await createTestHarness();
    ({ userId } = await harness.createUser({ expired: true }));
  });

  afterAll(async () => {
    await harness.teardown();
  });

  function sessionFor(token: string) {
    return harness.client.db(CONTROL_DB_NAME).collection("sessions").insertOne({
      userId,
      tokenHash: createHash("sha256").update(token).digest("hex"),
      refreshHash: createHash("sha256").update(`refresh-${token}`).digest("hex"),
      createdAt: new Date(),
    });
  }

  it("accepts a session token even when the legacy user token has expired", async () => {
    await sessionFor("phone-session");
    const result = await harness.executeAsUser("phone-session", QUERY);
    expect(result.errors).toBeUndefined();
  });

  it("keeps each session independent", async () => {
    await sessionFor("browser-session");
    await harness.client.db(CONTROL_DB_NAME).collection("sessions").deleteOne({
      tokenHash: createHash("sha256").update("browser-session").digest("hex"),
    });
    const revoked = await harness.executeAsUser("browser-session", QUERY);
    expect(revoked.errors?.[0]).toMatchObject({ message: expect.stringContaining("invalid token") });
    const other = await harness.executeAsUser("phone-session", QUERY);
    expect(other.errors).toBeUndefined();
  });
});
