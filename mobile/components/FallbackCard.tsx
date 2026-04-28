import React from 'react';
import { View, Text, Pressable, ActivityIndicator, StyleSheet } from 'react-native';
import type { AlertResponse } from '../types/api';

interface Props {
  alert: AlertResponse;
  onPress: () => void;
  loading?: boolean;  // true while the background force-refresh is in-flight
}

export function FallbackCard({ alert, onPress, loading = false }: Props) {
  const ts = new Date(alert.timestamp).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  });

  return (
    <Pressable
      style={({ pressed }) => [
        styles.card,
        loading && styles.cardLoading,
        !loading && pressed && styles.cardPressed,
      ]}
      onPress={loading ? undefined : onPress}
      disabled={loading}
      accessibilityRole="button"
      accessibilityLabel={
        loading
          ? `${alert.region} — refreshing assessment`
          : `${alert.region} — assessment unavailable, tap to retry`
      }
    >
      <View style={styles.iconBadge}>
        {loading ? (
          <ActivityIndicator size="small" color="#9ca3af" />
        ) : (
          <Text style={styles.iconText}>!</Text>
        )}
      </View>
      <View style={styles.content}>
        <Text style={styles.region}>{alert.region}</Text>
        <Text style={styles.message}>
          {loading ? 'Refreshing assessment…' : 'Last assessment failed · Tap to retry'}
        </Text>
        <Text style={styles.timestamp}>{ts} UTC</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 16,
    marginVertical: 6,
    backgroundColor: '#f3f4f6',
    borderRadius: 10,
    padding: 14,
    borderWidth: 1,
    borderColor: '#d1d5db',
  },
  cardPressed: { opacity: 0.75 },
  cardLoading: { opacity: 0.6 },
  iconBadge: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: '#e5e7eb',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 12,
  },
  iconText: {
    fontSize: 16,
    fontWeight: '700',
    color: '#9ca3af',
  },
  content: {
    flex: 1,
  },
  region: {
    fontSize: 15,
    fontWeight: '600',
    color: '#6b7280',
    marginBottom: 2,
  },
  message: {
    fontSize: 13,
    color: '#9ca3af',
    marginBottom: 2,
  },
  timestamp: {
    fontSize: 11,
    color: '#9ca3af',
  },
});
