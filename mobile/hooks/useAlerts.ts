/**
 * Feed data hook.
 *
 * Data flow:
 *   useFocusEffect — reads SQLite on every focus event (covers back-navigation
 *     from Alert Detail without a version-bump race).
 *   useEffect([days]) — reads SQLite when the days window changes, including
 *     while the screen is in the background.
 *   useEffect([isConnected]) — cold-start fetch that:
 *     1. Evicts SQLite rows older than 24 hours.
 *     2. Checks cache age: if newest cached row is older than 8 hours, fetches
 *        from backend and overwrites SQLite; otherwise reads SQLite directly.
 *
 * Pull-to-refresh always hits the backend — never reads SQLite only.
 *
 * Blank-screen prevention:
 *   _alertsCache is a module-level variable that survives component remounts.
 *   useState is initialised from _alertsCache so the feed shows existing data
 *   immediately even if the screen remounts (e.g. iOS modal dismissal), and
 *   the subsequent useFocusEffect read silently overwrites it with fresh data.
 *
 * alerts is never cleared before new data arrives — applyAlerts only
 * overwrites once the read/fetch resolves.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useFocusEffect } from 'expo-router';
import { fetchAlertForRegion, fetchFeed } from '../services/alerts';
import {
  deleteAlertsOlderThan,
  getLatestAlertsByDays,
  getNewestFetchedAt,
  refreshFallbackTimestamp,
  upsertAlert,
} from '../services/cache';
import { useConnectivity } from './useConnectivity';
import { useSettingsStore } from '../store/useSettingsStore';
import { useRefreshStore } from '../store/useRefreshStore';
import type { DaysOption } from '../store/useSettingsStore';
import type { AlertResponse } from '../types/api';

interface UseAlertsResult {
  alerts: AlertResponse[];
  isLoading: boolean;
  days: DaysOption;
  setDays: (d: DaysOption) => Promise<void>;
  refresh: () => void;
  refreshing: boolean;
  revalidate: () => Promise<void>;
}

const STALE_THRESHOLD_MS = 8 * 60 * 60 * 1000;   // 8 hours
const EVICT_THRESHOLD_MS = 24 * 60 * 60 * 1000;   // 24 hours

// Survives component remounts within the same JS runtime session.
// Initialises useState so the feed never flashes blank on remount.
let _alertsCache: AlertResponse[] = [];

export function useAlerts(): UseAlertsResult {
  const { isConnected } = useConnectivity();
  const days = useSettingsStore((s) => s.days);
  const setDays = useSettingsStore((s) => s.setDays);
  const { refreshingRegion, endRefresh } = useRefreshStore();

  const [alerts, setAlerts] = useState<AlertResponse[]>(_alertsCache);
  // True until the first SQLite read completes — prevents EmptyRegionCard flash.
  // Starts false when _alertsCache is already populated (component remount).
  const [isLoading, setIsLoading] = useState(_alertsCache.length === 0);
  const [refreshing, setRefreshing] = useState(false);
  const didInitialFetch = useRef(false);

  // Single write point: keeps the module cache in sync with component state.
  const applyAlerts = useCallback((cached: AlertResponse[]) => {
    _alertsCache = cached;
    setAlerts(cached);
  }, []);

  // Cold-start: evict 24h-old rows, check 8h staleness, fetch or serve from SQLite.
  useEffect(() => {
    if (didInitialFetch.current || !isConnected) return;
    didInitialFetch.current = true;

    (async () => {
      // Always evict rows older than 24h on cold start so stale data can't
      // accumulate across app restarts.
      await deleteAlertsOlderThan(EVICT_THRESHOLD_MS);

      const newestFetchedAt = await getNewestFetchedAt(days);
      const ageMs = newestFetchedAt != null ? Date.now() - newestFetchedAt : Infinity;
      const isStale = ageMs > STALE_THRESHOLD_MS;

      if (isStale) {
        console.log(
          `[alerts] cache stale (age=${(ageMs / 3_600_000).toFixed(1)}h,` +
          ` fetched_at=${newestFetchedAt ? new Date(newestFetchedAt).toISOString() : 'none'})` +
          ` — fetching from backend`,
        );
        const feed = await fetchFeed();
        await Promise.all(feed.map((a) => upsertAlert(a, days)));
        const cached = await getLatestAlertsByDays(days);
        applyAlerts(cached);
        console.log(`[alerts] ${cached.length} alerts served from backend`);
      } else {
        const cached = await getLatestAlertsByDays(days);
        applyAlerts(cached);
        console.log(
          `[alerts] ${cached.length} alerts served from SQLite` +
          ` (fetched_at=${newestFetchedAt ? new Date(newestFetchedAt).toISOString() : 'none'},` +
          ` age=${(ageMs / 3_600_000).toFixed(1)}h)`,
        );
      }
    })().catch(() => {
      // Network unavailable — existing SQLite data stays visible.
    });
  }, [isConnected]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-read SQLite when the days window changes (covers background changes).
  useEffect(() => {
    let cancelled = false;
    getLatestAlertsByDays(days).then((cached) => {
      if (!cancelled) applyAlerts(cached);
    });
    return () => {
      cancelled = true;
    };
  }, [days, applyAlerts]);

  // Re-read SQLite each time the feed screen regains focus.
  // Fires on mount (if focused) and on every back-navigation from Alert Detail.
  // Never clears alerts — applyAlerts only overwrites once the read resolves.
  // Clears isLoading after the first read regardless of result.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      getLatestAlertsByDays(days).then((cached) => {
        if (!cancelled) {
          applyAlerts(cached);
          setIsLoading(false);
        }
      }).catch(() => {
        if (!cancelled) setIsLoading(false);
      });
      return () => {
        cancelled = true;
      };
    }, [days, applyAlerts]),
  );

  // Background force-refresh triggered from Alert Detail.
  useEffect(() => {
    if (!refreshingRegion) return;
    const region = refreshingRegion;
    let cancelled = false;

    fetchAlertForRegion(region, days, true)
      .then(async (fresh) => {
        if (cancelled) return;
        await upsertAlert(fresh, fresh.days ?? days);
        endRefresh();
        const cached = await getLatestAlertsByDays(days);
        if (!cancelled) applyAlerts(cached);
      })
      .catch(() => {
        if (cancelled) return;
        refreshFallbackTimestamp(region, days)
          .catch(() => {})
          .finally(async () => {
            endRefresh();
            const cached = await getLatestAlertsByDays(days);
            if (!cancelled) applyAlerts(cached);
          });
      });

    return () => {
      cancelled = true;
    };
  }, [refreshingRegion, days]); // eslint-disable-line react-hooks/exhaustive-deps

  const revalidate = useCallback((): Promise<void> => {
    return getLatestAlertsByDays(days).then(applyAlerts);
  }, [days, applyAlerts]);

  // Pull-to-refresh always hits the backend — never reads SQLite only.
  const refresh = useCallback(() => {
    if (refreshing) return;
    setRefreshing(true);
    fetchFeed()
      .then(async (feed) => {
        await Promise.all(feed.map((a) => upsertAlert(a, days)));
        const cached = await getLatestAlertsByDays(days);
        console.log(`[alerts] ${cached.length} alerts served from backend (pull-to-refresh)`);
        applyAlerts(cached);
      })
      .catch(async () => {
        // Network error — show whatever SQLite has.
        const cached = await getLatestAlertsByDays(days);
        console.log(`[alerts] ${cached.length} alerts served from SQLite (refresh network error)`);
        applyAlerts(cached);
      })
      .finally(() => setRefreshing(false));
  }, [refreshing, days, applyAlerts]);

  return { alerts, isLoading, days, setDays, refresh, refreshing, revalidate };
}
