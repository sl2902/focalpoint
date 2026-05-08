import React, { useRef, useCallback, useMemo, useEffect, useState } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, LogBox } from 'react-native';

LogBox.ignoreLogs(['MapLibre Native [ERROR]']);
// Type-only import — erased at compile time, never triggers TurboModuleRegistry.
import type * as MapLibreModule from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import { WATCH_ZONE_COORDS } from '../constants/watchZones';
import { MapFallback } from './MapFallback';
import type { ComponentMarker } from '../types/map';
import type { Severity } from '../types/api';

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

// Bounding box computed from all 9 watch zone centres: [west, south, east, north]
const _coords = Object.values(WATCH_ZONE_COORDS);
const ALL_ZONES_BOUNDS: [number, number, number, number] = [
  Math.min(..._coords.map((c) => c.longitude)), // west
  Math.min(..._coords.map((c) => c.latitude)),  // south
  Math.max(..._coords.map((c) => c.longitude)), // east
  Math.max(..._coords.map((c) => c.latitude)),  // north
];
const ALL_ZONES_PADDING = { top: 50, bottom: 50, left: 30, right: 30 };

// Midpoint of all watch zones — used as the Camera's initial centerCoordinate.
const HOME_CENTER: [number, number] = [
  (ALL_ZONES_BOUNDS[0] + ALL_ZONES_BOUNDS[2]) / 2,
  (ALL_ZONES_BOUNDS[1] + ALL_ZONES_BOUNDS[3]) / 2,
];

// Integer rank used for cluster severity aggregation (max wins).
const SEVERITY_TO_ORDER: Record<Severity, number> = {
  CRITICAL: 3,
  RED: 2,
  AMBER: 1,
  GREEN: 0,
  INSUFFICIENT_DATA: -1,
};

// MapLibre match expression: cluster circle color ← highest severity order in cluster.
const CLUSTER_COLOR_EXPR: any = [
  'match', ['get', 'maxSeverityOrder'],
  3, SEVERITY_COLORS.CRITICAL,
  2, SEVERITY_COLORS.RED,
  1, SEVERITY_COLORS.AMBER,
  0, SEVERITY_COLORS.GREEN,
  SEVERITY_COLORS.INSUFFICIENT_DATA,
];

// MapLibre match expression: individual point color ← severity string property.
const POINT_COLOR_EXPR: any = [
  'match', ['get', 'severity'],
  'CRITICAL', SEVERITY_COLORS.CRITICAL,
  'RED',      SEVERITY_COLORS.RED,
  'AMBER',    SEVERITY_COLORS.AMBER,
  'GREEN',    SEVERITY_COLORS.GREEN,
  SEVERITY_COLORS.INSUFFICIENT_DATA,
];

// clusterProperties: propagate the highest severity order to each cluster node.
// map_expression: pull maxSeverityOrder from each leaf point.
// reduce_expression: accumulate via max so the cluster reflects its worst member.
const CLUSTER_PROPERTIES: any = {
  maxSeverityOrder: [
    ['max', ['accumulated'], ['get', 'maxSeverityOrder']],
    ['get', 'maxSeverityOrder'],
  ],
};

// Step expression: cluster radius grows with point count.
const CLUSTER_RADIUS_EXPR: any = ['step', ['get', 'point_count'], 20, 5, 28, 10, 36];

