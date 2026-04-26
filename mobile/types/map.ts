import type { Severity } from './api';

export interface ComponentMarker {
  id: string;
  latitude: number;
  longitude: number;
  severity: Severity;
  region: string;
}
