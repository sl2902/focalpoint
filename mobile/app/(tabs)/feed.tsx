/**
 * Feed screen — proactive severity-graded alert stream.
 *
 * Features:
 * - FlatList of AlertCard components for all 9 watch zones
 * - Pull-to-refresh
 * - CachedBanner when offline or data is stale
 * - Tap card → AlertDetail modal (passes region + timestamp, not full JSON)
 */

import React from 'react';
import {
  FlatList,
  RefreshControl,
  View,
  Text,
  StyleSheet,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AlertCard } from '../../components/AlertCard';
import { CachedBanner } from '../../components/CachedBanner';
import { useAlerts } from '../../hooks/useAlerts';
import type { AlertResponse } from '../../types/api';

export default function FeedScreen() {
  const router = useRouter();
  const { alerts, loading, error, stale, lastFetchedAt, refresh } = useAlerts();

  const handlePress = (alert: AlertResponse) => {
    router.push({
      pathname: '/alert/[id]',
      params: { id: alert.region, region: alert.region, timestamp: alert.timestamp },
    });
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>FocalPoint</Text>
        <Text style={styles.subtitle}>Conflict Intelligence Feed</Text>
      </View>

      {stale && <CachedBanner lastFetchedAt={lastFetchedAt} />}

      {error && (
        <View style={styles.errorBanner}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <FlatList
        data={alerts}
        keyExtractor={(item, i) => `${item.region}-${item.timestamp}-${i}`}
        renderItem={({ item }) => (
          <AlertCard alert={item} onPress={() => handlePress(item)} />
        )}
        refreshControl={
          <RefreshControl refreshing={loading} onRefresh={refresh} tintColor="#2563eb" />
        }
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          !loading ? (
            <View style={styles.empty}>
              <Text style={styles.emptyText}>No alerts available.</Text>
            </View>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  header: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 12,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e5e7eb',
  },
  title: { fontSize: 22, fontWeight: '700', color: '#111827' },
  subtitle: { fontSize: 13, color: '#6b7280', marginTop: 2 },
  errorBanner: {
    backgroundColor: '#fef2f2',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#fecaca',
  },
  errorText: { fontSize: 13, color: '#dc2626' },
  list: { paddingVertical: 8 },
  empty: { alignItems: 'center', marginTop: 60 },
  emptyText: { fontSize: 15, color: '#9ca3af' },
});
