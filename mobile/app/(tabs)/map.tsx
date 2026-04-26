import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import MapView from '../../components/MapView';
import type { ComponentMarker } from '../../types/map';

// Placeholder until fetchMarkers() is wired to the feed
const PLACEHOLDER_MARKERS: ComponentMarker[] = [];

export default function MapScreen() {
  const router = useRouter();

  const handleMarkerPress = (marker: ComponentMarker) => {
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
      <MapView markers={PLACEHOLDER_MARKERS} onMarkerPress={handleMarkerPress} />
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
});
