import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import type { Severity } from '../types/api';

interface Props {
  severity: Severity;
  size?: 'sm' | 'md';
}

const LABELS: Record<Severity, string> = {
  GREEN:             'GREEN',
  AMBER:             'AMBER',
  RED:               'RED',
  CRITICAL:          'CRITICAL',
  INSUFFICIENT_DATA: 'INSUFFICIENT DATA',
};

export function SeverityBadge({ severity, size = 'md' }: Props) {
  const color = SEVERITY_COLORS[severity];
  const small = size === 'sm';

  return (
    <View style={[styles.badge, { borderColor: color, backgroundColor: color + '22' }]}>
      <Text style={[styles.label, { color, fontSize: small ? 10 : 12 }]}>
        {LABELS[severity]}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    borderWidth: 1,
    borderRadius: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: 'flex-start',
  },
  label: {
    fontWeight: '700',
    letterSpacing: 0.5,
  },
});
