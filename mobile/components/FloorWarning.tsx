import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Props {
  reason: string;
}

/**
 * Shown on AlertDetail when SeverityResult.floor_applied is true.
 * Indicates severity was elevated to AMBER floor due to historical
 * CPJ/RSF data even though no live events were found.
 */
export function FloorWarning({ reason }: Props) {
  return (
    <View style={styles.container}>
      <Text style={styles.icon}>⚠</Text>
      <Text style={styles.text}>
        Severity floor applied: {reason}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    backgroundColor: '#fffbeb',
    borderWidth: 1,
    borderColor: '#fde68a',
    borderRadius: 8,
    padding: 10,
    marginTop: 10,
    gap: 8,
  },
  icon: {
    fontSize: 14,
    color: '#d97706',
  },
  text: {
    flex: 1,
    fontSize: 13,
    color: '#92400e',
    lineHeight: 18,
  },
});
