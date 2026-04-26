/**
 * Feed data hook.
 *
 * Network policy (one call only):
 *   On first mount (app launch), fires GET /alerts/feed once to seed
 *   local SQLite from the backend cache. All subsequent reads are local.
 *
 * Days change / pull-to-refresh / new data arrival:
 *   All re-read local SQLite via a single effect that depends on both
 *   `days` and `version`. The initial fetch only bumps `version` after
 *   writing to SQLite — it never calls setAlerts directly, avoiding the
 *   stale-closure bug where a days=7 capture would overwrite a days=30
 *   display after Zustand hydration restored the persisted value.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useFocusEffect } from 'expo-router';
import { fetchFeed } from '../services/alerts';
import { getLatestAlertsByDays, upsertAlert } from '../services/cache';
import { useConnectivity } from './useConnectivity';
import { useSettingsStore } from '../store/useSettingsStore';
import type { DaysOption } from '../store/useSettingsStore';
import type { AlertResponse } from '../types/api';

interface UseAlertsResult {
  alerts: AlertResponse[];
  days: DaysOption;
  setDays: (d: DaysOption) => Promise<void>;
  refresh: () => void;
  refreshing: boolean;
}

export function useAlerts(): UseAlertsResult {
  const { isConnected } = useConnectivity();
  const days = useSettingsStore((s) => s.days);
  const setDays = useSettingsStore((s) => s.setDays);

  const [alerts, setAlerts] = useState<AlertResponse[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  // Bumped after SQLite is written so the read effect re-fires with the
  // current days value rather than a stale closure value.
  const [version, setVersion] = useState(0);
  const didInitialFetch = useRef(false);

  // One-time app-launch fetch: seed local SQLite from backend cache.
  // Does NOT call setAlerts — only writes SQLite and bumps version so
  // the read effect below picks up the data with the live `days` value.
  useEffect(() => {
    if (didInitialFetch.current || !isConnected) return;
    didInitialFetch.current = true;

    fetchFeed()
      .then(async (feed) => {
        await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? 7)));
        setVersion((v) => v + 1);
      })
      .catch(() => {
        // Network unavailable — existing SQLite data stays visible.
      });
  }, [isConnected]); // eslint-disable-line react-hooks/exhaustive-deps

  // Single source of truth for setAlerts: re-reads SQLite whenever
  // days changes, new data arrives (version bump), or pull-to-refresh.
  useEffect(() => {
    let cancelled = false;
    getLatestAlertsByDays(days).then((cached) => {
      if (!cancelled) setAlerts(cached);
    });
    return () => {
      cancelled = true;
    };
  }, [days, version]);

  // Re-read SQLite whenever the feed screen regains focus — catches writes
  // made by the Alert Detail refresh button while the screen was in the background.
  useFocusEffect(
    useCallback(() => {
      setVersion((v) => v + 1);
    }, []),
  );

  // Pull-to-refresh: bump version to re-read SQLite. No network call.
  const refresh = useCallback(() => {
    if (refreshing) return;
    setRefreshing(true);
    setVersion((v) => v + 1);
    // setRefreshing(false) after the SQLite read completes.
    // We approximate this with a short timeout since the read effect
    // runs asynchronously and has no callback channel back here.
    setTimeout(() => setRefreshing(false), 300);
  }, [refreshing]);

  return { alerts, days, setDays, refresh, refreshing };
}
