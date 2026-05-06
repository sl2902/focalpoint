import React, { useRef, useEffect } from 'react';
import { TouchableOpacity, View, Text, StyleSheet, Animated } from 'react-native';
// Type-only import — erased at compile time, never triggers TurboModuleRegistry.
import type * as MapLibreModule from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
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

function PulsingMarker({ color }: { color: string }) {
  const scale = useRef(new Animated.Value(1)).current;
  const opacity = useRef(new Animated.Value(0.7)).current;

  useEffect(() => {
    const pulse = Animated.loop(
      Animated.sequence([
        Animated.parallel([
          Animated.timing(scale, { toValue: 2.2, duration: 1000, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0, duration: 1000, useNativeDriver: true }),
        ]),
        Animated.parallel([
          Animated.timing(scale, { toValue: 1, duration: 0, useNativeDriver: true }),
          Animated.timing(opacity, { toValue: 0.7, duration: 0, useNativeDriver: true }),
        ]),
      ]),
    );
    pulse.start();
    return () => pulse.stop();
  }, [scale, opacity]);

  return (
    <View style={styles.dotWrapper}>
      <Animated.View
        style={[
          styles.pulseRing,
          { backgroundColor: color, transform: [{ scale }], opacity },
        ]}
      />
      <View style={[styles.dot, { backgroundColor: color }]} />
    </View>
  );
}

// Bounding box that fits all 9 watch zones: NE=[maxLng,maxLat], SW=[minLng,minLat]
const ALL_ZONES_NE: [number, number] = [96.1, 48.4]; // Myanmar lng, Ukraine lat
const ALL_ZONES_SW: [number, number] = [31.2, 15.5]; // Ukraine lng, Yemen lat
const MIN_ZOOM = 1;
const MAX_ZOOM = 18;

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  const cameraRef = useRef<any>(null);
  const zoomRef = useRef(3);

  if (!_maplibre) {
    return <MapFallback />;
  }

  const { Map, Camera, Marker } = _maplibre;

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
    cameraRef.current?.fitBounds(ALL_ZONES_NE, ALL_ZONES_SW, 40, 500);
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
          defaultSettings={{
            centerCoordinate: [35.2, 31.9],
            zoomLevel: 3,
          }}
        />
        {markers.map((marker) => {
          const color = SEVERITY_COLORS[marker.severity];
          const shouldPulse = marker.severity === 'CRITICAL';
          return (
            <Marker
              key={marker.id}
              id={marker.id}
              lngLat={[marker.longitude, marker.latitude]}
            >
              <TouchableOpacity onPress={() => onMarkerPress(marker)}>
                {shouldPulse ? (
                  <PulsingMarker color={color} />
                ) : (
                  <View style={[styles.dot, { backgroundColor: color }]} />
                )}
              </TouchableOpacity>
            </Marker>
          );
        })}
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

  // Vertical stack of controls — top-right
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

  dotWrapper: {
    width: 26,
    height: 26,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: '#fff',
  },
  pulseRing: {
    position: 'absolute',
    width: 14,
    height: 14,
    borderRadius: 7,
  },
});
