import { Db } from "mongodb";
import { createHash } from "node:crypto";
import { GraphQLContext } from "../context.js";

/**
 * SourceCatalog resolvers - a straight port of
 * api/analytics_engine/service.py's `inventory_for_user` and
 * `device_inventory_for_user` from Python aggregation pipelines to the
 * equivalent MongoDB driver calls. This reproduces the exact same
 * aggregation shape (not a reimplementation of any derived health
 * semantics - purely counts/min/max/group over raw metadata fields that
 * were never encrypted, matching the design doc's note that both fields
 * "scan raw collections live" and phase 1 keeps that behavior).
 */

const SOURCE_LABELS: Record<string, string> = {
  "com.whoop.android": "WHOOP",
  "com.fitbit.FitbitMobile": "Fitbit / Google Health",
  "com.google.android.apps.fitness": "Google Fit",
  android: "Android",
};

const DEVICE_TYPE_LABELS: Record<number, string> = {
  0: "unknown",
  1: "watch",
  2: "phone",
  3: "scale",
  4: "ring",
  5: "head_mounted",
  6: "fitness_band",
  7: "chest_strap",
  8: "smart_display",
  9: "consumer_medical_device",
  10: "glasses",
  11: "hearable",
  12: "fitness_machine",
  13: "fitness_equipment",
  14: "portable_computer",
  15: "meter",
};

const RECORDING_METHOD_LABELS: Record<number, string> = {
  0: "unknown",
  1: "actively_recorded",
  2: "automatically_recorded",
  3: "manual_entry",
};

function sourceLabel(source: string | null | undefined): string {
  if (source && source.startsWith("com.android.healthconnect.phone.")) {
    return "Health Connect phone source";
  }
  return SOURCE_LABELS[source ?? ""] ?? source ?? "unknown";
}

async function nonUnderscoreCollections(db: Db): Promise<string[]> {
  const names = await db.listCollections({}, { nameOnly: true }).toArray();
  return names.map((entry) => entry.name).filter((name) => !name.startsWith("_")).sort();
}

export async function inventoryForUser(db: Db) {
  const result = {
    totalRecords: 0,
    earliest: null as string | null,
    latest: null as string | null,
    signals: [] as Record<string, unknown>[],
    sources: new Map<string, { label: string; records: number }>(),
  };
  for (const name of await nonUnderscoreCollections(db)) {
    const collection = db.collection(name);
    const count = await collection.countDocuments({});
    const bySource = await collection
      .aggregate<{ _id: string | null; count: number }>([{ $group: { _id: "$app", count: { $sum: 1 } } }])
      .toArray();
    const first = await collection.find({}, { projection: { start: 1 } }).sort({ start: 1 }).limit(1).next();
    const last = await collection.find({}, { projection: { start: 1 } }).sort({ start: -1 }).limit(1).next();
    const earliest = (first?.start as string | undefined) ?? null;
    const latest = (last?.start as string | undefined) ?? null;
    result.signals.push({
      collection: name,
      records: count,
      earliest,
      latest,
      bySource: bySource.map((row) => ({ source: row._id ?? "unknown", count: row.count })),
    });
    result.totalRecords += count;
    if (earliest && (!result.earliest || earliest < result.earliest)) result.earliest = earliest;
    if (latest && (!result.latest || latest > result.latest)) result.latest = latest;
    for (const row of bySource) {
      const source = row._id ?? "unknown";
      const entry = result.sources.get(source) ?? { label: sourceLabel(source), records: 0 };
      entry.records += row.count;
      result.sources.set(source, entry);
    }
  }
  return {
    totalRecords: result.totalRecords,
    earliest: result.earliest,
    latest: result.latest,
    signals: result.signals,
    sources: [...result.sources.entries()].map(([source, entry]) => ({ source, ...entry })),
  };
}

interface DeviceIdentity {
  manufacturer: string | null;
  model: string | null;
  type: number | null;
  typeLabel: string;
}

function normalizedDevice(value: unknown): DeviceIdentity | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const deviceType = typeof record.type === "number" ? record.type : null;
  return {
    manufacturer: (record.manufacturer as string) || null,
    model: (record.model as string) || null,
    type: deviceType,
    typeLabel: DEVICE_TYPE_LABELS[deviceType ?? -1] ?? "unknown",
  };
}

function deviceDescription(source: string, device: DeviceIdentity | null, legacy: boolean): string {
  const label = sourceLabel(source);
  if (legacy) return `${label} legacy records (device metadata unavailable)`;
  const manufacturer = device?.manufacturer;
  const model = device?.model;
  const typeLabel = (device?.typeLabel ?? "unknown").replace(/_/g, " ");
  const name = [manufacturer, model].filter(Boolean).join(" ");
  if (name) return `${name} (${typeLabel})`;
  return `${label} ${typeLabel} device (manufacturer/model not supplied)`;
}

