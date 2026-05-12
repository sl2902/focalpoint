import React from 'react';
import { View, Text, Pressable, ActivityIndicator, StyleSheet } from 'react-native';
import { SeverityBadge } from './SeverityBadge';
import { SEVERITY_BG_COLORS } from '../constants/severity';
import { useRefreshStore } from '../store/useRefreshStore';
import type { AlertResponse } from '../types/api';

const ANNOTATION_PATTERN = /\s*\[(Elevation note:|Note:|Historical risk floor applied)[^\]]*\]/gi;

interface Props {
  alert: AlertResponse;
  onPress?: () => void;
}

export function AlertCard({ alert, onPress }: Props) {
  const isLoading = useRefreshStore((s) => s.loadingRegions.has(alert.region));
  console.log('[feed] card render, region=', alert.region, 'isLoading=', isLoading);

  const bg = SEVERITY_BG_COLORS[alert.severity];
  const ts = new Date(alert.timestamp);
  const dateLabel = ts.toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const timeLabel = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
  const summary = alert.summary.replace(ANNOTATION_PATTERN, '').trim();

  return (
    <Pressable
      onPress={isLoading ? undefined : onPress}
      style={({ pressed }) => [styles.card, { backgroundColor: bg, opacity: isLoading || pressed ? 0.75 : 1 }]}
    >
      <View style={styles.header}>
        <SeverityBadge severity={alert.severity} size="sm" />
        <Text style={styles.region}>{alert.region}</Text>
        {isLoading ? (
          <ActivityIndicator size="small" color="#6b7280" />
        ) : (
          <Text style={styles.time}>{dateLabel} · {timeLabel} UTC</Text>
        )}
      </View>
      <Text style={styles.summary} numberOfLines={3}>
        {isLoading ? 'Refreshing assessment…' : summary}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 10,
    padding: 14,
    marginHorizontal: 16,
    marginVertical: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.08,
    shadowRadius: 3,
    elevation: 2,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 8,
  },
  region: {
    flex: 1,
    fontWeight: '600',
    fontSize: 14,
    color: '#111827',
  },
  time: {
    fontSize: 12,
    color: '#6b7280',
  },
  summary: {
    fontSize: 14,
    color: '#374151',
    lineHeight: 20,
  },
});
