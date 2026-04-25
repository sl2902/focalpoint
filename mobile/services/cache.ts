/**
 * Expo SQLite offline cache for alerts.
 *
 * Schema: one `alerts` table storing JSON-serialised AlertResponse rows.
 * Max 100 alerts per region — oldest evicted when limit is exceeded.
 * Staleness: data older than 1 hour is considered stale for UI labelling.
 */

import * as SQLite from 'expo-sqlite';
import type { AlertResponse } from '../types/api';

const DB_NAME = 'focalpoint.db';
const MAX_PER_REGION = 100;
const STALE_THRESHOLD_MS = 60 * 60 * 1000; // 1 hour

let _db: SQLite.SQLiteDatabase | null = null;

async function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (_db) return _db;
  _db = await SQLite.openDatabaseAsync(DB_NAME);
  await _db.execAsync(`
    CREATE TABLE IF NOT EXISTS alerts (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      region     TEXT    NOT NULL,
      data       TEXT    NOT NULL,
      fetched_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts(region);
  `);
  return _db;
}

/**
 * Upsert a fresh alert for a region into the cache.
 * Evicts oldest rows beyond MAX_PER_REGION.
 */
export async function upsertAlert(alert: AlertResponse): Promise<void> {
  const db = await getDb();
  const now = Date.now();
  await db.runAsync(
    'INSERT INTO alerts (region, data, fetched_at) VALUES (?, ?, ?)',
    alert.region,
    JSON.stringify(alert),
    now,
  );

  // Evict oldest beyond cap
  await db.runAsync(
    `DELETE FROM alerts
     WHERE region = ?
       AND id NOT IN (
         SELECT id FROM alerts
         WHERE region = ?
         ORDER BY fetched_at DESC
         LIMIT ?
       )`,
    alert.region,
    alert.region,
    MAX_PER_REGION,
  );
}

/**
 * Get the most recent cached alert for a region.
 * Returns null if nothing is cached.
 */
export async function getAlertByRegion(
  region: string,
): Promise<AlertResponse | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ data: string }>(
    'SELECT data FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
  );
  if (!row) return null;
  return JSON.parse(row.data) as AlertResponse;
}

/**
 * Get up to MAX_PER_REGION cached alerts for a region, newest first.
 */
export async function getAlertsForRegion(
  region: string,
): Promise<AlertResponse[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ data: string }>(
    'SELECT data FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT ?',
    region,
    MAX_PER_REGION,
  );
  return rows.map((r) => JSON.parse(r.data) as AlertResponse);
}

/**
 * Returns Unix timestamp (ms) of the most recently cached alert for a region,
 * or null if no cache entry exists.
 */
export async function getLastFetchedAt(region: string): Promise<number | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ fetched_at: number }>(
    'SELECT fetched_at FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
  );
  return row ? row.fetched_at : null;
}

/**
 * Returns true when the most recent cached data for a region is older than
 * STALE_THRESHOLD_MS (1 hour) or no cache entry exists.
 */
export async function isStale(region: string): Promise<boolean> {
  const ts = await getLastFetchedAt(region);
  if (!ts) return true;
  return Date.now() - ts > STALE_THRESHOLD_MS;
}
