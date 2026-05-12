/**
 * Feed data hook.
 *
 * Data flow:
 *   useFocusEffect — reads SQLite on every focus event (covers back-navigation
 *     from Alert Detail without a version-bump race).
 *   useEffect([days]) — reads SQLite when the days window changes, including
 *     while the screen is in the background.
 *   useEffect([isConnected]) — cold-start stale-while-revalidate:
 *     1. Evicts SQLite rows older than 24 hours.
 *     2. Immediately reads SQLite and displays whatever is cached (may be empty).
 *     3. Simultaneously fetches from backend — always, no staleness gate.
 *     4. When backend responds, writes fresh data to SQLite and updates display.
 *
 * Pull-to-refresh always hits the backend — never reads SQLite only.
 *
 * Background sync:
 *   A setInterval fires every 5 minutes while the component is mounted and
 *   connected. Each tick fetches from the backend, writes to SQLite, and
 *   refreshes the display — no visual spinner (unlike pull-to-refresh).
 *   The JS thread pauses when the app is backgrounded on iOS, so the interval
 *   naturally runs only while the app is in the foreground.
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

const EVICT_THRESHOLD_MS = 24 * 60 * 60 * 1000;   // 24 hours
const BACKGROUND_SYNC_MS = 5 * 60 * 1000;          // 5 minutes

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

  // Cold-start stale-while-revalidate: show SQLite immediately, always fetch backend in parallel.
  useEffect(() => {
    if (didInitialFetch.current || !isConnected) return;
    didInitialFetch.current = true;

    let cancelled = false;

    // Evict rows older than 24h so stale data can't accumulate across restarts.
    deleteAlertsOlderThan(EVICT_THRESHOLD_MS).catch(() => {});

    // Show whatever SQLite has right now so the feed is never blank while waiting.
    getLatestAlertsByDays(days).then((cached) => {
      if (cancelled) return;
      applyAlerts(cached);
      console.log(`[alerts] ${cached.length} showing SQLite while fetching backend`);
    }).catch(() => {});

    // Always fetch from backend on cold start — no staleness gate.
    console.log(`[alerts] cold start — fetching from backend days=${days}`);
    fetchFeed(days)
      .then(async (feed) => {
        // Always write to SQLite regardless of mount state — useFocusEffect must
        // read fresh data on the next tab switch even if this component unmounted
        // during the fetch (e.g. user switched tabs while the request was in-flight).
        console.log(`[alerts] writing ${feed.length} backend alerts to local SQLite after cold start fetch`);
        await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? days)));
        console.log(`[alerts] SQLite read: getLatestAlertsByDays(${days})`);
        const fresh = await getLatestAlertsByDays(days);
        // Only update React state if still mounted.
        if (!cancelled) {
          applyAlerts(fresh);
          console.log(`[alerts] ${fresh.length} alerts served from backend (cold start)`);
        }
      })
      .catch(() => {
        // Network unavailable — SQLite display already set above.
      });

    return () => {
      cancelled = true;
    };
  }, [isConnected]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch from backend when the days window changes — SQLite may be empty for
  // the new days value so reading it alone would show a blank feed.
  useEffect(() => {
    let cancelled = false;
    console.log(`[alerts] days changed to ${days} — fetching from backend`);
    fetchFeed(days)
      .then(async (feed) => {
        await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? days)));
        console.log(`[alerts] SQLite read: getLatestAlertsByDays(${days})`);
        const fresh = await getLatestAlertsByDays(days);
        if (!cancelled) {
          applyAlerts(fresh);
          console.log(`[alerts] ${fresh.length} alerts served from backend (days=${days} change)`);
        }
      })
      .catch(async () => {
        // Network unavailable — show whatever SQLite has for this window.
        const cached = await getLatestAlertsByDays(days);
        if (!cancelled) {
          applyAlerts(cached);
          console.log(`[alerts] ${cached.length} alerts served from SQLite (days=${days} change, network error)`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [days, applyAlerts]);

  // Background sync: fetch from backend every 5 minutes while connected.
  // Does not set refreshing — no spinner for background updates.
  // Interval is cleared on unmount and reset whenever connectivity or days changes.
  useEffect(() => {
    if (!isConnected) return;
    const id = setInterval(() => {
      fetchFeed(days)
        .then(async (feed) => {
          await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? days)));
          const fresh = await getLatestAlertsByDays(days);
          applyAlerts(fresh);
          console.log(`[alerts] background sync — ${fresh.length} alerts refreshed`);
        })
        .catch(() => {
          // Network error during background sync — keep current display.
        });
    }, BACKGROUND_SYNC_MS);
    return () => clearInterval(id);
  }, [isConnected, days, applyAlerts]);

  // Re-read SQLite each time the feed screen regains focus.
  // Fires on mount (if focused) and on every back-navigation from Alert Detail.
  // Never clears alerts — applyAlerts only overwrites once the read resolves.
  // Clears isLoading after the first read regardless of result.
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;
      console.log(`[alerts] SQLite read: getLatestAlertsByDays(${days})`);
      Promise.all([getLatestAlertsByDays(days), getNewestFetchedAt(days)]).then(([cached, newestTs]) => {
        if (!cancelled) {
          applyAlerts(cached);
          setIsLoading(false);
          const ts = newestTs != null ? new Date(newestTs).toISOString() : 'none';
          console.log(`[alerts] ${cached.length} alerts served from SQLite (focus, days=${days}, newest_fetched_at=${ts})`);
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
    console.log('[alerts] revalidate: reading SQLite for days=', days);
    return getLatestAlertsByDays(days).then((result) => {
      console.log('[feed] revalidate returned', result.length, 'alerts, regions:', result.map((a) => a.region));
      applyAlerts(result);
    });
  }, [days, applyAlerts]);

  // Pull-to-refresh always hits the backend — never reads SQLite only.
  const refresh = useCallback(() => {
    if (refreshing) return;
    setRefreshing(true);
    fetchFeed(days)
      .then(async (feed) => {
        await Promise.all(feed.map((a) => upsertAlert(a, a.days ?? days)));
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
