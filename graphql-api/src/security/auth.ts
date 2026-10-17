import { createHash } from "node:crypto";
import { MongoClient } from "mongodb";
import { GraphQLError } from "graphql";
import { CONTROL_DB_NAME } from "../db/mongo.js";

/**
 * Bearer-token validation, mirrored from api/auth_sessions.py's
 * `user_id_for_token` (used by the Flask `before_request` hook):
 *
 *   1. a per-device session whose `tokenHash` is sha256(token), or
 *   2. a legacy single token on the user document that hasn't expired.
 *
 * This is the one deliberately duplicated piece of Python logic (per
 * doc/graphql-schema-design.md) - a small collection lookup, kept
 * intentionally in sync with the Python rather than shared as a library.
 *
 * For the legacy path, the Flask container's `datetime.datetime.now()` is
 * naive and, because the container runs in UTC, equivalent to UTC "now", so
 * `new Date() > expiry` reproduces the same comparison as long as this
 * process also runs in UTC (it does, as a sibling container).
 */

export interface AuthenticatedUser {
  /** The MongoDB `_id` string of the authenticated user - never logged. */
  userId: string;
}

export class AuthError extends GraphQLError {
  constructor(message: string) {
    super(message, { extensions: { code: "UNAUTHENTICATED", http: { status: 401 } } });
  }
}

function extractBearerToken(authorizationHeader: string | undefined): string | null {
  if (!authorizationHeader) return null;
  const [scheme, ...rest] = authorizationHeader.split(" ");
  const token = rest.join(" ").trim();
  if (!scheme || scheme.toLowerCase() !== "bearer" || !token) return null;
  return token;
}

/**
 * Validate the request's bearer token against hcgateway.sessions (then the
 * legacy token on hcgateway.users). Throws
 * AuthError on any failure - missing header, unknown token, or expired
 * token - exactly mirroring the Flask hook's three rejection cases. Never
 * logs the token or any user field.
 */
export async function authenticate(
  mongoClient: MongoClient,
  authorizationHeader: string | undefined,
): Promise<AuthenticatedUser> {
  const token = extractBearerToken(authorizationHeader);
  if (!token) {
    throw new AuthError("valid bearer token required");
  }

  const db = mongoClient.db(CONTROL_DB_NAME);
  const tokenHash = createHash("sha256").update(token, "utf8").digest("hex");
  const session = await db
    .collection("sessions")
    .findOne<{ userId: string }>({ tokenHash }, { projection: { userId: 1 } });
  if (session) {
    return { userId: String(session.userId) };
  }

  const user = await db.collection("users").findOne<{ _id: string; expiry?: Date }>({ token });

  if (!user) {
    throw new AuthError("invalid token");
  }

  if (!user.expiry || new Date() > new Date(user.expiry)) {
    throw new AuthError("token expired. Use /api/v2/login to reauthenticate.");
  }

  return { userId: String(user._id) };
}
