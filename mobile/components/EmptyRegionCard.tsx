import React from 'react';
import {
  View,
  Text,
  Pressable,
  ActivityIndicator,
  StyleSheet,
} from 'react-native';
import { useRefreshStore } from '../store/useRefreshStore';

interface Props {
  region: string;
  days: number;
  onLoad: () => void;
}

export function EmptyRegionCard({ region, days, onLoad }: Props) {
  const loading = useRefreshStore((s) => s.loadingRegions.has(region));
  const anyLoading = useRefreshStore((s) => s.loadingRegions.size > 0);
  console.log('[feed] card render, region=', region, 'isLoading=', loading);
  const isBlocked = anyLoading;
  return (
    <View style={styles.card}>
      <View style={styles.left}>
        <Text style={styles.region}>{region}</Text>
        <Text style={styles.sub}>No {days}d data cached</Text>
      </View>
      <Pressable
        style={[styles.btn, isBlocked && styles.btnDisabled]}
        onPress={onLoad}
        disabled={isBlocked}
        accessibilityLabel={`Load ${days}-day alert for ${region}`}
      >
        {loading ? (
          <ActivityIndicator size="small" color="#fff" />
        ) : (
          <Text style={styles.btnText}>Load</Text>
        )}
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    marginHorizontal: 16,
    marginVertical: 6,
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    shadowColor: '#000',
    shadowOpacity: 0.04,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  left: {
    flex: 1,
  },
  region: {
    fontSize: 15,
    fontWeight: '600',
    color: '#374151',
    marginBottom: 3,
  },
  sub: {
    fontSize: 12,
    color: '#9ca3af',
  },
  btn: {
    backgroundColor: '#1d4ed8',
    borderRadius: 8,
    paddingVertical: 8,
    paddingHorizontal: 18,
    minWidth: 64,
    alignItems: 'center',
  },
  btnDisabled: {
    backgroundColor: '#93c5fd',
  },
  btnText: {
    color: '#fff',
    fontSize: 13,
    fontWeight: '600',
  },
});
