import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Props {
  confidence: number; // 0.0–1.0
}

export function ConfidenceBar({ confidence }: Props) {
  const pct = Math.round(confidence * 100);
  const color = pct >= 90 ? '#22c55e' : pct >= 70 ? '#f59e0b' : '#ef4444';

  return (
    <View style={styles.container}>
      <Text style={styles.label}>Data confidence</Text>
      <View style={styles.track}>
        <View style={[styles.fill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
      <Text style={[styles.pct, { color }]}>{pct}%</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginTop: 8,
  },
  label: {
    fontSize: 12,
    color: '#6b7280',
    width: 100,
  },
  track: {
    flex: 1,
    height: 6,
    backgroundColor: '#e5e7eb',
    borderRadius: 3,
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    borderRadius: 3,
  },
  pct: {
    fontSize: 12,
    fontWeight: '700',
    width: 36,
    textAlign: 'right',
  },
});
