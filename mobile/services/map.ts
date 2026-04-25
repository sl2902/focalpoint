import { apiGet } from './api';
import type { MarkersResponse } from '../types/api';

/**
 * Fetch incident map markers for a given region.
 * Lightweight endpoint — no Gemma call, returns raw geo events.
 */
export async function fetchMarkers(
  region: string,
  days: number = 7,
): Promise<MarkersResponse> {
  return apiGet<MarkersResponse>('/map/markers', {
    params: { region, days },
  });
}
