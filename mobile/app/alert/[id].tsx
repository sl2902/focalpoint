/**
 * Alert Detail screen (modal).
 *
 * Route params: { region: string, timestamp: string }
 * Full AlertResponse is loaded from SQLite cache using region as lookup key.
 * We do NOT pass the full JSON via route params — React Navigation has a
 * param size limit and serialisation is expensive.
 *
 * Displays:
 * - Severity badge
 * - Summary
 * - Confidence bar
 * - FloorWarning (if floor_applied detected in summary)
 * - ElevationNote (if elevation note detected in summary)
 * - Source citations
 */

import React, { useEffect, useState } from 'react';
import { View, Text, ScrollView, StyleSheet, ActivityIndicator } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import { SeverityBadge } from '../../components/SeverityBadge';
import { ConfidenceBar } from '../../components/ConfidenceBar';
import { CitationList } from '../../components/CitationList';
import { FloorWarning } from '../../components/FloorWarning';
import { ElevationNote } from '../../components/ElevationNote';
import { CachedBanner } from '../../components/CachedBanner';
import { getAlertByRegion } from '../../services/cache';
import type { AlertResponse } from '../../types/api';

// Detect elevation note appended by alert_generator.py
const ELEVATION_PATTERN = /\[Elevation note:[^\]]+\]/i;
// Detect floor applied note
const FLOOR_PATTERN = /\[Historical risk floor applied[^\]]*\]/i;

export default function AlertDetailScreen() {
  const { region, timestamp } = useLocalSearchParams<{
    id: string;
    region: string;
    timestamp: string;
  }>();

  const [alert, setAlert] = useState<AlertResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!region) return;
    getAlertByRegion(region)
      .then(setAlert)
      .finally(() => setLoading(false));
  }, [region]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#2563eb" />
      </View>
    );
  }

  if (!alert) {
    return (
      <View style={styles.center}>
        <Text style={styles.notFound}>Alert not found in cache.</Text>
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
          {new Date(alert.timestamp).toLocaleString()}
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
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  notFound: { fontSize: 15, color: '#9ca3af' },
  scroll: { padding: 16 },
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
});
