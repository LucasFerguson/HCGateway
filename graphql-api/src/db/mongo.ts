import { Db, Document, MongoClient } from "mongodb";

/**
 * Database naming convention, mirrored exactly from the Python services
 * (api/apiVersions/v2/routes.py's `current_user_and_db`, analytics_engine/service.py's
 * `process_user`): the control database is always named "hcgateway"; each
 * user's data lives in "hcgateway_<userId>". Do not reimplement this
 * differently anywhere else in this service.
 */
export const CONTROL_DB_NAME = "hcgateway";

export function userDatabaseName(userId: string): string {
  return `hcgateway_${userId}`;
}

let client: MongoClient | null = null;

export async function connectMongo(mongoUri: string): Promise<MongoClient> {
  if (client) return client;
  client = new MongoClient(mongoUri, {
    // Keep a bounded pool; this is a read-only service with no long-lived
    // transactions, so the driver defaults are otherwise fine.
    maxPoolSize: 20,
  });
  await client.connect();
  return client;
}

export async function closeMongo(): Promise<void> {
  if (client) {
    await client.close();
    client = null;
  }
}

export function controlDb(mongoClient: MongoClient): Db {
  return mongoClient.db(CONTROL_DB_NAME);
}

export function userDb(mongoClient: MongoClient, userId: string): Db {
  return mongoClient.db(userDatabaseName(userId));
}

/**
 * hcgateway.users documents use a string `_id` (the same convention as
 * every other control-database document in this service), not the driver's
 * default ObjectId - mirrors how Flask/PyMongo stores and looks up users.
 */
export interface UserDoc extends Document {
  _id: string;
  username?: string;
  analyticsConfig?: Record<string, unknown>;
}

export async function findUserById(db: Db, userId: string): Promise<UserDoc | null> {
  return db.collection<UserDoc>("users").findOne({ _id: userId } as Partial<UserDoc>);
}
