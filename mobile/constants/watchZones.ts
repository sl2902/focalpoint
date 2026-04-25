// 9 watch zones matching backend WATCH_ZONES list exactly.
// Coordinates are approximate region centres for initial map camera position.

export const WATCH_ZONES = [
  'Palestine',
  'Gaza',
  'Israel',
  'Iran',
  'Ukraine',
  'Sudan',
  'Myanmar',
  'Yemen',
  'Syria',
] as const;

export type WatchZone = (typeof WATCH_ZONES)[number];

export interface WatchZoneCoords {
  latitude: number;
  longitude: number;
  zoomLevel: number;
}

export const WATCH_ZONE_COORDS: Record<WatchZone, WatchZoneCoords> = {
  Palestine: { latitude: 31.9, longitude: 35.2, zoomLevel: 8 },
  Gaza:      { latitude: 31.35, longitude: 34.3, zoomLevel: 10 },
  Israel:    { latitude: 31.5, longitude: 34.75, zoomLevel: 7 },
  Iran:      { latitude: 32.4, longitude: 53.7, zoomLevel: 5 },
  Ukraine:   { latitude: 48.4, longitude: 31.2, zoomLevel: 5 },
  Sudan:     { latitude: 15.6, longitude: 32.5, zoomLevel: 5 },
  Myanmar:   { latitude: 19.8, longitude: 96.1, zoomLevel: 5 },
  Yemen:     { latitude: 15.5, longitude: 48.5, zoomLevel: 5 },
  Syria:     { latitude: 34.8, longitude: 38.9, zoomLevel: 6 },
};
