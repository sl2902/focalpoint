/**
 * Progressive alert loader — fetches each watch zone independently.
 *
 * For each region:
 *   1. Immediately seeds from local SQLite cache (instant render).
 *   2. Fires a live fetch against GET /alerts/{region}?days=N.
 *   3. Updates the entry when the live result resolves.
 *
 * Re-runs whenever `days` changes or `refresh()` is called.
 */

import { useState, useEffect, useCallback } from 'react';
import { fetchAlertForRegion } from '../services/alerts';
import { getAlertByRegion, upsertAlert } from '../services/cache';
import { useConnectivity } from './useConnectivity';
import { useSettingsStore } from '../store/useSettingsStore';
import type { DaysOption } from '../store/useSettingsStore';
import type { AlertResponse } from '../types/api';
import { WATCH_ZONES } from '../constants/watchZones';

export type EntryStatus = 'loading' | 'done' | 'error';

export interface RegionEntry {
  region: string;
  alert: AlertResponse | null;
  status: EntryStatus;
}

interface UseAlertsResult {
  entries: RegionEntry[];
  days: DaysOption;
  setDays: (d: DaysOption) => Promise<void>;
  refresh: () => void;
  refreshing: boolean;
}

const INITIAL_ENTRIES: RegionEntry[] = WATCH_ZONES.map((region) => ({
  region,
  alert: null,
  status: 'loading',
}));

export function useAlerts(): UseAlertsResult {
  const { isConnected } = useConnectivity();
  const days = useSettingsStore((s) => s.days);
  const setDays = useSettingsStore((s) => s.setDays);

  const [entries, setEntries] = useState<RegionEntry[]>(INITIAL_ENTRIES);
  const [version, setVersion] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(() => {
    setRefreshing(true);
    setVersion((v) => v + 1);
  }, []);

  function updateEntry(region: string, patch: Partial<RegionEntry>) {
    setEntries((prev) =>
      prev.map((e) => (e.region === region ? { ...e, ...patch } : e))
    );
  }

  useEffect(() => {
    let cancelled = false;
    const pending = { count: WATCH_ZONES.length };

    function onZoneDone() {
      pending.count -= 1;
      if (pending.count === 0 && !cancelled) {
        setRefreshing(false);
      }
    }

    // Reset all to loading (preserve any existing alert for instant display).
    setEntries((prev) =>
      prev.map((e) => ({ ...e, status: 'loading' as EntryStatus }))
    );

    WATCH_ZONES.forEach(async (zone) => {
      // Step 1 — seed from local cache for instant render.
      const cached = await getAlertByRegion(zone);
      if (cached && !cancelled) {
        updateEntry(zone, { alert: cached, status: 'loading' });
      }

      if (!isConnected) {
        // Offline — cache is the final answer.
        if (!cancelled) {
          updateEntry(zone, {
            alert: cached ?? null,
            status: cached ? 'done' : 'error',
          });
        }
        onZoneDone();
        return;
      }

      // Step 2 — fetch live from backend.
      try {
        const fresh = await fetchAlertForRegion(zone, days);
        if (cancelled) return;
        await upsertAlert(fresh);
        updateEntry(zone, { alert: fresh, status: 'done' });
      } catch {
        if (cancelled) return;
        // Keep cached alert visible if we have one; mark error only if empty.
        updateEntry(zone, { status: cached ? 'done' : 'error' });
      } finally {
        onZoneDone();
      }
    });

    return () => {
      cancelled = true;
    };
  }, [days, isConnected, version]);

  return { entries, days, setDays, refresh, refreshing };
}
