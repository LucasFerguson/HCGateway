import { DateTimeScalar, DateScalar, JSONScalar } from "./scalars.js";
import { viewerResolvers } from "./viewer.js";
import { sourceRecordsResolvers } from "./sourceRecords.js";
import { analyticsResolvers } from "./analytics.js";
import { sourceCatalogResolvers } from "./sourceCatalog.js";
import { ingestionResolvers } from "./ingestion.js";
import { analyticsConfigResolvers } from "./analyticsConfig.js";

function mergeResolvers(...groups: Record<string, unknown>[]): Record<string, Record<string, unknown>> {
  const merged: Record<string, Record<string, unknown>> = {};
  for (const group of groups) {
    for (const [typeName, fields] of Object.entries(group)) {
      merged[typeName] = { ...(merged[typeName] ?? {}), ...(fields as Record<string, unknown>) };
    }
  }
  return merged;
}

export const resolvers = {
  DateTime: DateTimeScalar,
  Date: DateScalar,
  JSON: JSONScalar,
  ...mergeResolvers(
    viewerResolvers,
    sourceRecordsResolvers,
    analyticsResolvers,
    sourceCatalogResolvers,
    ingestionResolvers,
    analyticsConfigResolvers,
  ),
};
