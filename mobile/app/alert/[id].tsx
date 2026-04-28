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
import { useLocalSearchParams, useRouter } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { SeverityBadge } from '../../components/SeverityBadge';
import { ConfidenceBar } from '../../components/ConfidenceBar';
import { CitationList } from '../../components/CitationList';
import { FloorWarning } from '../../components/FloorWarning';
import { ElevationNote } from '../../components/ElevationNote';
import { CachedBanner } from '../../components/CachedBanner';
import { fetchAlertForRegion } from '../../services/alerts';
import { isFallback, upsertAlert } from '../../services/cache';
import { useSettingsStore } from '../../store/useSettingsStore';
import { useRefreshStore } from '../../store/useRefreshStore';
import type { AlertResponse } from '../../types/api';

const ELEVATION_PATTERN = /\[Elevation note:[^\]]+\]/i;
const FLOOR_PATTERN = /\[Historical risk floor applied[^\]]*\]/i;

export default function AlertDetailScreen() {
  const { data } = useLocalSearchParams<{ id: string; data: string }>();
  const router = useRouter();
  const days = useSettingsStore((s) => s.days);
  const { refreshingRegion, startRefresh } = useRefreshStore();

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

  // For fallback alerts: hand off to the feed's background refresh hook and
  // navigate back immediately so the feed card shows the loading indicator.
  function handleRetry() {
    if (!alert || refreshingRegion !== null) return;
    startRefresh(alert.region);
    router.back();
  }

  // For valid alerts: in-place refresh — stays on the detail screen.
  async function handleRefresh() {
    if (!alert || refreshing) return;
    setRefreshing(true);
    setRefreshError(false);
    try {
      const fresh = await fetchAlertForRegion(alert.region, days, true);
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

  const alertIsFallback = isFallback(alert);
  const elevationMatch = alert.summary.match(ELEVATION_PATTERN);
  const floorMatch = alert.summary.match(FLOOR_PATTERN);
  const cleanSummary = alert.summary
    .replace(ELEVATION_PATTERN, '')
    .replace(FLOOR_PATTERN, '')
    .trim();

  const fetchedAt = new Date(alert.timestamp);
  const isOld = !alertIsFallback && Date.now() - fetchedAt.getTime() > 60 * 60 * 1000;

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      {isOld && <CachedBanner lastFetchedAt={fetchedAt} />}
      <ScrollView contentContainerStyle={styles.scroll}>
        <View style={styles.regionRow}>
          <Text style={styles.region}>{alert.region}</Text>
          {!alertIsFallback && <SeverityBadge severity={alert.severity} />}
        </View>

        <Text style={styles.timestamp}>
          {new Date(alert.timestamp).toLocaleString([], { timeZone: 'UTC' })} UTC
        </Text>

        {alertIsFallback ? (
          <View style={styles.fallbackBanner}>
            <Text style={styles.fallbackTitle}>Assessment unavailable</Text>
            <Text style={styles.fallbackBody}>
              Gemma 4 could not generate a safety assessment for this region.
              Tap Refresh below to run a fresh live attempt.
            </Text>
          </View>
        ) : (
          <>
            <ConfidenceBar confidence={alert.confidence} />
            {floorMatch && (
              <FloorWarning reason="Severity elevated to AMBER — historical CPJ/RSF data indicates elevated risk for journalists in this region." />
            )}
            {elevationMatch && (
              <ElevationNote note={elevationMatch[0].replace(/^\[|\]$/g, '')} />
            )}
            <Text style={styles.summary}>{cleanSummary}</Text>
            <CitationList citations={alert.source_citations} />
          </>
        )}

        {alertIsFallback ? (
          <Pressable
            style={[
              styles.refreshBtn,
              styles.refreshBtnFallback,
              refreshingRegion !== null && styles.refreshBtnDisabled,
            ]}
            onPress={handleRetry}
            disabled={refreshingRegion !== null}
          >
            <Text style={styles.refreshBtnText}>
              Retry assessment — {alert.region} ({days}d)
            </Text>
          </Pressable>
        ) : (
          <>
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
          </>
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

  fallbackBanner: {
    marginTop: 16,
    backgroundColor: '#fef3c7',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#fcd34d',
  },
  fallbackTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#92400e',
    marginBottom: 6,
  },
  fallbackBody: {
    fontSize: 14,
    color: '#92400e',
    lineHeight: 21,
  },

  refreshBtn: {
    marginTop: 28,
    backgroundColor: '#1d4ed8',
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: 'center',
  },
  refreshBtnFallback: { backgroundColor: '#b45309' },
  refreshBtnDisabled: { backgroundColor: '#93c5fd' },
  refreshBtnText: { color: '#fff', fontSize: 15, fontWeight: '600' },

  refreshError: {
    marginTop: 10,
    fontSize: 13,
    color: '#ef4444',
    textAlign: 'center',
  },
});
