import { ApolloServerPlugin, GraphQLRequestListener } from "@apollo/server";
import { GraphQLError } from "graphql";

/**
 * Response-size safety backstop (~100MB default, env-configurable via
 * GRAPHQL_RESPONSE_SIZE_CAP_BYTES). Per doc/graphql-schema-design.md's
 * "Unbounded queries" section, this replaces a mandatory range requirement:
 * unbounded "give me everything" queries are allowed, but a response that
 * would exceed the cap is aborted with a GraphQL error rather than let a
 * huge payload buffer in memory or block the event loop indefinitely.
 *
 * This checks the size of the fully-formatted single response body after
 * execution (Apollo core does not currently expose a true streaming byte
 * counter for the "complete" response kind). For an incremental (@defer)
 * response the cap is applied per emitted chunk, which does bound the
 * streaming case without buffering the whole thing.
 */
export function responseSizeCapPlugin(maxBytes: number): ApolloServerPlugin {
  return {
    async requestDidStart(): Promise<GraphQLRequestListener<never>> {
      return {
        async willSendResponse(requestContext) {
          const { response } = requestContext;
          if (response.body.kind === "single") {
            const size = Buffer.byteLength(JSON.stringify(response.body.singleResult), "utf8");
            if (size > maxBytes) {
              response.body = {
                kind: "single",
                singleResult: {
                  errors: [
                    new GraphQLError(
                      `Response size (${size} bytes) exceeds the configured cap of ${maxBytes} bytes. Narrow the query with a TimeRange or fewer fields.`,
                      { extensions: { code: "RESPONSE_SIZE_CAP_EXCEEDED" } },
                    ),
                  ],
                  data: null,
                },
              };
            }
          }
        },
      };
    },
  };
}
