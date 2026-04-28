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

// Promise-based singleton: concurrent callers await the same promise instead
// of each opening their own connection and racing on schema setup.
let _dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!_dbPromise) {
    _dbPromise = _openDb();
  }
  return _dbPromise;
}

async function _openDb(): Promise<SQLite.SQLiteDatabase> {
  const db = await SQLite.openDatabaseAsync(DB_NAME);
  await db.execAsync(`
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
    await db.execAsync('ALTER TABLE alerts ADD COLUMN days INTEGER NOT NULL DEFAULT 7');
  } catch {
    // Column already exists — safe to ignore.
  }
  return db;
}

// ── Exported API ────────────────────────────────────────────────────────────

const FALLBACK_MARKERS = [
  'safe fallback response',
  'Output validation failed',
];

export function isFallback(alert: AlertResponse): boolean {
  if (FALLBACK_MARKERS.some((m) => alert.summary.includes(m))) return true;
  return (
    alert.source_citations.length > 0 &&
    alert.source_citations.every((c) => c.id.startsWith('FALLBACK:'))
  );
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
  // Trim to MAX_PER_REGION rows per (region, days). Two queries avoid the
  // expo-sqlite subquery parameter binding bug (WHERE ? inside a subquery
  // gets NULL). OFFSET is hardcoded so only region and days are bound.
  const cutoff = await db.getFirstAsync<{ fetched_at: number }>(
    `SELECT fetched_at FROM alerts WHERE region = ? AND days = ? ORDER BY fetched_at DESC LIMIT 1 OFFSET ${MAX_PER_REGION - 1}`,
    alert.region,
    days,
  );
  if (cutoff) {
    await db.runAsync(
      'DELETE FROM alerts WHERE region = ? AND days = ? AND fetched_at < ?',
      alert.region,
      days,
      cutoff.fetched_at,
    );
  }
}

/**
 * Latest alert per region for the given days window, sorted by severity.
 * Valid alerts take priority; a fallback is returned only when no valid row
 * exists for that region so the feed can render it with degraded styling.
 */
export async function getLatestAlertsByDays(days: number): Promise<AlertResponse[]> {
  if (Platform.OS === 'web') {
    const suffix = `:${days}`;
    const all = Object.entries(mem)
      .filter(([k]) => k.endsWith(suffix))
      .map(([, v]) => v);
    const validByRegion = new Map(
      all.filter((a) => !isFallback(a)).map((a) => [a.region, a]),
    );
    const fallbackByRegion = new Map(
      all.filter((a) => isFallback(a)).map((a) => [a.region, a]),
    );
    const result = [
      ...validByRegion.values(),
      ...[...fallbackByRegion.values()].filter((a) => !validByRegion.has(a.region)),
    ];
    return sortBySeverity(result);
  }
  const db = await getDb();
  const rows = await db.getAllAsync<{ region: string; data: string }>(
    'SELECT region, data FROM alerts WHERE days = ? ORDER BY fetched_at DESC',
    days,
  );
  // Two passes: first collect newest valid alert per region, then collect
  // newest fallback only for regions that have no valid alert at all.
  const validByRegion = new Map<string, AlertResponse>();
  const fallbackByRegion = new Map<string, AlertResponse>();
  for (const row of rows) {
    const alert = JSON.parse(row.data) as AlertResponse;
    if (isFallback(alert)) {
      if (!fallbackByRegion.has(row.region)) fallbackByRegion.set(row.region, alert);
    } else {
      if (!validByRegion.has(row.region)) validByRegion.set(row.region, alert);
    }
  }
  const result = [
    ...validByRegion.values(),
    ...[...fallbackByRegion.values()].filter((a) => !validByRegion.has(a.region)),
  ];
  return sortBySeverity(result);
}

/**
 * Stamp the latest fallback row for (region, days) with the current time.
 * Called when a force-refresh attempt fails at the network level so the feed
 * card shows when the retry was last tried, not the original failure time.
 */
export async function refreshFallbackTimestamp(region: string, days: number): Promise<void> {
  if (Platform.OS === 'web') {
    const key = `${region}:${days}`;
    if (key in mem && isFallback(mem[key])) {
      mem[key] = { ...mem[key], timestamp: new Date().toISOString() };
    }
    return;
  }
  const db = await getDb();
  const row = await db.getFirstAsync<{ id: number; data: string }>(
    'SELECT id, data FROM alerts WHERE region = ? AND days = ? ORDER BY fetched_at DESC LIMIT 1',
    region,
    days,
  );
  if (!row) return;
  const alert = JSON.parse(row.data) as AlertResponse;
  if (!isFallback(alert)) return;
  const updated: AlertResponse = { ...alert, timestamp: new Date().toISOString() };
  await db.runAsync(
    'UPDATE alerts SET data = ?, fetched_at = ? WHERE id = ?',
    JSON.stringify(updated),
    Date.now(),
    row.id,
  );
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