interface CameraTarget {
  centerCoordinate: [number, number];
  zoomLevel: number;
  animationDuration: number;
}

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  // MapView ref — exposes getBounds, getZoom, queryRenderedFeatures etc.
  // Does NOT expose setCamera in MapLibre RN 11.x.
  const mapRef = useRef<React.ElementRef<typeof MapLibreModule.MapView> | null>(null);
  // Camera ref — populated after the native map loads, used for diagnostics only.
  // Navigation is driven by cameraState props, not ref calls.
  const cameraRef = useRef<React.ElementRef<typeof MapLibreModule.Camera> | null>(null);
  const sourceRef = useRef<React.ElementRef<typeof MapLibreModule.GeoJSONSource> | null>(null);
  // Tracks current zoom from onRegionDidChange so zoom-in/out deltas are correct.
  const zoomRef = useRef(HOME_ZOOM);

  // State-driven camera: updating this triggers Camera to animate to the new position.
  const [cameraState, setCameraState] = useState<CameraTarget>({
    centerCoordinate: HOME_CENTER,
    zoomLevel: HOME_ZOOM,
    animationDuration: 0,
  });

  useEffect(() => {
    console.log('[map] cameraRef on mount:', cameraRef.current);
  }, []);

  // Build GeoJSON FeatureCollection from live marker data.
  // maxSeverityOrder duplicates the severity rank so clusterProperties can
  // reference it by name in both the map and reduce expressions.
  const geoJSON = useMemo(() => ({
    type: 'FeatureCollection' as const,
    features: markers.map((m) => ({
      type: 'Feature' as const,
      id: m.id,
      geometry: {
        type: 'Point' as const,
        coordinates: [m.longitude, m.latitude] as [number, number],
      },
      properties: {
        id: m.id,
        region: m.region,
        severity: m.severity,
        timestamp: m.timestamp ?? null,
        summary: m.summary ?? null,
        confidence: m.confidence ?? null,
        maxSeverityOrder: SEVERITY_TO_ORDER[m.severity] ?? -1,
      },
    })),
  }), [markers]);

  // Unified press handler for both clustered and individual features.
  // onPress fires as NativeSyntheticEvent<PressEventWithFeatures> — features at nativeEvent.
  const handleSourcePress = useCallback(async (event: any) => {
    const features: any[] = event?.nativeEvent?.features ?? [];
    const feature = features[0];
    if (!feature) return;

    const properties = feature?.properties ?? {};
    const coords: [number, number] = feature?.geometry?.coordinates ?? [0, 0];

    if (properties?.cluster) {
      // Read cluster_id directly from the tap-time event — never from a cached ref.
      const clusterId = event?.nativeEvent?.features?.[0]?.properties?.cluster_id;
      try {
        const expansionZoom = await sourceRef.current?.getClusterExpansionZoom(clusterId);
        const currentZoom = zoomRef.current;
        const targetZoom = Math.min(
          Math.max((expansionZoom ?? currentZoom + 3) + 1, currentZoom + 2),
          MAX_ZOOM,
        );
        console.log(
          `[map] cluster tap cluster_id=${clusterId} expansionZoom=${expansionZoom} currentZoom=${currentZoom} targetZoom=${targetZoom}`,
        );
        setCameraState({ centerCoordinate: coords, zoomLevel: targetZoom, animationDuration: 400 });
        zoomRef.current = targetZoom;
      } catch (err) {
        console.log(`[map] getClusterExpansionZoom failed cluster_id=${clusterId}:`, err);
        const targetZoom = Math.min(zoomRef.current + 3, MAX_ZOOM);
        setCameraState({ centerCoordinate: coords, zoomLevel: targetZoom, animationDuration: 400 });
        zoomRef.current = targetZoom;
      }
      // Cluster tap: expand only — never show popup or navigate to Alert Detail.
    } else {
      // Individual marker tap — fires handleMarkerPress in map.tsx which shows
      // the region popup; "View Details" on the popup navigates to Alert Detail.
      onMarkerPress({
        id: properties?.id,
        latitude: coords[1],
        longitude: coords[0],
        severity: properties?.severity,
        region: properties?.region,
        timestamp: properties?.timestamp ?? undefined,
        summary: properties?.summary ?? undefined,
        confidence: properties?.confidence ?? undefined,
      });
    }
  }, [onMarkerPress]);

  if (!_maplibre) {
    return <MapFallback />;
  }

  const { Map, Camera, GeoJSONSource, Layer } = _maplibre;

  const handleZoomIn = () => {
    const next = Math.min(zoomRef.current + 1, MAX_ZOOM);
    setCameraState(prev => ({ ...prev, zoomLevel: next, animationDuration: 300 }));
    zoomRef.current = next;
  };

  const handleZoomOut = () => {
    const next = Math.max(zoomRef.current - 1, MIN_ZOOM);
    setCameraState(prev => ({ ...prev, zoomLevel: next, animationDuration: 300 }));
    zoomRef.current = next;
  };

  const handleHome = () => {
    setCameraState({ centerCoordinate: HOME_CENTER, zoomLevel: HOME_ZOOM, animationDuration: 500 });
    zoomRef.current = HOME_ZOOM;
  };

  return (
    <View style={styles.container}>
      <Map
        ref={mapRef}
        style={styles.map}
        mapStyle={TILE_STYLE}
        onDidFinishLoadingMap={() => {
          console.log('[map] cameraRef after map load:', cameraRef.current);
        }}
        onRegionDidChange={(feature: any) => {
          const z = feature?.properties?.zoomLevel;
          if (typeof z === 'number') zoomRef.current = z;
        }}
      >
        <Camera
          ref={cameraRef}
          centerCoordinate={cameraState.centerCoordinate}
          zoomLevel={cameraState.zoomLevel}
          animationDuration={cameraState.animationDuration}
          animationMode="flyTo"
        />

        <GeoJSONSource
          id="markers"
          ref={sourceRef}
          data={geoJSON}
          cluster={true}
          clusterRadius={30}
          clusterMaxZoom={8}
          clusterProperties={CLUSTER_PROPERTIES}
          onPress={handleSourcePress}
        >
          {/* Cluster background circle — color reflects highest severity */}
          <Layer
            id="clusters"
            type="circle"
            filter={['has', 'point_count'] as any}
            paint={{
              'circle-radius': CLUSTER_RADIUS_EXPR,
              'circle-color': CLUSTER_COLOR_EXPR,
              'circle-opacity': 0.9,
              'circle-stroke-width': 2,
              'circle-stroke-color': '#fff',
            }}
          />

          {/* Cluster count label */}
          <Layer
            id="cluster-count"
            type="symbol"
            filter={['has', 'point_count'] as any}
            layout={{
              'text-field': (['get', 'point_count'] as any),
              'text-size': 13,
              'text-allow-overlap': true,
            }}
            paint={{ 'text-color': '#fff' }}
          />

          {/* Individual unclustered point — color reflects its own severity */}
          <Layer
            id="unclustered-point"
            type="circle"
            filter={(['!', ['has', 'point_count']] as any)}
            paint={{
              'circle-radius': 8,
              'circle-color': POINT_COLOR_EXPR,
              'circle-stroke-width': 2,
              'circle-stroke-color': '#fff',
            }}
          />
        </GeoJSONSource>
      </Map>

      {/* Zoom + home + fit-all controls — top-right vertical stack */}
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
        <TouchableOpacity style={[styles.controlBtn, styles.controlBtnFitAll]} onPress={handleHome} activeOpacity={0.7}>
          <Text style={styles.controlBtnFitAllText}>All</Text>
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
  controlBtnFitAll: {
    marginTop: 4,
  },
  controlBtnFitAllText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '700',
    lineHeight: 16,
  },
  controlBtnText: {
    color: '#fff',
    fontSize: 20,
    fontWeight: '600',
    lineHeight: 24,
  },
});
