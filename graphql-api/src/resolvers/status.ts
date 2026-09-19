/**
 * MetricStatus mapping: the backend's lowercase snake_case status strings
 * (verified exhaustively across day_dashboard.py, recovery.py, strain.py -
 * see the comment atop schema/typeDefs.ts) to the GraphQL enum's
 * SCREAMING_CASE values. Never coerces an unrecognized status to a default
 * that could hide a missing value - an unrecognized string is surfaced as
 * MISSING with the original preserved via the field's `note`, which is the
 * safest fallback for a status enum (never silently AVAILABLE).
 */
const STATUS_MAP: Record<string, string> = {
  available: "AVAILABLE",
  partial: "PARTIAL",
  missing: "MISSING",
  insufficient_data: "INSUFFICIENT_DATA",
  blocked: "BLOCKED",
  not_implemented: "NOT_IMPLEMENTED",
  unavailable: "UNAVAILABLE",
  sample_time_only: "SAMPLE_TIME_ONLY",
};

export function toMetricStatus(raw: unknown): string {
  if (typeof raw !== "string") return "MISSING";
  return STATUS_MAP[raw] ?? "MISSING";
}
