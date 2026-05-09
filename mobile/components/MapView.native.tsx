import React, { useRef, useCallback, useMemo } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, LogBox } from 'react-native';

LogBox.ignoreLogs(['MapLibre Native [ERROR]']);
// Type-only import — erased at compile time, never triggers TurboModuleRegistry.
import type * as MapLibreModule from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import { WATCH_ZONE_COORDS } from '../constants/watchZones';
import { MapFallback } from './MapFallback';
import type { ComponentMarker } from '../types/map';

const TILE_STYLE = 'https://demotiles.maplibre.org/style.json';

// require() instead of static import so the TurboModuleRegistry throw is caught
// here rather than crashing the module factory and leaving default=undefined for
// React.lazy. If MLRNCameraModule is absent the component renders MapFallback.
// eslint-disable-next-line @typescript-eslint/no-require-imports
let _maplibre: typeof MapLibreModule | null = null;
try {
  _maplibre = require('@maplibre/maplibre-react-native');
} catch {
  // MLRNCameraModule not registered in this binary.
}

interface Props {
  markers: ComponentMarker[];
  onMarkerPress: (marker: ComponentMarker) => void;
}

const MIN_ZOOM = 1;
const MAX_ZOOM = 18;
const HOME_ZOOM = 3;

const _coords = Object.values(WATCH_ZONE_COORDS);
const BOUNDS_MIN_LNG = Math.min(..._coords.map((c) => c.longitude));
const BOUNDS_MIN_LAT = Math.min(..._coords.map((c) => c.latitude));
const BOUNDS_MAX_LNG = Math.max(..._coords.map((c) => c.longitude));
const BOUNDS_MAX_LAT = Math.max(..._coords.map((c) => c.latitude));

const HOME_CENTER: [number, number] = [
  (BOUNDS_MIN_LNG + BOUNDS_MAX_LNG) / 2,
  (BOUNDS_MIN_LAT + BOUNDS_MAX_LAT) / 2,
];

// Visual offsets applied only to display coordinates so Gaza / Palestine / Israel
// don't overlap at the overview zoom level. Gaza stays as the anchor.
const DISPLAY_OFFSETS: Record<string, { latOffset: number; lngOffset: number }> = {
  Palestine: { latOffset: 0.5, lngOffset: 0.0 },
  Israel:    { latOffset: 0.3, lngOffset: 0.6 },
};

