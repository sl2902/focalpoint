import React, { useRef, useCallback, useMemo } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, LogBox } from 'react-native';

LogBox.ignoreLogs(['MapLibre Native [ERROR]']);
// Type-only import — erased at compile time, never triggers TurboModuleRegistry.
import type * as MapLibreModule from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
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

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  const cameraRef = useRef<any>(null);
  const sourceRef = useRef<any>(null);
  const zoomRef = useRef(3);

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
      const clusterId = properties.cluster_id;
      try {
        // Fetch expansion zoom and direct children in parallel.
        const [expansionZoom, children] = await Promise.all([
          sourceRef.current?.getClusterExpansionZoom(clusterId),
          sourceRef.current?.getClusterChildren(clusterId),
        ]);

        // Use expansion zoom directly — no fixed increment.
        const targetZoom = expansionZoom ?? Math.min(zoomRef.current + 3, MAX_ZOOM);
        cameraRef.current?.flyTo(coords, 400);
        cameraRef.current?.zoomTo(targetZoom, 400);
        zoomRef.current = targetZoom;

        // If exactly 1 individual point remains after expansion (others formed a
        // sub-cluster), auto-show its popup once the camera animation settles.
        const singles = (children ?? []).filter((c: any) => !c?.properties?.cluster);
        if (singles.length === 1) {
          const leaf = singles[0];
          const leafCoords: [number, number] = leaf?.geometry?.coordinates ?? coords;
          setTimeout(() => {
            onMarkerPress({
              id: leaf?.properties?.id,
              latitude: leafCoords[1],
              longitude: leafCoords[0],
              severity: leaf?.properties?.severity,
              region: leaf?.properties?.region,
              timestamp: leaf?.properties?.timestamp ?? undefined,
              summary: leaf?.properties?.summary ?? undefined,
              confidence: leaf?.properties?.confidence ?? undefined,
            });
          }, 450);
        }
      } catch {
        // getClusterExpansionZoom unavailable — aggressive fixed fallback.
        const targetZoom = Math.min(zoomRef.current + 3, MAX_ZOOM);
        cameraRef.current?.flyTo(coords, 400);
        cameraRef.current?.zoomTo(targetZoom, 400);
        zoomRef.current = targetZoom;
      }
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
    cameraRef.current?.zoomTo(next, 300);
    zoomRef.current = next;
  };

  const handleZoomOut = () => {
    const next = Math.max(zoomRef.current - 1, MIN_ZOOM);
    cameraRef.current?.zoomTo(next, 300);
    zoomRef.current = next;
  };

  const handleHome = () => {
    cameraRef.current?.flyTo([35.2, 31.9], 500);
    cameraRef.current?.zoomTo(3, 500);
    zoomRef.current = 3;
  };

  return (
    <View style={styles.container}>
      <Map
        style={styles.map}
        mapStyle={TILE_STYLE}
        onRegionDidChange={(feature: any) => {
          const z = feature?.properties?.zoomLevel;
          if (typeof z === 'number') zoomRef.current = z;
        }}
      >
        <Camera
          ref={cameraRef}
          defaultSettings={{ centerCoordinate: [35.2, 31.9], zoomLevel: 3 }}
        />

        <GeoJSONSource
          id="markers"
          ref={sourceRef}
          data={geoJSON}
          cluster={true}
          clusterRadius={30}
          clusterMaxZoom={6}
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
