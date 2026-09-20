import "dotenv/config";
import express from "express";
import http from "node:http";
import cors from "cors";
import bodyParser from "body-parser";
import { ApolloServer } from "@apollo/server";
import { ApolloServerPluginLandingPageLocalDefault } from "@apollo/server/plugin/landingPage/default";
import { expressMiddleware } from "@as-integrations/express5";
import { makeExecutableSchema } from "@graphql-tools/schema";
import { GraphQLError } from "graphql";
import type { Request } from "express";

import { loadConfig } from "./config.js";
import { typeDefs } from "./schema/typeDefs.js";
import { resolvers } from "./resolvers/index.js";
import { connectMongo, closeMongo } from "./db/mongo.js";
import { buildContext, GraphQLContext } from "./context.js";
import { AuthError } from "./security/auth.js";
import { depthLimitRule, aliasLimitRule, complexityRule } from "./security/validationRules.js";
import { responseSizeCapPlugin } from "./security/responseSizeCap.js";
import { requestLoggingPlugin, logAuthFailure } from "./logging.js";

function isLocalhostRequest(req: Request): boolean {
  const host = (req.headers.host || "").split(":")[0];
  return host === "localhost" || host === "127.0.0.1" || host === "::1";
}

function isBrowserRequest(req: Request): boolean {
  // GraphQL clients (curl, Apollo Client's HttpLink, etc.) don't send
  // text/html in Accept; real browser navigation does.
  const accept = req.headers.accept || "";
  return accept.includes("text/html");
}

function renderRemoteSandboxRedirectPage(req: Request): string {
  const endpoint = `http://${req.headers.host}/graphql`;
  const escapedEndpoint = endpoint.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>HCGateway GraphQL API</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 40rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.5; color: #1a1a1a; }
    code, .endpoint { background: #f0f0f0; padding: 0.15em 0.4em; border-radius: 4px; font-family: ui-monospace, monospace; }
    .endpoint { display: inline-block; margin: 0.5em 0; font-size: 1.05em; user-select: all; }
    ol { padding-left: 1.3em; }
    li { margin-bottom: 0.6em; }
    a.button { display: inline-block; margin-top: 1em; padding: 0.6em 1.2em; background: #311c87; color: white; text-decoration: none; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>HCGateway GraphQL API</h1>
  <p>
    The embedded query explorer only works when this page is opened from
    <code>localhost</code> - browsers block it here because it loads from an
    HTTPS origin and this endpoint is plain HTTP. Use the standalone Sandbox
    instead:
  </p>
  <ol>
    <li>Get a bearer token (see <code>doc/graphql-api.md</code> in the repo).</li>
    <li>Open the standalone Sandbox: <a class="button" href="https://studio.apollographql.com/sandbox/explorer" target="_blank" rel="noopener">studio.apollographql.com/sandbox/explorer</a></li>
    <li>Set the endpoint URL to: <span class="endpoint">${escapedEndpoint}</span></li>
    <li>In the <strong>Headers</strong> panel, add <code>Authorization</code>: <code>Bearer &lt;your token&gt;</code></li>
  </ol>
  <p>Full explanation and a plain <code>curl</code> example: see <code>doc/graphql-api.md</code>.</p>
</body>
</html>`;
}

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
      requestLoggingPlugin(),
      responseSizeCapPlugin(config.responseSizeCapBytes),
      // Apollo Server's own default landing-page plugin picks Sandbox vs.
      // the bare "send a POST request" page based on NODE_ENV, independent
      // of our own config - explicitly select one so GRAPHQL_PLAYGROUND_ENABLED
      // is the single source of truth regardless of NODE_ENV. Per the design
      // doc's checklist ("Production: GraphiQL/introspection playground
      // disabled"), the fully-disabled empty-HTML case stays separate below.
      ...(config.playgroundEnabled
        ? [ApolloServerPluginLandingPageLocalDefault({ embed: true })]
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

  // Apollo's embedded Sandbox (served below for real browser GETs to
  // /graphql) is loaded from an HTTPS iframe (sandbox.embed.apollographql.com)
  // regardless of what host you're viewing it from. Browsers treat
  // localhost/127.0.0.1 as a "potentially trustworthy origin" exempt from
  // mixed-content blocking, so the embedded version works there - but it
  // cannot work from any other host (a LAN IP, a hostname), since the
  // HTTPS-page-to-HTTP-endpoint request gets blocked before it ever reaches
  // this server. Detect that case and serve a small static page pointing at
  // the standalone (non-embedded) Sandbox instead of letting it fail
  // confusingly with "Schema Introspection Failure" - see
  // doc/graphql-api.md's "Using the query explorer" section for the full
  // explanation.
  if (config.playgroundEnabled) {
    app.get("/graphql", (req, res, next) => {
      if (isBrowserRequest(req) && !isLocalhostRequest(req)) {
        res.type("html").send(renderRemoteSandboxRedirectPage(req));
        return;
      }
      next();
    });
  }

  app.use(
    "/graphql",
    expressMiddleware(server, {
      context: async ({ req }) => {
        try {
          return await buildContext(mongoClient, config, req.headers.authorization);
        } catch (error) {
          logAuthFailure(error);
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
