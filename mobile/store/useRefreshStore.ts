import { create } from 'zustand';

interface RefreshStore {
  refreshingRegion: string | null;
  startRefresh: (region: string) => void;
  endRefresh: () => void;
}

export const useRefreshStore = create<RefreshStore>((set) => ({
  refreshingRegion: null,
  startRefresh: (region) => set({ refreshingRegion: region }),
  endRefresh: () => set({ refreshingRegion: null }),
}));
