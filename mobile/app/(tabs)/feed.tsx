import React, { useState, useCallback } from 'react';
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
import { EmptyRegionCard } from '../../components/EmptyRegionCard';
import { FallbackCard } from '../../components/FallbackCard';
import { useAlerts } from '../../hooks/useAlerts';
import { fetchAlertForRegion } from '../../services/alerts';
import { isFallback, upsertAlert } from '../../services/cache';
import { WATCH_ZONES } from '../../constants/watchZones';
import { useRefreshStore } from '../../store/useRefreshStore';
import type { AlertResponse } from '../../types/api';

const DAYS_LABELS: Record<number, string> = {
  1: '1d', 3: '3d', 7: '7d', 14: '14d', 30: '30d',
};

type FeedItem =
  | { type: 'alert'; data: AlertResponse }
  | { type: 'fallback'; data: AlertResponse }
  | { type: 'empty'; region: string };

export default function FeedScreen() {
  const router = useRouter();
  const { alerts, days, refresh, refreshing, revalidate } = useAlerts();
  const { refreshingRegion } = useRefreshStore();
  const [loadingRegion, setLoadingRegion] = useState<string | null>(null);

  // Regions that have no cached alert for the current days window.
  const loadedSet = new Set(alerts.map((a) => a.region));
  const feedItems: FeedItem[] = [
    ...alerts.map((a) => ({
      type: isFallback(a) ? ('fallback' as const) : ('alert' as const),
      data: a,
    })),
    ...WATCH_ZONES.filter((z) => !loadedSet.has(z)).map((z) => ({
      type: 'empty' as const,
      region: z,
    })),
  ];

  const handlePress = (alert: AlertResponse) => {
    router.push({
      pathname: '/alert/[id]',
      params: { id: alert.region, data: JSON.stringify(alert) },
    });
  };

  const handleLoad = useCallback(async (region: string) => {
    if (loadingRegion) return;
    setLoadingRegion(region);
    try {
      const fresh = await fetchAlertForRegion(region, days);
      await upsertAlert(fresh, fresh.days ?? days);
      revalidate();
    } catch {
      // Card stays empty — user can retry.
    } finally {
      setLoadingRegion(null);
    }
  }, [loadingRegion, days, revalidate]);

  const renderItem = ({ item }: { item: FeedItem }) => {
    if (item.type === 'alert') {
      return (
        <AlertCard
          alert={item.data}
          onPress={() => handlePress(item.data)}
        />
      );
    }
    if (item.type === 'fallback') {
      return (
        <FallbackCard
          alert={item.data}
          onPress={() => handlePress(item.data)}
          loading={refreshingRegion === item.data.region}
        />
      );
    }
    return (
      <EmptyRegionCard
        region={item.region}
        days={days}
        onLoad={() => handleLoad(item.region)}
        loading={loadingRegion === item.region}
        disabled={
          (loadingRegion !== null && loadingRegion !== item.region) ||
          refreshingRegion !== null
        }
      />
    );
  };

  const isEmpty = feedItems.length === 0 && !refreshing;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>FocalPoint</Text>
          <Text style={styles.subtitle}>Conflict Intelligence Feed</Text>
        </View>
      </View>

      <FlatList
        data={feedItems}
        keyExtractor={(item) => {
          if (item.type === 'empty') return `empty:${item.region}`;
          return `${item.type}:${item.data.region}`;
        }}
        renderItem={renderItem}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={refresh}
            tintColor="#2563eb"
          />
        }
        contentContainerStyle={[styles.list, isEmpty && styles.listEmpty]}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Text style={styles.emptyIcon}>📡</Text>
            <Text style={styles.emptyTitle}>
              No {DAYS_LABELS[days]} data available yet
            </Text>
            <Text style={styles.emptyBody}>
              Data is updated automatically in the background.
            </Text>
          </View>
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

  list: { paddingVertical: 8 },
  listEmpty: { flex: 1 },

  emptyState: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
    paddingTop: 80,
  },
  emptyIcon: { fontSize: 40, marginBottom: 16 },
  emptyTitle: {
    fontSize: 16,
    fontWeight: '600',
    color: '#374151',
    marginBottom: 8,
    textAlign: 'center',
  },
  emptyBody: {
    fontSize: 14,
    color: '#6b7280',
    textAlign: 'center',
    lineHeight: 21,
  },
});
