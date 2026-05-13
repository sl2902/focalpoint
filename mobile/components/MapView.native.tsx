import React, { useRef, useEffect } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, LogBox, Animated } from 'react-native';

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

// Each marker is a ViewAnnotation (React Native view) so both Animated-driven
// CRITICAL pulse rings and static non-CRITICAL circles use the same component.
// Defined outside the main component so its identity is stable across re-renders.
function MarkerAnnotation({
  marker,
  onPress,
  ViewAnnotation,
}: {
  marker: ComponentMarker;
  onPress: (m: ComponentMarker) => void;
  ViewAnnotation: any;
}) {
  const anim = useRef(new Animated.Value(0)).current;
  const isCritical = marker.severity === 'CRITICAL';

  useEffect(() => {
    if (!isCritical) return;
    const loop = Animated.loop(
      Animated.timing(anim, { toValue: 1, duration: 1500, useNativeDriver: true }),
    );
    loop.start();
    return () => loop.stop();
  }, [anim, isCritical]);

  const scale = anim.interpolate({ inputRange: [0, 1], outputRange: [1, 1.8] });
  const ringOpacity = anim.interpolate({ inputRange: [0, 1], outputRange: [0.6, 0] });

  const offset = DISPLAY_OFFSETS[marker.region];
  const lngLat: [number, number] = [
    marker.longitude + (offset?.lngOffset ?? 0),
    marker.latitude + (offset?.latOffset ?? 0),
  ];
  const color = (SEVERITY_COLORS as Record<string, string>)[marker.severity]
    ?? SEVERITY_COLORS.INSUFFICIENT_DATA;

  return (
    <ViewAnnotation id={marker.id} lngLat={lngLat} onPress={() => onPress(marker)}>
      <View style={styles.markerWrapper}>
        {isCritical && (
          <Animated.View
            style={[
              styles.pulseRing,
              { backgroundColor: color, transform: [{ scale }], opacity: ringOpacity },
            ]}
          />
        )}
        <View style={[styles.markerDot, { backgroundColor: color }]} />
        <Text style={styles.markerLabel}>{marker.region}</Text>
      </View>
    </ViewAnnotation>
  );
}

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  const mapRef = useRef<React.ElementRef<typeof MapLibreModule.MapView> | null>(null);
  const cameraRef = useRef<React.ElementRef<typeof MapLibreModule.Camera> | null>(null);
  // Tracks current zoom from onRegionDidChange so +/- buttons stay accurate.
  const zoomRef = useRef(HOME_ZOOM);

  if (!_maplibre) {
    return <MapFallback />;
  }

  const { Map, Camera, ViewAnnotation } = _maplibre as any;

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

        {markers.map((m) => (
          <MarkerAnnotation
            key={m.id}
            marker={m}
            onPress={onMarkerPress}
            ViewAnnotation={ViewAnnotation}
          />
        ))}
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

  markerWrapper: {
    alignItems: 'center',
  },
  pulseRing: {
    position: 'absolute',
    width: 24,
    height: 24,
    borderRadius: 12,
  },
  markerDot: {
    width: 20,
    height: 20,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: '#fff',
  },
  markerLabel: {
    marginTop: 3,
    fontSize: 11,
    fontWeight: '600',
    color: '#fff',
    textShadowColor: 'rgba(0,0,0,0.85)',
    textShadowOffset: { width: 0, height: 0 },
    textShadowRadius: 3,
  },

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
