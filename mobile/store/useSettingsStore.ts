import { create } from 'zustand';
import { getItem, setItem } from '../services/storage';
import type { WatchZone } from '../constants/watchZones';

const STORAGE_KEY = 'focalpoint_settings';

export type DaysOption = 1 | 3 | 7 | 14 | 30;

interface SettingsState {
  watchZone: WatchZone;
  watchZoneArea: string;
  language: string;
  days: DaysOption;
  discreetMode: boolean;
  notifications: boolean;

  setWatchZone: (zone: WatchZone) => Promise<void>;
  setWatchZoneArea: (area: string) => Promise<void>;
  setLanguage: (lang: string) => Promise<void>;
  setDays: (days: DaysOption) => Promise<void>;
  setDiscreetMode: (on: boolean) => Promise<void>;
  setNotifications: (on: boolean) => Promise<void>;
  hydrate: () => Promise<void>;
}

const DEFAULTS: Omit<
  SettingsState,
  | 'setWatchZone'
  | 'setWatchZoneArea'
  | 'setLanguage'
  | 'setDays'
  | 'setDiscreetMode'
  | 'setNotifications'
  | 'hydrate'
> = {
  watchZone: 'Gaza',
  watchZoneArea: '',
  language: 'en',
  days: 7,
  discreetMode: false,
  notifications: true,
};

async function persist(partial: Partial<typeof DEFAULTS>): Promise<void> {
  const current = await getItem(STORAGE_KEY);
  const existing = current ? JSON.parse(current) : {};
  await setItem(STORAGE_KEY, JSON.stringify({ ...existing, ...partial }));
}

export const useSettingsStore = create<SettingsState>((set) => ({
  ...DEFAULTS,

  setWatchZone: async (watchZone) => {
    set({ watchZone });
    await persist({ watchZone });
  },
  setWatchZoneArea: async (watchZoneArea) => {
    set({ watchZoneArea });
    await persist({ watchZoneArea });
  },
  setLanguage: async (language) => {
    set({ language });
    await persist({ language });
  },
  setDays: async (days) => {
    set({ days });
    await persist({ days });
  },
  setDiscreetMode: async (discreetMode) => {
    set({ discreetMode });
    await persist({ discreetMode });
  },
  setNotifications: async (notifications) => {
    set({ notifications });
    await persist({ notifications });
  },

  hydrate: async () => {
    const stored = await getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<typeof DEFAULTS>;
      set({ ...DEFAULTS, ...parsed });
    }
  },
}));
