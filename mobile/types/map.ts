import type { Severity } from './api';

export interface ComponentMarker {
  id: string;
  latitude: number;
  longitude: number;
  severity: Severity;
  region: string;
  // Populated from valid (non-fallback) alert data for richer map popups.
  timestamp?: string;
  summary?: string;
  confidence?: number;
}
