import { Platform } from 'react-native';
import * as SQLite from 'expo-sqlite';
import type { AlertResponse } from '../types/api';

const MAX_PER_REGION = 100;
const STALE_THRESHOLD_MS = 60 * 60 * 1000; // 1 hour

// ── Web: in-memory store ────────────────────────────────────────────────────

type MemRow = { data: AlertResponse; fetchedAt: number };
const mem: Record<string, MemRow[]> = {};

function memUpsert(alert: AlertResponse): void {
  const rows = mem[alert.region] ?? [];
  rows.unshift({ data: alert, fetchedAt: Date.now() });
  mem[alert.region] = rows.slice(0, MAX_PER_REGION);
}

function memNewest(region: string): MemRow | undefined {
  return mem[region]?.[0];
}

// ── Native: SQLite ──────────────────────────────────────────────────────────

const DB_NAME = 'focalpoint.db';
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

// ── Exported API ────────────────────────────────────────────────────────────

export async function upsertAlert(alert: AlertResponse): Promise<void> {
  if (Platform.OS === 'web') {
    memUpsert(alert);
    return;
  }
  const db = await getDb();
  const now = Date.now();
  await db.runAsync(
    'INSERT INTO alerts (region, data, fetched_at) VALUES (?, ?, ?)',
    alert.region,
    JSON.stringify(alert),
    now,
  );
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

export async function getAlertByRegion(
  region: string,
): Promise<AlertResponse | null> {
  if (Platform.OS === 'web') {
    return memNewest(region)?.data ?? null;
  }
  const db = await getDb();
  const row = await db.getFirstAsync<{ data: string }>(
    'SELECT data FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
  );
  return row ? (JSON.parse(row.data) as AlertResponse) : null;
}

export async function getAlertsForRegion(
  region: string,
): Promise<AlertResponse[]> {
  if (Platform.OS === 'web') {
    return (mem[region] ?? []).map((r) => r.data);
  }
  const db = await getDb();
  const rows = await db.getAllAsync<{ data: string }>(
    'SELECT data FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT ?',
    region,
    MAX_PER_REGION,
  );
  return rows.map((r) => JSON.parse(r.data) as AlertResponse);
}

export async function getLastFetchedAt(region: string): Promise<number | null> {
  if (Platform.OS === 'web') {
    return memNewest(region)?.fetchedAt ?? null;
  }
  const db = await getDb();
  const row = await db.getFirstAsync<{ fetched_at: number }>(
    'SELECT fetched_at FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
  );
  return row ? row.fetched_at : null;
}

export async function isStale(region: string): Promise<boolean> {
  const ts = await getLastFetchedAt(region);
  if (!ts) return true;
  return Date.now() - ts > STALE_THRESHOLD_MS;
}
