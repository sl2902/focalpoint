import React from 'react';
import {
  FlatList,
  RefreshControl,
  View,
  Text,
  Pressable,
  StyleSheet,
} from 'react-native';
import { useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { AlertCard } from '../../components/AlertCard';
import { AlertCardSkeleton } from '../../components/AlertCardSkeleton';
import { useAlerts, RegionEntry } from '../../hooks/useAlerts';
import type { DaysOption } from '../../store/useSettingsStore';
import type { AlertResponse } from '../../types/api';

const DAYS_OPTIONS: DaysOption[] = [1, 3, 7, 14, 30];
const DAYS_LABELS: Record<number, string> = {
  1: '1d', 3: '3d', 7: '7d', 14: '14d', 30: '30d',
};

export default function FeedScreen() {
  const router = useRouter();
  const { entries, days, setDays, refresh, refreshing } = useAlerts();

  const handlePress = (alert: AlertResponse) => {
    router.push({
      pathname: '/alert/[id]',
      params: { id: alert.region, data: JSON.stringify(alert) },
    });
  };

  const renderEntry = ({ item }: { item: RegionEntry }) => {
    if (item.status === 'loading' && !item.alert) {
      return <AlertCardSkeleton />;
    }
    if (item.status === 'error' && !item.alert) {
      return (
        <View style={styles.errorRow}>
          <Text style={styles.errorRegion}>{item.region}</Text>
          <Text style={styles.errorMsg}>Could not load — pull to retry</Text>
        </View>
      );
    }
    return (
      <AlertCard
        alert={item.alert!}
        onPress={() => handlePress(item.alert!)}
      />
    );
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>FocalPoint</Text>
          <Text style={styles.subtitle}>Conflict Intelligence Feed</Text>
        </View>
      </View>

      {/* Days segmented control */}
      <View style={styles.segmentBar}>
        {DAYS_OPTIONS.map((d) => (
          <Pressable
            key={d}
            onPress={() => setDays(d)}
            style={[styles.segment, days === d && styles.segmentActive]}
          >
            <Text style={[styles.segmentText, days === d && styles.segmentTextActive]}>
              {DAYS_LABELS[d]}
            </Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={entries}
        keyExtractor={(item) => item.region}
        renderItem={renderEntry}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={refresh}
            tintColor="#2563eb"
          />
        }
        contentContainerStyle={styles.list}
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

  segmentBar: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    paddingHorizontal: 16,
    paddingVertical: 10,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#e5e7eb',
  },
  segment: {
    flex: 1,
    paddingVertical: 6,
    borderRadius: 6,
    alignItems: 'center',
    backgroundColor: '#f3f4f6',
  },
  segmentActive: {
    backgroundColor: '#1d4ed8',
  },
  segmentText: {
    fontSize: 13,
    fontWeight: '600',
    color: '#6b7280',
  },
  segmentTextActive: {
    color: '#fff',
  },

  list: { paddingVertical: 8 },

  errorRow: {
    marginHorizontal: 16,
    marginVertical: 6,
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 14,
    borderWidth: 1,
    borderColor: '#fee2e2',
  },
  errorRegion: { fontSize: 14, fontWeight: '600', color: '#374151', marginBottom: 2 },
  errorMsg: { fontSize: 12, color: '#ef4444' },
});
