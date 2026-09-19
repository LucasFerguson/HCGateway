import { GraphQLContext } from "../context.js";
import { findUserById } from "../db/mongo.js";

/**
 * AnalyticsConfig resolver, mirroring api/analytics_engine/context.py's
 * `context_for_user`: read the user's stored `analyticsConfig` document,
 * falling back to the same environment-variable defaults Python uses
 * (HEALTH_HOME_TIME_ZONE, SLEEP_TARGET_MINUTES, HEALTH_BIRTH_DATE). This is
 * a read-only mirror of GET /api/v2/analytics/config; PUT stays REST-only,
 * per the design doc - no mutation type exists in this schema.
 */

interface StoredAnalyticsConfig {
  homeTimeZone?: string;
  sleepTargetMinutes?: number | string;
  birthDate?: string | null;
  heartRateZoneThresholds?: number[] | null;
  heartRateZoneTestDate?: string | null;
}

export const analyticsConfigResolvers = {
  Viewer: {
    config: () => ({}),
  },
  AnalyticsConfig: {
    homeTimeZone: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const configured = await configFor(context);
      return configured.homeTimeZone ?? process.env.HEALTH_HOME_TIME_ZONE ?? "UTC";
    },
    sleepTargetMinutes: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const configured = await configFor(context);
      const raw = configured.sleepTargetMinutes ?? process.env.SLEEP_TARGET_MINUTES ?? 480;
      return typeof raw === "string" ? Number.parseInt(raw, 10) : raw;
    },
    birthDate: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const configured = await configFor(context);
      return configured.birthDate ?? process.env.HEALTH_BIRTH_DATE ?? null;
    },
    heartRateZoneThresholds: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const configured = await configFor(context);
      return configured.heartRateZoneThresholds ?? null;
    },
    heartRateZoneTestDate: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const configured = await configFor(context);
      return configured.heartRateZoneTestDate ?? null;
    },
  },
};

async function configFor(context: GraphQLContext): Promise<StoredAnalyticsConfig> {
  const user = await context.withTimeout(findUserById(context.controlDb, context.user.userId), "config");
  return (user?.analyticsConfig as StoredAnalyticsConfig) ?? {};
}
