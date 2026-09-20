import { MongoMemoryServer } from "mongodb-memory-server";
import { MongoClient } from "mongodb";
import { ApolloServer } from "@apollo/server";
import { makeExecutableSchema } from "@graphql-tools/schema";
import { typeDefs } from "../schema/typeDefs.js";
import { resolvers } from "../resolvers/index.js";
import { buildContext, GraphQLContext } from "../context.js";
import { AppConfig } from "../config.js";
import { depthLimitRule, aliasLimitRule, complexityPlugin } from "../security/validationRules.js";
import { CONTROL_DB_NAME } from "../db/mongo.js";

/**
 * Shared test harness: an in-memory MongoDB instance seeded the same way
 * the real hcgateway control database is shaped (users collection with a
 * string `_id`, token, expiry), plus a helper to execute a GraphQL
 * operation through the real Apollo Server + resolver stack (not a mock),
 * so these tests exercise the actual auth/context/resolver code paths.
 */

export interface TestHarness {
  mongod: MongoMemoryServer;
  client: MongoClient;
  server: ApolloServer<GraphQLContext>;
  config: AppConfig;
  createUser: (options?: { expired?: boolean }) => Promise<{ userId: string; token: string }>;
  executeAsUser: (
    token: string | undefined,
    query: string,
    variables?: Record<string, unknown>,
  ) => Promise<{ body: unknown; errors?: unknown[] }>;
  teardown: () => Promise<void>;
}

let userCounter = 0;

export async function createTestHarness(configOverrides: Partial<AppConfig> = {}): Promise<TestHarness> {
  const mongod = await MongoMemoryServer.create();
  const client = new MongoClient(mongod.getUri());
  await client.connect();

  const config: AppConfig = {
    mongoUri: mongod.getUri(),
    port: 0,
    resolverTimeoutMs: 5_000,
    responseSizeCapBytes: 100 * 1024 * 1024,
    maxQueryDepth: 12,
    maxAliasCount: 30,
    maxQueryComplexity: 2_000,
    playgroundEnabled: false,
    nodeEnv: "test",
    ...configOverrides,
  };

  const schema = makeExecutableSchema({ typeDefs, resolvers });
  const server = new ApolloServer<GraphQLContext>({
    schema,
    validationRules: [
      depthLimitRule(config.maxQueryDepth),
      aliasLimitRule(config.maxAliasCount),
    ],
    plugins: [complexityPlugin(config.maxQueryComplexity)],
    includeStacktraceInErrorResponses: false,
  });
  await server.start();

  async function createUser(options: { expired?: boolean } = {}): Promise<{ userId: string; token: string }> {
    userCounter += 1;
    const userId = `test-user-${userCounter}`;
    const token = `test-token-${userCounter}-${Math.random().toString(36).slice(2)}`;
    const expiry = options.expired
      ? new Date(Date.now() - 60_000)
      : new Date(Date.now() + 60 * 60 * 1000);
    await client.db(CONTROL_DB_NAME).collection("users").insertOne({
      _id: userId,
      username: userId,
      password: "$argon2id$test-hash",
      token,
      refresh: `refresh-${userId}`,
      expiry,
      analyticsConfig: { homeTimeZone: "UTC", sleepTargetMinutes: 480, birthDate: null },
    } as never);
    return { userId, token };
  }

  async function executeAsUser(token: string | undefined, query: string, variables?: Record<string, unknown>) {
    // Mirrors index.ts exactly: buildContext() is called once per request,
    // before any resolver executes, and a missing/invalid/expired token
    // throws AuthError from inside it - the same rejection path a real
    // request over HTTP hits via Apollo's context function.
    let contextValue: GraphQLContext;
    try {
      contextValue = await buildContext(client, config, token ? `Bearer ${token}` : undefined);
    } catch (error) {
      return { body: undefined, errors: [{ message: (error as Error).message }] };
    }
    const response = await server.executeOperation(
      { query, variables },
      { contextValue },
    );
    if (response.body.kind !== "single") {
      throw new Error("expected a single-result response in tests");
    }
    return { body: response.body.singleResult.data, errors: response.body.singleResult.errors };
  }

  async function teardown() {
    await server.stop();
    await client.close();
    await mongod.stop();
  }

  return { mongod, client, server, config, createUser, executeAsUser, teardown };
}
