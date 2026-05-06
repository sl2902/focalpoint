/**
 * Feed data hook.
 *
 * Data flow:
 *   useFocusEffect — reads SQLite on every focus event (covers back-navigation
 *     from Alert Detail without a version-bump race).
 *   useEffect([days]) — reads SQLite when the days window changes, including
 *     while the screen is in the background.
 *   useEffect([isConnected]) — one-time app-launch fetch that seeds SQLite
 *     from the backend cache; reads SQLite directly after writing.
 *
 * Blank-screen prevention:
 *   _alertsCache is a module-level variable that survives component remounts.
 *   useState is initialised from _alertsCache so the feed shows existing data
 *   immediately even if the screen remounts (e.g. iOS modal dismissal), and
 *   the subsequent useFocusEffect read silently overwrites it with fresh data.
 *
 * alerts is never cleared before new data arrives — applyAlerts only
 * overwrites once the SQLite read resolves.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useFocusEffect } from 'expo-router';
import { fetchAlertForRegion, fetchFeed } from '../services/alerts';
import { getLatestAlertsByDays, refreshFallbackTimestamp, upsertAlert } from '../services/cache';
import { useConnectivity } from './useConnectivity';
import { useSettingsStore } from '../store/useSettingsStore';
import { useRefreshStore } from '../store/useRefreshStore';
import type { DaysOption } from '../store/useSettingsStore';
import type { AlertResponse } from '../types/api';

interface UseAlertsResult {
  alerts: AlertResponse[];
  days: DaysOption;
  setDays: (d: DaysOption) => Promise<void>;
  refresh: () => void;
  refreshing: boolean;
  revalidate: () => void;
}

// Survives component remounts within the same JS runtime session.
// Initialises useState so the feed never flashes blank on remount.
let _alertsCache: AlertResponse[] = [];

export function useAlerts(): UseAlertsResult {
  const { isConnected } = useConnectivity();
  const days = useSettingsStore((s) => s.days);
  const setDays = useSettingsStore((s) => s.setDays);
  const { refreshingRegion, endRefresh } = useRefreshStore();

  const [alerts, setAlerts] = useState<AlertResponse[]>(_alertsCache);
  const [refreshing, setRefreshing] = useState(false);
  const didInitialFetch = useRef(false);

  // Single write point: keeps the module cache in sync with component state.
  const applyAlerts = useCallback((cached: AlertResponse[]) => {
    _alertsCache = cached;
    setAlerts(cached);
  }, []);

  // One-time app-launch fetch: seed local SQLite from backend cache.
  // Reads SQLite directly after writing — no version bump needed.
  useEffect(() => {
    if (didInitialFetch.current || !isConnected) return;
    didInitialFetch.current = true;

    fetchFeed()
      .then(async (feed) => {
        await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? 7)));
        const cached = await getLatestAlertsByDays(days);
        applyAlerts(cached);
      })
      .catch(() => {
        // Network unavailable — existing data stays visible.
      });
  }, [isConnected]); // eslint-disable-line react-hooks/exhaustive-deps

  // Re-read SQLite when the days window changes (covers background changes).
  // applyAlerts is stable (empty deps), so this is effectively useEffect([days]).
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
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      getLatestAlertsByDays(days).then((cached) => {
        if (!cancelled) applyAlerts(cached);
      });
      return () => {
        cancelled = true;
      };
    }, [days, applyAlerts]),
  );

  // Background force-refresh triggered from Alert Detail.
  // Reads SQLite directly after writing so the feed reflects the new data.
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

  const revalidate = useCallback(() => {
    getLatestAlertsByDays(days).then(applyAlerts);
  }, [days, applyAlerts]);

  const refresh = useCallback(() => {
    if (refreshing) return;
    setRefreshing(true);
    getLatestAlertsByDays(days)
      .then(applyAlerts)
      .finally(() => setRefreshing(false));
  }, [refreshing, days, applyAlerts]);

  return { alerts, days, setDays, refresh, refreshing, revalidate };
}
