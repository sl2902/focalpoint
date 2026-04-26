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
 * Trigger a live assessment for a single region.
 * This is the only path that may invoke Gemma 4.
 */
export async function fetchAlertForRegion(
  region: string,
  days: number,
): Promise<AlertResponse> {
  return apiGet<AlertResponse>(`/alerts/${encodeURIComponent(region)}`, {
    params: { days },
  });
}
