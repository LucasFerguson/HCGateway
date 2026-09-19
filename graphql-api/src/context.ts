import { Db, MongoClient } from "mongodb";
import { GraphQLError } from "graphql";
import { AuthenticatedUser, authenticate } from "./security/auth.js";
import { CONTROL_DB_NAME, userDb } from "./db/mongo.js";
import { AppConfig } from "./config.js";

/**
 * Per-request Apollo Server context. Built once in the `context` function
 * (before any resolver runs), holding:
 *  - the authenticated user's id (never a query argument, per the schema
 *    design's non-negotiable rule)
 *  - the user's own database handle
 *  - the control database handle (hcgateway - users/analytics_jobs/sync_status)
 *  - a resolver-timeout helper, applied per resolved field for the ~25s
 *    safety backstop
 *
 * runId pinning: Viewer's resolver (not this module) reads
 * _analytics_current once and stores it on `analyticsRunId` below, so every
 * Analytics.* resolver in the same operation reads the same immutable run
 * even if the worker completes a new one mid-request.
 */
export interface GraphQLContext {
  user: AuthenticatedUser;
  mongoClient: MongoClient;
  controlDb: Db;
  userDb: Db;
  config: AppConfig;
  /** Set once by Viewer's resolver; undefined until then. */
  analyticsRunId?: string;
  /** Wrap a resolver's async work with the configured wall-clock timeout. */
  withTimeout: <T>(work: Promise<T>, fieldName: string) => Promise<T>;
}

export class ResolverTimeoutError extends GraphQLError {
  constructor(fieldName: string, timeoutMs: number) {
    super(`Field "${fieldName}" did not complete within ${timeoutMs}ms`, {
      extensions: { code: "RESOLVER_TIMEOUT" },
    });
  }
}

function withTimeoutFactory(timeoutMs: number) {
  return function withTimeout<T>(work: Promise<T>, fieldName: string): Promise<T> {
    let timer: ReturnType<typeof setTimeout>;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new ResolverTimeoutError(fieldName, timeoutMs)), timeoutMs);
    });
    return Promise.race([work, timeout]).finally(() => clearTimeout(timer)) as Promise<T>;
  };
}

export async function buildContext(
  mongoClient: MongoClient,
  config: AppConfig,
  authorizationHeader: string | undefined,
): Promise<GraphQLContext> {
  // Authenticate before any resolver executes - this function runs inside
  // Apollo Server's `context` callback, which completes before execution
  // begins for the operation.
  const user = await authenticate(mongoClient, authorizationHeader);
  return {
    user,
    mongoClient,
    controlDb: mongoClient.db(CONTROL_DB_NAME),
    userDb: userDb(mongoClient, user.userId),
    config,
    withTimeout: withTimeoutFactory(config.resolverTimeoutMs),
  };
}
