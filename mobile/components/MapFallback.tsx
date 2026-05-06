import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

export function MapFallback() {
  return (
    <View style={styles.container}>
      <Text style={styles.icon}>🗺️</Text>
      <Text style={styles.title}>Map unavailable</Text>
      <Text style={styles.body}>
        The map could not be loaded on this device.{'\n'}
        Conflict data is still available in the Feed tab.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#111827',
    padding: 32,
  },
  icon: { fontSize: 48, marginBottom: 16 },
  title: {
    fontSize: 18,
    fontWeight: '700',
    color: '#f9fafb',
    marginBottom: 8,
    textAlign: 'center',
  },
  body: {
    fontSize: 14,
    color: '#9ca3af',
    textAlign: 'center',
    lineHeight: 21,
  },
});
