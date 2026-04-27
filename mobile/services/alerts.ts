import { apiGet } from './api';
import type { AlertResponse } from '../types/api';

/**
 * Fetch the latest cached alert per region from the backend store.
 * The backend serves this from SQLite — no Gemma calls are made.
 */
export async function fetchFeed(): Promise<AlertResponse[]> {
  return apiGet<AlertResponse[]>('/alerts/feed');
}

/**
 * Fetch an alert for a single region.
 *
 * force=true  — bypasses all server-side caches (SQLite TTL + Redis) and
 *               always runs a fresh GDELT + Gemma 4 pipeline. Used by the
 *               Alert Detail Refresh button.
 * force=false — uses the backend SQLite cache when fresh; only runs the
 *               live pipeline on a cache miss. Used by the feed Load button.
 */
export async function fetchAlertForRegion(
  region: string,
  days: number,
  force = false,
): Promise<AlertResponse> {
  return apiGet<AlertResponse>(`/alerts/${encodeURIComponent(region)}`, {
    params: { days, ...(force && { force: true }) },
  });
}
