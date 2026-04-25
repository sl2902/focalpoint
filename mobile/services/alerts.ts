import { apiGet } from './api';
import type { AlertResponse } from '../types/api';

/**
 * Fetch the proactive alert feed — latest stored alert per watch zone,
 * ordered by severity. Respects the `days` setting from the caller.
 */
export async function fetchFeed(days: number): Promise<AlertResponse[]> {
  return apiGet<AlertResponse[]>('/alerts/feed', { params: { days } });
}

/**
 * Fetch the alert for a single named region.
 */
export async function fetchAlertForRegion(
  region: string,
  days: number,
): Promise<AlertResponse> {
  return apiGet<AlertResponse>(`/alerts/${encodeURIComponent(region)}`, {
    params: { days },
  });
}
