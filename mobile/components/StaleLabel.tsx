import React from 'react';
import { Text, StyleSheet } from 'react-native';

export function StaleLabel() {
  return <Text style={styles.label}>STALE</Text>;
}

const styles = StyleSheet.create({
  label: {
    fontSize: 10,
    fontWeight: '700',
    color: '#92400e',
    backgroundColor: '#fef3c7',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 3,
    overflow: 'hidden',
  },
});
