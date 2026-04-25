/**
 * Fetches the alert feed from the backend and falls back to SQLite cache
 * when offline. Tracks loading, error, and staleness state.
 */

import { useState, useEffect, useCallback } from 'react';
import { fetchFeed } from '../services/alerts';
import { getAlertsForRegion, upsertAlert, isStale } from '../services/cache';
import { useConnectivity } from './useConnectivity';
import { useSettingsStore } from '../store/useSettingsStore';
import type { AlertResponse } from '../types/api';
import { WATCH_ZONES } from '../constants/watchZones';

interface UseAlertsResult {
  alerts: AlertResponse[];
  loading: boolean;
  error: string | null;
  stale: boolean;
  lastFetchedAt: Date | null;
  refresh: () => Promise<void>;
}

export function useAlerts(): UseAlertsResult {
  const { isConnected } = useConnectivity();
  const days = useSettingsStore((s) => s.days);

  const [alerts, setAlerts] = useState<AlertResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);

    if (isConnected) {
      try {
        const fresh = await fetchFeed(days);
        // Persist each alert to SQLite cache
        await Promise.all(fresh.map(upsertAlert));
        setAlerts(fresh);
        setStale(false);
        setLastFetchedAt(new Date());
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setError(message);
        // Fall back to cache on error
        await loadFromCache();
      }
    } else {
      await loadFromCache();
    }

    setLoading(false);
  }, [isConnected, days]);

  async function loadFromCache() {
    const cached: AlertResponse[] = [];
    for (const zone of WATCH_ZONES) {
      const zoneAlerts = await getAlertsForRegion(zone);
      cached.push(...zoneAlerts);
    }
    setAlerts(cached);
    setStale(true);
    setLastFetchedAt(null);
  }

  useEffect(() => {
    refresh();
  }, [days]);

  return { alerts, loading, error, stale, lastFetchedAt, refresh };
}
