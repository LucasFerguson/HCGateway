import depthLimit from "graphql-depth-limit";
import { GraphQLError, ValidationContext, FieldNode, ASTVisitor, ValidationRule } from "graphql";
import { createComplexityRule, ComplexityEstimatorArgs } from "graphql-query-complexity";

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

export function depthLimitRule(maxDepth: number): ValidationRule {
  return depthLimit(maxDepth) as ValidationRule;
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

export function complexityRule(maxComplexity: number): ValidationRule {
  return createComplexityRule({
    maximumComplexity: maxComplexity,
    estimators: [weightedFieldEstimator()],
    createError: (max, actual) =>
      new GraphQLError(`Query is too complex: ${actual}. Maximum allowed complexity: ${max}.`, {
        extensions: { code: "QUERY_COMPLEXITY_EXCEEDED" },
      }),
  }) as unknown as ValidationRule;
}
