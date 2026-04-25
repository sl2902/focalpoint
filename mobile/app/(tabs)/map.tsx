/**
 * Map screen — MapLibre map with severity-coloured incident markers.
 *
 * Features:
 * - OpenStreetMap demo tiles (no API key required)
 * - Markers for all 9 watch zones
 * - Tap marker → AlertDetail modal
 * - Tile URL: https://demotiles.maplibre.org/style.json
 *
 * Uses @maplibre/maplibre-react-native v11 named exports:
 *   Map, Camera, Marker  (NOT MapView / PointAnnotation / setAccessToken)
 */

import React, { useState } from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Map, Camera, Marker } from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../../constants/severity';
import type { Severity } from '../../types/api';

const TILE_STYLE = 'https://demotiles.maplibre.org/style.json';

// Placeholder markers — replaced with live fetchMarkers() during screen implementation
const PLACEHOLDER_MARKERS: Array<{
  id: string;
  latitude: number;
  longitude: number;
  severity: Severity;
  region: string;
}> = [];

export default function MapScreen() {
  const router = useRouter();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const handleMarkerPress = (marker: (typeof PLACEHOLDER_MARKERS)[number]) => {
    router.push({
      pathname: '/alert/[id]',
      params: { id: marker.region, region: marker.region },
    });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Incident Map</Text>
      </View>
      <Map style={styles.map} mapStyle={TILE_STYLE}>
        <Camera
          initialViewState={{
            center: [35.2, 31.9],
            zoom: 3,
          }}
        />

        {PLACEHOLDER_MARKERS.map((marker) => (
          <Marker
            key={marker.id}
            id={marker.id}
            lngLat={[marker.longitude, marker.latitude]}
          >
            <Pressable onPress={() => handleMarkerPress(marker)}>
              <View
                style={[
                  styles.marker,
                  { backgroundColor: SEVERITY_COLORS[marker.severity] },
                ]}
              />
            </Pressable>
          </Marker>
        ))}
      </Map>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  header: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e5e7eb',
  },
  title: { fontSize: 18, fontWeight: '700', color: '#111827' },
  map: { flex: 1 },
  marker: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: '#fff',
  },
});
