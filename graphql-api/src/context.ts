import { Db, MongoClient } from "mongodb";
import { GraphQLError } from "graphql";
import { AuthenticatedUser, authenticate } from "./security/auth.js";
import { CONTROL_DB_NAME, userDb } from "./db/mongo.js";
import { AppConfig } from "./config.js";
import { CurrentRunDoc, DailyDocument } from "./db/analytics.js";

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
 * Run pinning: Viewer's resolver stores the in-flight _analytics_current read
 * as `analyticsCurrentRun`, so aliases coalesce and every Analytics field uses
 * the same immutable run metadata even if the worker completes mid-request.
 */
export interface GraphQLContext {
  user: AuthenticatedUser;
  mongoClient: MongoClient;
  controlDb: Db;
  userDb: Db;
  config: AppConfig;
  /** Set once by Viewer's resolver; undefined until then. */
  analyticsRunId?: string;
  /** Set before awaiting so aliased Viewer.analytics fields share one read. */
  analyticsCurrentRun?: Promise<CurrentRunDoc | null>;
  /** Request-local Promise caches coalesce concurrently resolved analytics fields. */
  analyticsReadCache: {
    summaries: Map<string, Promise<Record<string, unknown> | null>>;
    dailySeries: Map<string, Promise<DailyDocument[]>>;
    strainWorkoutDays: Map<string, Promise<DailyDocument[]>>;
  };
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
    analyticsReadCache: {
      summaries: new Map(),
      dailySeries: new Map(),
      strainWorkoutDays: new Map(),
    },
    withTimeout: withTimeoutFactory(config.resolverTimeoutMs),
  };
}
