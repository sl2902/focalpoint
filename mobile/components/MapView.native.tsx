import React from 'react';
import { Pressable, View, StyleSheet } from 'react-native';
import { Map, Camera, Marker } from '@maplibre/maplibre-react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import type { ComponentMarker } from '../types/map';

const TILE_STYLE = 'https://demotiles.maplibre.org/style.json';

interface Props {
  markers: ComponentMarker[];
  onMarkerPress: (marker: ComponentMarker) => void;
}

export default function MapViewNative({ markers, onMarkerPress }: Props) {
  return (
    <Map style={styles.map} mapStyle={TILE_STYLE}>
      <Camera
        initialViewState={{
          center: [35.2, 31.9],
          zoom: 3,
        }}
      />
      {markers.map((marker) => (
        <Marker
          key={marker.id}
          id={marker.id}
          lngLat={[marker.longitude, marker.latitude]}
        >
          <Pressable onPress={() => onMarkerPress(marker)}>
            <View
              style={[
                styles.dot,
                { backgroundColor: SEVERITY_COLORS[marker.severity] },
              ]}
            />
          </Pressable>
        </Marker>
      ))}
    </Map>
  );
}

const styles = StyleSheet.create({
  map: { flex: 1 },
  dot: {
    width: 14,
    height: 14,
    borderRadius: 7,
    borderWidth: 2,
    borderColor: '#fff',
  },
});
