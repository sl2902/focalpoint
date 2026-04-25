/**
 * Lightweight store for discreet mode toggle.
 * Mirrors the discreetMode field in useSettingsStore but provides a fast
 * synchronous toggle accessible from any screen without going through
 * SecureStore on every render.
 */

import { create } from 'zustand';

interface DiscreetState {
  discreetMode: boolean;
  toggle: () => void;
  setDiscreetMode: (on: boolean) => void;
}

export const useDiscreetStore = create<DiscreetState>((set) => ({
  discreetMode: false,
  toggle: () => set((s) => ({ discreetMode: !s.discreetMode })),
  setDiscreetMode: (discreetMode) => set({ discreetMode }),
}));