// MapLibre match expression: circle color from severity string property.
const POINT_COLOR_EXPR: any = [
  'match', ['get', 'severity'],
  'CRITICAL', SEVERITY_COLORS.CRITICAL,
  'RED',      SEVERITY_COLORS.RED,
  'AMBER',    SEVERITY_COLORS.AMBER,
  'GREEN',    SEVERITY_COLORS.GREEN,
  SEVERITY_COLORS.INSUFFICIENT_DATA,
];

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  const mapRef = useRef<React.ElementRef<typeof MapLibreModule.MapView> | null>(null);
  const cameraRef = useRef<React.ElementRef<typeof MapLibreModule.Camera> | null>(null);
  // Tracks current zoom from onRegionDidChange so +/- buttons stay accurate.
  const zoomRef = useRef(HOME_ZOOM);

  // Build GeoJSON with display-offset coordinates. Original lat/lng stored in
  // properties so the press handler passes accurate data to onMarkerPress.
  const geoJSON = useMemo(() => ({
    type: 'FeatureCollection' as const,
    features: markers.map((m) => {
      const offset = DISPLAY_OFFSETS[m.region];
      return {
        type: 'Feature' as const,
        id: m.id,
        geometry: {
          type: 'Point' as const,
          coordinates: [
            m.longitude + (offset?.lngOffset ?? 0),
            m.latitude  + (offset?.latOffset  ?? 0),
          ] as [number, number],
        },
        properties: {
          id: m.id,
          region: m.region,
          severity: m.severity,
          timestamp: m.timestamp ?? null,
          summary: m.summary ?? null,
          confidence: m.confidence ?? null,
          originalLatitude: m.latitude,
          originalLongitude: m.longitude,
        },
      };
    }),
  }), [markers]);

  const handleSourcePress = useCallback((event: any) => {
    const features: any[] = event?.nativeEvent?.features ?? [];
    const feature = features[0];
    if (!feature) return;
    const p = feature?.properties ?? {};
    onMarkerPress({
      id: p?.id,
      latitude: p?.originalLatitude,
      longitude: p?.originalLongitude,
      severity: p?.severity,
      region: p?.region,
      timestamp: p?.timestamp ?? undefined,
      summary: p?.summary ?? undefined,
      confidence: p?.confidence ?? undefined,
    });
  }, [onMarkerPress]);

  if (!_maplibre) {
    return <MapFallback />;
  }

  const { Map, Camera, GeoJSONSource, Layer } = _maplibre;

  const handleZoomIn = () => {
    const next = Math.min(zoomRef.current + 1, MAX_ZOOM);
    cameraRef.current?.zoomTo(next, 300);
    zoomRef.current = next;
  };

  const handleZoomOut = () => {
    const next = Math.max(zoomRef.current - 1, MIN_ZOOM);
    cameraRef.current?.zoomTo(next, 300);
    zoomRef.current = next;
  };

  const handleHome = () => {
    cameraRef.current?.fitBounds(
      [BOUNDS_MIN_LNG, BOUNDS_MIN_LAT, BOUNDS_MAX_LNG, BOUNDS_MAX_LAT],
      50,
      50,
      1000,
    );
    zoomRef.current = HOME_ZOOM;
  };

  return (
    <View style={styles.container}>
      <Map
        ref={mapRef}
        style={styles.map}
        mapStyle={TILE_STYLE}
        onRegionDidChange={(feature: any) => {
          const z = feature?.properties?.zoomLevel;
          if (typeof z === 'number') zoomRef.current = z;
        }}
      >
        <Camera
          ref={cameraRef}
          centerCoordinate={HOME_CENTER}
          zoomLevel={HOME_ZOOM}
          animationDuration={600}
          animationMode="flyTo"
        />

        <GeoJSONSource
          id="markers"
          data={geoJSON}
          onPress={handleSourcePress}
        >
          {/* Severity-colored circle for each watch zone */}
          <Layer
            id="marker-circle"
            type="circle"
            paint={{
              'circle-radius': 10,
              'circle-color': POINT_COLOR_EXPR,
              'circle-stroke-width': 2,
              'circle-stroke-color': '#fff',
            }}
          />
          {/* Region name label below each circle — white text, dark halo */}
          <Layer
            id="marker-label"
            type="symbol"
            layout={{
              'text-field': '{region}',
              'text-size': 12,
              'text-anchor': 'top',
              'text-offset': [0, 1.4] as any,
              'text-allow-overlap': true,
              'text-ignore-placement': true,
              'text-font': ['Open Sans Regular', 'Arial Unicode MS Regular'] as any,
            }}
            paint={{
              'text-color': '#ffffff',
              'text-halo-color': 'rgba(0,0,0,0.85)',
              'text-halo-width': 2,
            }}
          />
        </GeoJSONSource>
      </Map>

      {/* Zoom + home controls — top-right vertical stack */}
      <View style={styles.controlStack}>
        <TouchableOpacity style={styles.controlBtn} onPress={handleZoomIn} activeOpacity={0.7}>
          <Text style={styles.controlBtnText}>+</Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.controlBtn} onPress={handleZoomOut} activeOpacity={0.7}>
          <Text style={styles.controlBtnText}>−</Text>
        </TouchableOpacity>
        <TouchableOpacity style={[styles.controlBtn, styles.controlBtnHome]} onPress={handleHome} activeOpacity={0.7}>
          <Text style={styles.controlBtnText}>⌂</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  map: { flex: 1 },

  controlStack: {
    position: 'absolute',
    top: 12,
    right: 12,
    gap: 8,
  },
  controlBtn: {
    width: 40,
    height: 40,
    borderRadius: 8,
    backgroundColor: 'rgba(0,0,0,0.6)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  controlBtnHome: {
    marginTop: 4,
  },
  controlBtnText: {
    color: '#fff',
    fontSize: 20,
    fontWeight: '600',
    lineHeight: 24,
  },
});
