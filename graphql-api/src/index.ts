import "dotenv/config";
import express from "express";
import http from "node:http";
import cors from "cors";
import bodyParser from "body-parser";
import { ApolloServer } from "@apollo/server";
import { expressMiddleware } from "@as-integrations/express5";
import { makeExecutableSchema } from "@graphql-tools/schema";
import { GraphQLError } from "graphql";

import { loadConfig } from "./config.js";
import { typeDefs } from "./schema/typeDefs.js";
import { resolvers } from "./resolvers/index.js";
import { connectMongo, closeMongo } from "./db/mongo.js";
import { buildContext, GraphQLContext } from "./context.js";
import { AuthError } from "./security/auth.js";
import { depthLimitRule, aliasLimitRule, complexityRule } from "./security/validationRules.js";
import { responseSizeCapPlugin } from "./security/responseSizeCap.js";

async function main() {
  const config = loadConfig();
  const mongoClient = await connectMongo(config.mongoUri);

  const schema = makeExecutableSchema({ typeDefs, resolvers });

  const server = new ApolloServer<GraphQLContext>({
    schema,
    introspection: true, // needed for tooling/codegen per the design doc; playground is gated separately below.
    includeStacktraceInErrorResponses: config.nodeEnv !== "production",
    validationRules: [
      depthLimitRule(config.maxQueryDepth),
      aliasLimitRule(config.maxAliasCount),
      complexityRule(config.maxQueryComplexity),
    ],
    plugins: [
      responseSizeCapPlugin(config.responseSizeCapBytes),
      // Disable the default landing page outside development, per the
      // design doc's checklist ("Production: GraphiQL/introspection
      // playground disabled").
      ...(config.playgroundEnabled
        ? []
        : [
            {
              async serverWillStart() {
                return {
                  async renderLandingPage() {
                    return { html: "" };
                  },
                };
              },
            },
          ]),
    ],
    formatError(formattedError, error) {
      // Never log tokens, raw health values, or record IDs. Apollo's
      // default error formatting already omits request context, but we
      // scrub the message defensively for our own thrown errors and avoid
      // logging the error at all here - the process-level logger below
      // only logs a coarse operation name and status code.
      if (error instanceof AuthError) {
        return { message: formattedError.message, extensions: { code: "UNAUTHENTICATED" } };
      }
      return formattedError;
    },
  });

  await server.start();

  const app = express();
  app.disable("x-powered-by");
  app.use(cors());
  app.use(bodyParser.json({ limit: "1mb" }));

  app.get("/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.use(
    "/graphql",
    expressMiddleware(server, {
      context: async ({ req }) => {
        try {
          return await buildContext(mongoClient, config, req.headers.authorization);
        } catch (error) {
          if (error instanceof AuthError) {
            // Re-throw as-is; Apollo Server surfaces context-construction
            // errors as a top-level GraphQL error response before any
            // resolver executes, satisfying "reject before any resolver
            // runs" for missing/invalid/expired tokens.
            throw error;
          }
          throw new GraphQLError("Failed to establish request context", {
            extensions: { code: "INTERNAL_SERVER_ERROR" },
          });
        }
      },
    }),
  );

  const httpServer = http.createServer(app);
  await new Promise<void>((resolve) => httpServer.listen({ port: config.port }, resolve));
  // Deliberately coarse: no tokens, user IDs, or request bodies.
  console.log(`GraphQL API ready at http://0.0.0.0:${config.port}/graphql`);

  const shutdown = async () => {
    await server.stop();
    await closeMongo();
    httpServer.close(() => process.exit(0));
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

main().catch((error) => {
  console.error("GraphQL API failed to start:", error instanceof Error ? error.message : error);
  process.exit(1);
});
