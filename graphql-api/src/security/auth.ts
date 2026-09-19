import { MongoClient } from "mongodb";
import { GraphQLError } from "graphql";
import { CONTROL_DB_NAME } from "../db/mongo.js";

/**
 * Bearer-token validation, mirrored from api/apiVersions/v2/routes.py's
 * `before_request` hook:
 *
 *   user = usrStore.find_one({'token': token})
 *   if not user: 401
 *   if datetime.datetime.now() > user['expiry']: 401
 *   g.user = user['_id']
 *
 * This is the one deliberately duplicated piece of Python logic (per
 * doc/graphql-schema-design.md) - a single small collection lookup, kept
 * intentionally in sync with the Flask hook rather than shared as a library.
 *
 * The Flask container's `datetime.datetime.now()` is naive and, because the
 * container runs in UTC, is equivalent to UTC "now". Node's `Date` read back
 * from the driver for a naive-UTC BSON datetime is a normal UTC-based Date,
 * so `new Date() > expiry` reproduces the same comparison exactly as long as
 * this process also runs in UTC (it does, as a sibling container in the same
 * Compose stack).
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
 * Validate the request's bearer token against hcgateway.users. Throws
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

  const usersCollection = mongoClient.db(CONTROL_DB_NAME).collection("users");
  const user = await usersCollection.findOne<{ _id: string; expiry?: Date }>({ token });

  if (!user) {
    throw new AuthError("invalid token");
  }

  if (!user.expiry || new Date() > new Date(user.expiry)) {
    throw new AuthError("token expired. Use /api/v2/login to reauthenticate.");
  }

  return { userId: String(user._id) };
}
