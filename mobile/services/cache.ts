import { Platform } from 'react-native';
import * as SQLite from 'expo-sqlite';
import type { AlertResponse } from '../types/api';

const MAX_PER_REGION = 100;

const SEVERITY_ORDER = ['CRITICAL', 'RED', 'AMBER', 'GREEN', 'INSUFFICIENT_DATA'];

function sortBySeverity(alerts: AlertResponse[]): AlertResponse[] {
  return [...alerts].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
  );
}

// ── Web: in-memory keyed by "region:days" ───────────────────────────────────

const mem: Record<string, AlertResponse> = {};

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
      days       INTEGER NOT NULL DEFAULT 7,
      data       TEXT    NOT NULL,
      fetched_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_alerts_region ON alerts(region);
    CREATE INDEX IF NOT EXISTS idx_alerts_region_days ON alerts(region, days);
  `);
  // Migrate existing tables that predate the days column.
  try {
    await _db.execAsync('ALTER TABLE alerts ADD COLUMN days INTEGER NOT NULL DEFAULT 7');
  } catch {
    // Column already exists — safe to ignore.
  }
  return _db;
}

// ── Exported API ────────────────────────────────────────────────────────────

const FALLBACK_MARKERS = [
  'Gemma 4 API call failed',
  'Output validation failed',
];

function isFallback(summary: string): boolean {
  return FALLBACK_MARKERS.some((m) => summary.includes(m));
}

export async function upsertAlert(alert: AlertResponse, days: number): Promise<void> {
  if (Platform.OS === 'web') {
    mem[`${alert.region}:${days}`] = alert;
    return;
  }
  const db = await getDb();
  const now = Date.now();
  await db.runAsync(
    'INSERT INTO alerts (region, days, data, fetched_at) VALUES (?, ?, ?, ?)',
    alert.region,
    days,
    JSON.stringify(alert),
    now,
  );
  // Keep at most MAX_PER_REGION rows per (region, days).
  await db.runAsync(
    `DELETE FROM alerts
     WHERE region = ? AND days = ?
       AND id NOT IN (
         SELECT id FROM alerts WHERE region = ? AND days = ?
         ORDER BY fetched_at DESC LIMIT ?
       )`,
    alert.region, days,
    alert.region, days,
    MAX_PER_REGION,
  );
}

/** Latest non-fallback alert per region for the given days window, sorted by severity. */
export async function getLatestAlertsByDays(days: number): Promise<AlertResponse[]> {
  if (Platform.OS === 'web') {
    const suffix = `:${days}`;
    const alerts = Object.entries(mem)
      .filter(([k]) => k.endsWith(suffix))
      .map(([, v]) => v)
      .filter((a) => !isFallback(a.summary));
    return sortBySeverity(alerts);
  }
  const db = await getDb();
  // Rows ordered newest-first. Deduplicate per region, skipping fallback
  // rows WITHOUT marking the region as seen so that an older valid row
  // for the same region can still surface.
  const rows = await db.getAllAsync<{ region: string; data: string }>(
    'SELECT region, data FROM alerts WHERE days = ? ORDER BY fetched_at DESC',
    days,
  );
  const seen = new Set<string>();
  const latest: AlertResponse[] = [];
  for (const row of rows) {
    if (seen.has(row.region)) continue;
    const alert = JSON.parse(row.data) as AlertResponse;
    if (isFallback(alert.summary)) continue; // skip — don't mark seen
    seen.add(row.region);
    latest.push(alert);
  }
  return sortBySeverity(latest);
}

/** Most recent alert for a region across any days window — used by Alert Detail. */
export async function getAlertByRegion(region: string): Promise<AlertResponse | null> {
  if (Platform.OS === 'web') {
    const entry = Object.entries(mem).find(([k]) => k.startsWith(`${region}:`));
    return entry ? entry[1] : null;
  }
  const db = await getDb();
  const row = await db.getFirstAsync<{ data: string }>(
    'SELECT data FROM alerts WHERE region = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
  );
  return row ? (JSON.parse(row.data) as AlertResponse) : null;
}
