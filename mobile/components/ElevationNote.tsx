import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Props {
  note: string;
}

/**
 * Shown on AlertDetail when Gemma 4 raised severity above the
 * deterministic scorer's baseline (elevation note appended to summary).
 */
export function ElevationNote({ note }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.icon}>↑</Text>
      <Text style={styles.text}>{note}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: '#fdf4ff',
    borderWidth: 1,
    borderColor: '#e9d5ff',
    borderRadius: 8,
    padding: 10,
    marginTop: 10,
    gap: 8,
  },
  icon: {
    fontSize: 14,
    color: '#7c3aed',
    fontWeight: '700',
  },
  text: {
    flex: 1,
    fontSize: 13,
    color: '#5b21b6',
    lineHeight: 18,
  },
});
