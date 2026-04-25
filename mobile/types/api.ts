// TypeScript interfaces mirroring exact backend Pydantic field names.
// Do NOT rename fields — they must match the JSON responses from FastAPI.

export type Severity =
  | 'GREEN'
  | 'AMBER'
  | 'RED'
  | 'CRITICAL'
  | 'INSUFFICIENT_DATA';

export interface Citation {
  id: string;          // GDELT Cloud event ID (conflict_*), URL, or CPJ/RSF identifier
  description: string; // Human-readable citation label, always in English
}

export interface AlertResponse {
  severity: Severity;
  summary: string;
  source_citations: Citation[];
  region: string;
  timestamp: string;   // ISO 8601
  confidence: number;  // 0.0–1.0
}

export interface MapMarker {
  event_id: string;
  latitude: number;
  longitude: number;
  event_type: string | null;
  region: string;
  timestamp: string;   // YYYY-MM-DD
}

export interface MarkersResponse {
  markers: MapMarker[];
  region: string;
  total: number;
}

export interface QueryResponse {
  answer: string;
  severity: Severity;
  source_citations: Citation[];
  region: string;
  timestamp: string;   // ISO 8601
  was_sanitised: boolean;
}

export interface TranscribeResponse {
  text: string;
  language: string;    // 2-letter language code, e.g. "en"
}

export interface HealthResponse {
  status: string;
  version: string;
}
