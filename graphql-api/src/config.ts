/**
 * Environment configuration, loaded once at process start.
 *
 * Mirrors the pattern of api/.env: a single MONGO_URI pointed at the same
 * MongoDB instance/credentials the Flask API and Python workers use. This
 * service never writes to MongoDB and never shares a process with Flask.
 */

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function intEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Environment variable ${name} must be a positive integer`);
  }
  return parsed;
}

export interface AppConfig {
  mongoUri: string;
  port: number;
  /** Per-resolver-field wall-clock timeout, milliseconds. */
  resolverTimeoutMs: number;
  /** Serialized response-size cap, bytes. */
  responseSizeCapBytes: number;
  /** Maximum allowed query depth. */
  maxQueryDepth: number;
  /** Maximum allowed alias count per operation. */
  maxAliasCount: number;
  /** Maximum allowed computed complexity cost per operation. */
  maxQueryComplexity: number;
  /** Whether introspection/GraphiQL landing page should be enabled. */
  playgroundEnabled: boolean;
  nodeEnv: string;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const nodeEnv = env.NODE_ENV || "development";
  return {
    mongoUri: env.MONGO_URI || requireEnv("MONGO_URI"),
    port: intEnv("GRAPHQL_PORT", 6645),
    resolverTimeoutMs: intEnv("GRAPHQL_RESOLVER_TIMEOUT_MS", 25_000),
    responseSizeCapBytes: intEnv("GRAPHQL_RESPONSE_SIZE_CAP_BYTES", 100 * 1024 * 1024),
    maxQueryDepth: intEnv("GRAPHQL_MAX_QUERY_DEPTH", 12),
    maxAliasCount: intEnv("GRAPHQL_MAX_ALIAS_COUNT", 30),
    maxQueryComplexity: intEnv("GRAPHQL_MAX_QUERY_COMPLEXITY", 2_000),
    playgroundEnabled: (env.GRAPHQL_PLAYGROUND_ENABLED ?? (nodeEnv !== "production" ? "true" : "false")) === "true",
    nodeEnv,
  };
}
