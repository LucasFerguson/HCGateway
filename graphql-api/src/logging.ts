import type { ApolloServerPlugin } from "@apollo/server";
import { GraphQLContext } from "./context.js";
import { AuthError } from "./security/auth.js";

/**
 * Structured per-request logging, one JSON line per operation to stdout
 * (picked up by `docker compose logs` / any downstream log collector).
 *
 * Deliberately never logs: the bearer token, query variables, resolver
 * return values, or any raw health value - matching the same rule the rest
 * of this service follows (see security/auth.ts, index.ts formatError).
 * `userId` is a MongoDB ObjectId string, not a secret - it's already treated
 * as safe to reference (not log) throughout this codebase, e.g. in
 * GraphQLContext.
 */

interface RequestLogLine {
  ts: string;
  event: "graphql_request" | "graphql_auth_failure";
  operationName: string | null;
  durationMs: number | null;
  userId: string | null;
  errorCount: number;
  errorCodes: string[];
  httpStatus: number | null;
}

function emit(line: RequestLogLine): void {
  console.log(JSON.stringify(line));
}

export function requestLoggingPlugin(): ApolloServerPlugin<GraphQLContext> {
  return {
    async requestDidStart() {
      const startedAt = process.hrtime.bigint();

      return {
        async willSendResponse(requestContext) {
          const durationMs = Number(process.hrtime.bigint() - startedAt) / 1_000_000;
          const errors = requestContext.errors ?? [];
          // A request that fails GraphQL Server's context() callback (e.g.
          // auth) never reaches this hook at all - see logAuthFailure below,
          // called directly from the context() catch block in index.ts.
          // contextValue is only meaningful once context() has succeeded.
          const userId =
            "user" in requestContext.contextValue
              ? (requestContext.contextValue as GraphQLContext).user?.userId ?? null
              : null;

          emit({
            ts: new Date().toISOString(),
            event: "graphql_request",
            operationName: requestContext.operationName ?? null,
            durationMs: Math.round(durationMs * 100) / 100,
            userId,
            errorCount: errors.length,
            errorCodes: errors.map(
              (error) => (error.extensions?.code as string | undefined) ?? "UNKNOWN",
            ),
            httpStatus: requestContext.response.http?.status ?? null,
          });
        },
      };
    },
  };
}

/**
 * Logs a request that never reached Apollo's request pipeline because
 * context() rejected it first (missing/invalid/expired token, or an
 * unexpected context-construction failure). Called directly from the
 * `context()` catch block in index.ts, since Apollo's own plugin hooks
 * (requestDidStart/willSendResponse) do not fire for this rejection path.
 * Never logs the token or any request body/variable content.
 */
export function logAuthFailure(error: unknown): void {
  const isAuthError = error instanceof AuthError;
  emit({
    ts: new Date().toISOString(),
    event: "graphql_auth_failure",
    operationName: null,
    durationMs: null,
    userId: null,
    errorCount: 1,
    errorCodes: [isAuthError ? "UNAUTHENTICATED" : "INTERNAL_SERVER_ERROR"],
    httpStatus: isAuthError ? 401 : 500,
  });
}
