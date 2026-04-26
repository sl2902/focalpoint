/**
 * Alert Detail screen.
 *
 * Route params: { id: string, data: string (JSON-serialised AlertResponse) }
 *
 * The initial alert is passed as a serialised param from the feed.
 * The Refresh button triggers a live backend call for this region only —
 * the only path that may invoke Gemma 4. On success the local SQLite
 * cache is updated and the screen reflects the new result.
 */

import React, { useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  Pressable,
} from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { SeverityBadge } from '../../components/SeverityBadge';
import { ConfidenceBar } from '../../components/ConfidenceBar';
import { CitationList } from '../../components/CitationList';
import { FloorWarning } from '../../components/FloorWarning';
import { ElevationNote } from '../../components/ElevationNote';
import { CachedBanner } from '../../components/CachedBanner';
import { fetchAlertForRegion } from '../../services/alerts';
import { upsertAlert } from '../../services/cache';
import { useSettingsStore } from '../../store/useSettingsStore';
import type { AlertResponse } from '../../types/api';

const ELEVATION_PATTERN = /\[Elevation note:[^\]]+\]/i;
const FLOOR_PATTERN = /\[Historical risk floor applied[^\]]*\]/i;

export default function AlertDetailScreen() {
  const { data } = useLocalSearchParams<{ id: string; data: string }>();
  const days = useSettingsStore((s) => s.days);

  const [alert, setAlert] = useState<AlertResponse | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(false);

  useEffect(() => {
    if (!data) return;
    try {
      setAlert(JSON.parse(data) as AlertResponse);
    } catch {
      // leave alert null — "not found" UI handles it
    }
  }, [data]);

  async function handleRefresh() {
    if (!alert || refreshing) return;
    setRefreshing(true);
    setRefreshError(false);
    try {
      const fresh = await fetchAlertForRegion(alert.region, days);
      await upsertAlert(fresh, days);
      setAlert(fresh);
    } catch {
      setRefreshError(true);
    } finally {
      setRefreshing(false);
    }
  }

  if (!alert) {
    return (
      <View style={styles.center}>
        <Text style={styles.notFound}>Alert not found.</Text>
      </View>
    );
  }

  const elevationMatch = alert.summary.match(ELEVATION_PATTERN);
  const floorMatch = alert.summary.match(FLOOR_PATTERN);
  const cleanSummary = alert.summary
    .replace(ELEVATION_PATTERN, '')
    .replace(FLOOR_PATTERN, '')
    .trim();

  const fetchedAt = new Date(alert.timestamp);
  const isOld = Date.now() - fetchedAt.getTime() > 60 * 60 * 1000;

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      {isOld && <CachedBanner lastFetchedAt={fetchedAt} />}
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.regionRow}>
          <Text style={styles.region}>{alert.region}</Text>
          <SeverityBadge severity={alert.severity} />
        </View>

        <Text style={styles.timestamp}>
          {new Date(alert.timestamp).toLocaleString([], { timeZone: 'UTC' })} UTC
        </Text>

        <ConfidenceBar confidence={alert.confidence} />

        {floorMatch && (
          <FloorWarning reason="Severity elevated to AMBER — historical CPJ/RSF data indicates elevated risk for journalists in this region." />
        )}
        {elevationMatch && (
          <ElevationNote note={elevationMatch[0].replace(/^\[|\]$/g, '')} />
        )}

        <Text style={styles.summary}>{cleanSummary}</Text>

        <CitationList citations={alert.source_citations} />

        {/* Refresh button — triggers a live Gemma 4 assessment for this region */}
        <Pressable
          style={[styles.refreshBtn, refreshing && styles.refreshBtnDisabled]}
          onPress={handleRefresh}
          disabled={refreshing}
        >
          {refreshing ? (
            <ActivityIndicator size="small" color="#fff" />
          ) : (
            <Text style={styles.refreshBtnText}>
              Refresh {alert.region} ({days}d)
            </Text>
          )}
        </Pressable>

        {refreshError && (
          <Text style={styles.refreshError}>
            Could not reach the server. Try again when connected.
          </Text>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  notFound: { fontSize: 15, color: '#9ca3af' },
  scroll: { padding: 16, paddingBottom: 32 },

  regionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 4,
  },
  region: { fontSize: 22, fontWeight: '700', color: '#111827' },
  timestamp: { fontSize: 13, color: '#9ca3af', marginBottom: 12 },
  summary: {
    fontSize: 15,
    color: '#111827',
    lineHeight: 23,
    marginTop: 14,
  },

  refreshBtn: {
    marginTop: 28,
    backgroundColor: '#1d4ed8',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  refreshBtnDisabled: { backgroundColor: '#93c5fd' },
  refreshBtnText: { color: '#fff', fontSize: 15, fontWeight: '600' },

  refreshError: {
    marginTop: 10,
    fontSize: 13,
    color: '#ef4444',
    textAlign: 'center',
  },
});
