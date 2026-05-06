import React, { useRef, useEffect } from 'react';
import { Pressable, View, StyleSheet, Animated } from 'react-native';
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

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  if (!_maplibre) {
    return <MapFallback />;
  }

  const { Map, Camera, Marker } = _maplibre;

  return (
    <Map style={styles.map} mapStyle={TILE_STYLE}>
      <Camera
        initialViewState={{
          center: [35.2, 31.9],
          zoom: 3,
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
            <Pressable onPress={() => onMarkerPress(marker)}>
              {shouldPulse ? (
                <PulsingMarker color={color} />
              ) : (
                <View style={[styles.dot, { backgroundColor: color }]} />
              )}
            </Pressable>
          </Marker>
        );
      })}
    </Map>
  );
}

const styles = StyleSheet.create({
  map: { flex: 1 },
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
