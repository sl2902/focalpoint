import { create } from 'zustand';
import type { AlertResponse } from '../types/api';

interface RefreshStore {
  // Background fallback refresh (Alert Detail → feed card loading indicator).
  refreshingRegion: string | null;
  startRefresh: (region: string) => void;
  endRefresh: () => void;

  // Per-region in-flight load set — persists across navigation so Load/Refresh
  // buttons stay disabled if the user navigates away and back mid-fetch.
  loadingRegions: Set<string>;
  startLoad: (region: string) => void;
  endLoad: (region: string) => void;

  // Completed refresh results — keyed by region. Alert Detail reads this on
  // mount so a refresh that completed while the user was navigated away is
  // applied immediately when they return, without requiring a new button tap.
  refreshedAlerts: Record<string, AlertResponse>;
  setRefreshedAlert: (region: string, alert: AlertResponse) => void;
  clearRefreshedAlert: (region: string) => void;

  // Monotonic counter bumped after every successful region refresh. Feed
  // watches this to call revalidate() and update cards without pull-to-refresh.
  completedRefreshVersion: number;
  bumpCompletedRefresh: () => void;
}

export const useRefreshStore = create<RefreshStore>((set) => ({
  refreshingRegion: null,
  startRefresh: (region) => set({ refreshingRegion: region }),
  endRefresh: () => set({ refreshingRegion: null }),

  loadingRegions: new Set<string>(),
  startLoad: (region) =>
    set((s) => ({ loadingRegions: new Set([...s.loadingRegions, region]) })),
  endLoad: (region) =>
    set((s) => {
      const next = new Set(s.loadingRegions);
      next.delete(region);
      return { loadingRegions: next };
    }),

  refreshedAlerts: {},
  setRefreshedAlert: (region, alert) =>
    set((s) => ({ refreshedAlerts: { ...s.refreshedAlerts, [region]: alert } })),
  clearRefreshedAlert: (region) =>
    set((s) => {
      const { [region]: _, ...rest } = s.refreshedAlerts;
      return { refreshedAlerts: rest };
    }),

  completedRefreshVersion: 0,
  bumpCompletedRefresh: () =>
    set((s) => ({ completedRefreshVersion: s.completedRefreshVersion + 1 })),
}));
