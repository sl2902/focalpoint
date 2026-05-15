import React, { useRef, useEffect, useState } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, LogBox, Animated } from 'react-native';

LogBox.ignoreLogs(['MapLibre Native [ERROR]']);
// Type-only import — erased at compile time, never triggers TurboModuleRegistry.
import type * as MapLibreModule from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import { WATCH_ZONE_COORDS } from '../constants/watchZones';
import { MapFallback } from './MapFallback';
import type { ComponentMarker } from '../types/map';

const TILE_STYLE_URL = 'https://demotiles.maplibre.org/style.json';

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

// Visual offsets so Gaza / Palestine / Israel pulse rings don't fully overlap.
// Gaza stays at exact coordinates; Palestine shifts north; Israel shifts east.
const DISPLAY_OFFSETS: Record<string, { latOffset: number; lngOffset: number }> = {
  Palestine: { latOffset: 0.3, lngOffset: 0.0 },
  Israel:    { latOffset: 0.0, lngOffset: 0.2 },
};

// Pure presentational component — animation is managed by the parent and passed
// in as an already-running Animated.Value so each marker animates independently.
function MarkerAnnotation({
  marker,
  anim,
  onPress,
  ViewAnnotation,
}: {
  marker: ComponentMarker;
  anim: Animated.Value;
  onPress: (m: ComponentMarker) => void;
  ViewAnnotation: any;
}) {
  const isCritical = marker.severity === 'CRITICAL';
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
  const zoomRef = useRef(HOME_ZOOM);

  // Persistent map of region → Animated.Value so each marker has its own
  // independent animation. Values are created once and never replaced so
  // interpolations derived from them stay valid across re-renders.
  const animMap = useRef<Record<string, Animated.Value>>({});
  markers.forEach((m) => {
    if (!animMap.current[m.region]) {
      animMap.current[m.region] = new Animated.Value(0);
    }
  });

  const [tileStyle, setTileStyle] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    fetch(TILE_STYLE_URL)
      .then((r) => r.json())
      .then((style) => {
        const modified = style.layers.map((layer: any) => {
          if (
            layer.type === 'symbol' &&
            (layer.id.includes('label') || layer.id.includes('name'))
          ) {
            return {
              ...layer,
              paint: {
                ...layer.paint,
                'text-color': '#0a0a0a',
                'text-halo-color': 'rgba(255,255,255,0.95)',
                'text-halo-width': 2.5,
                'text-halo-blur': 1,
              },
            };
          }
          return layer;
        });
        setTileStyle({ ...style, layers: modified });
      })
      .catch(() => { /* keep URL fallback on network error */ });
  }, []);

  // Start a pulse loop for every CRITICAL marker in a single effect.
  // Stops all loops on cleanup so animations don't leak across severity changes.
  useEffect(() => {
    const criticals = markers.filter((m) => m.severity === 'CRITICAL');
    console.log(`[map] starting pulse for ${criticals.length} CRITICAL markers`);
    const loops = criticals.map((m) => {
      const anim = animMap.current[m.region];
      anim.setValue(0);
      return Animated.loop(
        Animated.timing(anim, { toValue: 1, duration: 1500, useNativeDriver: true }),
      );
    });
    loops.forEach((l) => l.start());
    return () => loops.forEach((l) => l.stop());
  }, [markers]); // eslint-disable-line react-hooks/exhaustive-deps

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
      {tileStyle && <Map
        ref={mapRef}
        style={styles.map}
        mapStyle={tileStyle}
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
            anim={animMap.current[m.region]}
            onPress={onMarkerPress}
            ViewAnnotation={ViewAnnotation}
          />
        ))}
      </Map>}

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
