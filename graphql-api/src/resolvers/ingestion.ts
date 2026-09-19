import { GraphQLContext } from "../context.js";
import { readCurrentMetadata } from "../db/analytics.js";

/**
 * IngestionStatus resolvers, mirroring
 * api/analytics_engine/sync_status.py's `status_response` and
 * api/apiVersions/v2/routes.py's `analyticsStatus`/`job_response` exactly -
 * same 120-second active-window semantics, same field names.
 */

const ACTIVE_WINDOW_SECONDS = 120;

interface SyncStatusDoc {
  lastUploadAt?: Date;
  activeUntil?: Date;
  lastRecordType?: string;
  lastRecordCount?: number;
  totalUploadRequests?: number;
  totalRecordsReceived?: number;
}

function phoneSyncStatus(doc: SyncStatusDoc | null) {
  const now = new Date();
  if (!doc) {
    return {
      observedActive: false,
      state: "NEVER_OBSERVED",
      lastUploadAt: null,
      activeUntil: null,
      secondsSinceLastUpload: null,
      lastRecordType: null,
      lastRecordCount: null,
      totalUploadRequests: 0,
      totalRecordsReceived: 0,
      activityWindowSeconds: ACTIVE_WINDOW_SECONDS,
      note: "No phone upload has been observed since sync-status tracking was enabled.",
    };
  }
  const lastUpload = doc.lastUploadAt ? new Date(doc.lastUploadAt) : null;
  const activeUntil = doc.activeUntil ? new Date(doc.activeUntil) : null;
  const active = Boolean(activeUntil && now <= activeUntil);
  const elapsed = lastUpload ? Math.max(0, Math.floor((now.getTime() - lastUpload.getTime()) / 1000)) : null;
  return {
    observedActive: active,
    state: active ? "RECEIVING" : "IDLE",
    lastUploadAt: lastUpload,
    activeUntil,
    secondsSinceLastUpload: elapsed,
    lastRecordType: doc.lastRecordType ?? null,
    lastRecordCount: doc.lastRecordCount ?? null,
    totalUploadRequests: doc.totalUploadRequests ?? 0,
    totalRecordsReceived: doc.totalRecordsReceived ?? 0,
    activityWindowSeconds: ACTIVE_WINDOW_SECONDS,
    note: "Active means the server received an authenticated phone upload within the rolling activity window; it is not a durable phone task state.",
  };
}

interface AnalyticsJobDoc {
  _id: string;
  status?: string;
  reason?: string;
  requestedRevision?: number;
  requestedAt?: Date;
  startedAt?: Date;
  completedAt?: Date;
  error?: unknown;
  result?: unknown;
}

function jobStatus(job: AnalyticsJobDoc | null) {
  if (!job) return null;
  return {
    userId: job._id,
    status: job.status ?? "unknown",
    reason: job.reason ?? null,
    requestedRevision: job.requestedRevision ?? null,
    requestedAt: job.requestedAt ?? null,
    startedAt: job.startedAt ?? null,
    completedAt: job.completedAt ?? null,
    error: job.error ?? null,
    result: job.result ?? null,
  };
}

export const ingestionResolvers = {
  Viewer: {
    ingestion: () => ({}),
  },
  IngestionStatus: {
    phoneSync: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const doc = await context.withTimeout(
        context.controlDb.collection<SyncStatusDoc>("sync_status").findOne({ _id: context.user.userId } as never),
        "ingestion.phoneSync",
      );
      return phoneSyncStatus(doc);
    },
    analyticsJob: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const job = await context.withTimeout(
        context.controlDb.collection<AnalyticsJobDoc>("analytics_jobs").findOne({ _id: context.user.userId } as never),
        "ingestion.analyticsJob",
      );
      return jobStatus(job);
    },
    current: async (_parent: unknown, _args: unknown, context: GraphQLContext) => {
      const meta = await context.withTimeout(readCurrentMetadata(context.userDb), "ingestion.current");
      if (!meta) return null;
      return {
        runId: meta.runId,
        algorithmVersion: meta.algorithmVersion,
        sourceFingerprint: meta.sourceFingerprint,
        configurationFingerprint: meta.configurationFingerprint,
        completedAt: meta.completedAt,
        counts: meta.counts,
        issueCount: meta.issueCount,
      };
    },
  },
};
