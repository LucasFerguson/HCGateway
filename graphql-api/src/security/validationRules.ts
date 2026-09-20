import depthLimit from "graphql-depth-limit";
import { GraphQLError, ValidationContext, FieldNode, ASTVisitor, ValidationRule } from "graphql";
import { getComplexity, ComplexityEstimatorArgs } from "graphql-query-complexity";
import type { ApolloServerPlugin } from "@apollo/server";
import type { GraphQLContext } from "../context.js";

/**
 * Query-shape safety limits, per doc/graphql-schema-design.md's checklist:
 * depth limit, alias limit, and a complexity/cost calculator that weights
 * unbounded raw-sample fields more heavily than bounded/day fields. These
 * defend against a pathological *query shape* (e.g. many aliased unbounded
 * `heartRate` selections in one request) - a concern the wall-clock resolver
 * timeout alone does not address, since each aliased selection can complete
 * within the timeout individually while the aggregate response is still huge.
 * All three run as GraphQL validation rules, before any resolver executes.
 */

/**
 * graphql-depth-limit's own errors carry no `extensions.code`, unlike our
 * alias/complexity rules below - wrap it so all three limiters are
 * consistently identifiable in logs and client-side error handling.
 */
export function depthLimitRule(maxDepth: number): ValidationRule {
  return function DepthLimit(context: ValidationContext): ASTVisitor {
    const originalReportError = context.reportError.bind(context);
    context.reportError = (error: GraphQLError) => {
      originalReportError(
        new GraphQLError(error.message, {
          nodes: error.nodes,
          extensions: { code: "QUERY_DEPTH_LIMIT_EXCEEDED" },
        }),
      );
    };
    return (depthLimit(maxDepth) as (ctx: ValidationContext) => ASTVisitor)(context);
  };
}

/** Reject an operation with more than `maxAliases` field aliases anywhere in it. */
export function aliasLimitRule(maxAliases: number): ValidationRule {
  return function AliasLimit(context: ValidationContext): ASTVisitor {
    let count = 0;
    return {
      Field(node: FieldNode) {
        if (node.alias) {
          count += 1;
          if (count > maxAliases) {
            context.reportError(
              new GraphQLError(`Query exceeds the maximum alias count of ${maxAliases}.`, {
                extensions: { code: "ALIAS_LIMIT_EXCEEDED" },
              }),
            );
          }
        }
      },
    };
  };
}

/**
 * Per-field complexity weights, keyed "Type.field". Cheap bounded/derived
 * fields (Day, MetricDay, summaries) fall back to a low default cost of 1;
 * unbounded raw-sample fields (heartRate samples/records, sleepSessions,
 * steps, and other SourceRecords fields that can return a user's entire
 * history with no pagination) are weighted heavily enough that a *handful*
 * of them - not dozens - exceeds the default complexity ceiling
 * (GRAPHQL_MAX_QUERY_COMPLEXITY, default 2,000). graphql-query-complexity's
 * model is additive up the selection tree (parent weight + childComplexity),
 * so these leaf/near-leaf weights are deliberately large in absolute terms,
 * not just large relative to a cheap field's weight of ~1 - a handful of
 * unbounded selections should already approach the ceiling on their own,
 * with the ~25s per-field resolver timeout as the backstop for the
 * legitimate single-field case a static cost estimate cannot size exactly
 * (cost depends on this account's actual row counts, e.g. the 610,787-record
 * primary-account heartRate collection noted in doc/session-handoff.md,
 * which the estimator has no way to know ahead of executing the query).
 */
const FIELD_COMPLEXITY: Record<string, number> = {
  "HeartRateData.samples": 900,
  "HeartRateData.records": 700,
  "HeartRateRecord.samples": 400,
  "SourceRecords.heartRate": 400,
  "SourceRecords.sleepSessions": 150,
  "SourceRecords.steps": 400,
  "SourceRecords.activeCalories": 200,
  "SourceRecords.totalCalories": 200,
  "SourceRecords.restingHeartRate": 80,
  "SourceRecords.heartRateVariability": 80,
  "SourceRecords.respiratoryRate": 80,
  "SourceRecords.oxygenSaturation": 80,
  "SourceRecords.weight": 40,
  "SourceRecords.exerciseSessions": 80,
  "Analytics.days": 30,
  "Analytics.sleepEvents": 20,
  "Analytics.day": 5,
  "SourceCatalog.inventory": 100,
  "SourceCatalog.devices": 100,
};

function weightedFieldEstimator() {
  return (args: ComplexityEstimatorArgs): number => {
    const key = `${args.type.name}.${args.field.name}`;
    const weight = FIELD_COMPLEXITY[key] ?? 1;
    return weight + args.childComplexity;
  };
}

/**
 * Complexity is enforced as a PLUGIN (didResolveOperation), not a
 * ValidationRule, despite graphql-query-complexity shipping a
 * `createComplexityRule` helper that looks like a drop-in ValidationRule.
 *
 * Bug found live 2026-09-20: any operation declaring $variables failed with
 * "Variable ... was not provided", even when the variable was correctly
 * supplied in the request body - reproducible with `curl` directly, so not
 * a client issue. Traced through actual runtime execution (temporarily
 * patching @apollo/server's ESM build to confirm httpRequest.body and
 * request.variables were correct at every step up to and including
 * requestPipeline.js's execute() call) to graphql-query-complexity's
 * QueryComplexity class: it internally calls graphql-js's getVariableValues
 * using `this.options.variables`, DURING validate() - but `validate()`
 * itself has no access to per-request variables (variable coercion is an
 * execution-time concern in graphql-js, not part of the standard validation
 * phase). `createComplexityRule(options)` bakes `options.variables` in once
 * at server-startup time, so every request was validated as if it supplied
 * zero variables, and any operation with a required variable failed
 * getVariableValues's coercion before user code ever ran.
 *
 * The correct integration point (confirmed via graphql-query-complexity's
 * own recommended usage for Apollo Server 4/5, since its ValidationRule
 * helper predates variables-aware plugin hooks): call the library's
 * standalone `getComplexity()` from `didResolveOperation`, which runs after
 * parsing AND variable coercion, with real `requestContext.request.variables`
 * available.
 */
export function complexityPlugin(maxComplexity: number): ApolloServerPlugin<GraphQLContext> {
  return {
    async requestDidStart() {
      return {
        async didResolveOperation(requestContext) {
          const complexity = getComplexity({
            schema: requestContext.schema,
            query: requestContext.document,
            operationName: requestContext.operationName ?? undefined,
            variables: requestContext.request.variables ?? {},
            estimators: [weightedFieldEstimator()],
          });
          if (complexity > maxComplexity) {
            throw new GraphQLError(
              `Query is too complex: ${complexity}. Maximum allowed complexity: ${maxComplexity}.`,
              { extensions: { code: "QUERY_COMPLEXITY_EXCEEDED" } },
            );
          }
        },
      };
    },
  };
}