export async function deviceInventoryForUser(db: Db) {
  interface Entry {
    id: string;
    description: string;
    sourcePackage: string;
    sourceLabel: string;
    device: DeviceIdentity | null;
    identityQuality: string;
    mayCombinePhysicalDevices: boolean;
    records: number;
    earliest: string | null;
    latest: string | null;
    signals: Map<string, number>;
    recordingMethods: Map<string, { value: number | null; label: string; records: number }>;
    association: { sourcePackage: string; deviceMetadata: DeviceIdentity | null; legacyWithoutDeviceMetadata: boolean };
  }
  const grouped = new Map<string, Entry>();

  for (const signal of await nonUnderscoreCollections(db)) {
    const collection = db.collection(signal);
    const rows = await collection
      .aggregate<{
        _id: { source: string | null; device: unknown; recordingMethod: number | null };
        records: number;
        earliest: string | null;
        latest: string | null;
      }>([
        {
          $group: {
            _id: { source: "$app", device: "$provenance.device", recordingMethod: "$provenance.recordingMethod" },
            records: { $sum: 1 },
            earliest: { $min: "$start" },
            latest: { $max: "$start" },
          },
        },
      ])
      .toArray();

    for (const row of rows) {
      const rawIdentity = row._id ?? { source: null, device: null, recordingMethod: null };
      const source = rawIdentity.source ?? "unknown";
      const device = normalizedDevice(rawIdentity.device);
      const legacy = device === null;
      const identity = { sourcePackage: source, device, legacy };
      const identityJson = JSON.stringify(identity, Object.keys(identity).sort());
      const deviceId = "observed-" + createHash("sha256").update(identityJson).digest("hex").slice(0, 16);
      const identityQuality =
        device && (device.manufacturer || device.model)
          ? "explicit_model"
          : device && device.type !== null && device.type !== 0
            ? "device_type"
            : "source_only";

      let entry = grouped.get(deviceId);
      if (!entry) {
        entry = {
          id: deviceId,
          description: deviceDescription(source, device, legacy),
          sourcePackage: source,
          sourceLabel: sourceLabel(source),
          device,
          identityQuality,
          mayCombinePhysicalDevices: identityQuality !== "explicit_model",
          records: 0,
          earliest: null,
          latest: null,
          signals: new Map(),
          recordingMethods: new Map(),
          association: { sourcePackage: source, deviceMetadata: device, legacyWithoutDeviceMetadata: legacy },
        };
        grouped.set(deviceId, entry);
      }
      const count = row.records;
      entry.records += count;
      entry.signals.set(signal, (entry.signals.get(signal) ?? 0) + count);
      const method = rawIdentity.recordingMethod ?? null;
      const methodKey = method === null ? "unavailable" : String(method);
      const methodEntry = entry.recordingMethods.get(methodKey) ?? {
        value: method,
        label: RECORDING_METHOD_LABELS[method ?? -1] ?? "unavailable",
        records: 0,
      };
      methodEntry.records += count;
      entry.recordingMethods.set(methodKey, methodEntry);
      if (row.earliest && (!entry.earliest || row.earliest < entry.earliest)) entry.earliest = row.earliest;
      if (row.latest && (!entry.latest || row.latest > entry.latest)) entry.latest = row.latest;
    }
  }

  const devices = [...grouped.values()]
    .map((entry) => ({
      id: entry.id,
      description: entry.description,
      sourcePackage: entry.sourcePackage,
      sourceLabel: entry.sourceLabel,
      device: entry.device,
      identityQuality: entry.identityQuality.toUpperCase(),
      mayCombinePhysicalDevices: entry.mayCombinePhysicalDevices,
      records: entry.records,
      earliest: entry.earliest,
      latest: entry.latest,
      signals: [...entry.signals.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([collection, count]) => ({ collection, count })),
      recordingMethods: [...entry.recordingMethods.values()].sort((a, b) => {
        const aMissing = a.value === null ? 1 : 0;
        const bMissing = b.value === null ? 1 : 0;
        if (aMissing !== bMissing) return aMissing - bMissing;
        return (a.value ?? 99) - (b.value ?? 99);
      }),
      association: {
        sourcePackage: Boolean(entry.association.sourcePackage),
        deviceMetadata: Boolean(entry.association.deviceMetadata),
        legacyWithoutDeviceMetadata: entry.association.legacyWithoutDeviceMetadata,
      },
    }))
    .sort((a, b) => b.records - a.records || a.id.localeCompare(b.id));

  return {
    count: devices.length,
    devices,
    limitations: [
      "Device metadata is supplied by the app that writes each Health Connect record.",
      "Source-only identities can combine multiple physical devices, including Fitbit devices used in different date ranges.",
      "Date ranges describe observations and are not used as identity keys.",
    ],
  };
}

export const sourceCatalogResolvers = {
  Viewer: {
    sources: () => ({}),
  },
  SourceCatalog: {
    inventory: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      context.withTimeout(inventoryForUser(context.userDb), "sources.inventory"),
    devices: async (_parent: unknown, _args: unknown, context: GraphQLContext) =>
      context.withTimeout(deviceInventoryForUser(context.userDb), "sources.devices"),
  },
};
