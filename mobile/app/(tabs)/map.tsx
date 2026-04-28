import React, { useState, useEffect, useRef, useCallback } from 'react';
import { View, Text, Pressable, StyleSheet, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import { useFocusEffect } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';
import MapView from '../../components/MapView';
import { isFallback, getLatestAlertsByDays } from '../../services/cache';
import { useSettingsStore } from '../../store/useSettingsStore';
import { WATCH_ZONES, WATCH_ZONE_COORDS } from '../../constants/watchZones';
import { SEVERITY_COLORS, SEVERITY_BG_COLORS } from '../../constants/severity';
import type { AlertResponse } from '../../types/api';
import type { ComponentMarker } from '../../types/map';

const LEGEND_ITEMS = [
  { key: 'GREEN',             color: SEVERITY_COLORS.GREEN,    label: 'Safe'     },
  { key: 'AMBER',             color: SEVERITY_COLORS.AMBER,    label: 'Elevated' },
  { key: 'RED',               color: SEVERITY_COLORS.RED,      label: 'Active'   },
  { key: 'CRITICAL',          color: SEVERITY_COLORS.CRITICAL, label: 'Critical' },
  { key: 'INSUFFICIENT_DATA', color: '#9ca3af',                label: 'No data'  },
];


export default function MapScreen() {
  const router = useRouter();
  const days = useSettingsStore((s) => s.days);
  const [markers, setMarkers] = useState<ComponentMarker[]>([]);
  const [alertByRegion, setAlertByRegion] = useState<Map<string, AlertResponse>>(new Map());
  const [version, setVersion] = useState(0);
  // Native: selected marker drives the preview popup overlay.
  const [selectedMarker, setSelectedMarker] = useState<ComponentMarker | null>(null);
  // Native: grey-marker toast.
  const [toastRegion, setToastRegion] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Re-read SQLite whenever days changes, on first mount, or when the tab regains focus.
  useEffect(() => {
    let cancelled = false;
    getLatestAlertsByDays(days).then((alerts) => {
      if (cancelled) return;
      // Fallback alerts (failed Gemma calls) show as grey markers — only
      // valid assessments contribute colour and popup data.
      const validAlerts = alerts.filter((a) => !isFallback(a));
      const byRegion = new Map(validAlerts.map((a) => [a.region, a]));
      setAlertByRegion(byRegion);
      const newMarkers: ComponentMarker[] = WATCH_ZONES.map((region) => {
        const coords = WATCH_ZONE_COORDS[region];
        const alert = byRegion.get(region);
        return {
          id: region,
          latitude: coords.latitude,
          longitude: coords.longitude,
          severity: alert ? alert.severity : 'INSUFFICIENT_DATA',
          region,
          timestamp:  alert?.timestamp,
          summary:    alert?.summary,
          confidence: alert?.confidence,
        };
      });
      setMarkers(newMarkers);
      // Dismiss stale popup when data refreshes.
      setSelectedMarker(null);
    });
    return () => { cancelled = true; };
  }, [days, version]);

  useFocusEffect(
    useCallback(() => {
      setVersion((v) => v + 1);
    }, []),
  );

  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  const handleMarkerPress = useCallback(
    (marker: ComponentMarker) => {
      const alert = alertByRegion.get(marker.region);

      if (!alert) {
        // Web: Leaflet popup already shows "no data" inline — nothing to do.
        // Native: show brief toast.
        if (Platform.OS !== 'web') {
          if (toastTimer.current) clearTimeout(toastTimer.current);
          setToastRegion(marker.region);
          toastTimer.current = setTimeout(() => setToastRegion(null), 2500);
        }
        return;
      }

      if (Platform.OS === 'web') {
        // User already saw the Leaflet popup and clicked "View Details" — navigate now.
        router.push({
          pathname: '/alert/[id]',
          params: { id: marker.region, data: JSON.stringify(alert) },
        });
      } else {
        // First tap on native — show preview popup; navigation is via "View Details".
        setSelectedMarker(marker);
        setToastRegion(null);
      }
    },
    [alertByRegion, router],
  );

  function handleViewDetails() {
    if (!selectedMarker) return;
    const alert = alertByRegion.get(selectedMarker.region);
    if (!alert) return;
    setSelectedMarker(null);
    router.push({
      pathname: '/alert/[id]',
      params: { id: selectedMarker.region, data: JSON.stringify(alert) },
    });
  }

  const popupAlert = selectedMarker ? alertByRegion.get(selectedMarker.region) : undefined;

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <Text style={styles.title}>Incident Map</Text>
        <Text style={styles.timeLabel}>Showing {days}d data</Text>
      </View>

      <View style={styles.mapContainer}>
        <MapView markers={markers} onMarkerPress={handleMarkerPress} />

        {/* Severity legend — bottom-right overlay */}
        <View style={styles.legend} pointerEvents="none">
          {LEGEND_ITEMS.map((item) => (
            <View key={item.key} style={styles.legendRow}>
              <View style={[styles.legendDot, { backgroundColor: item.color }]} />
              <Text style={styles.legendLabel}>{item.label}</Text>
            </View>
          ))}
        </View>

        {/* Native: marker preview popup — slides up from bottom */}
        {selectedMarker !== null && popupAlert && Platform.OS !== 'web' && (
          <View style={styles.markerPopup}>
            <View style={styles.popupHeader}>
              <Text style={styles.popupRegion}>{selectedMarker.region}</Text>
              <Pressable
                onPress={() => setSelectedMarker(null)}
                style={styles.popupDismiss}
                hitSlop={12}
              >
                <Text style={styles.popupDismissText}>✕</Text>
              </Pressable>
            </View>

            <View
              style={[
                styles.popupBadge,
                { backgroundColor: SEVERITY_BG_COLORS[selectedMarker.severity] },
              ]}
            >
              <View
                style={[
                  styles.popupBadgeDot,
                  { backgroundColor: SEVERITY_COLORS[selectedMarker.severity] },
                ]}
              />
              <Text
                style={[
                  styles.popupBadgeText,
                  { color: SEVERITY_COLORS[selectedMarker.severity] },
                ]}
              >
                {selectedMarker.severity}
              </Text>
            </View>

            <Pressable style={styles.viewDetailsBtn} onPress={handleViewDetails}>
              <Text style={styles.viewDetailsBtnText}>View Details →</Text>
            </Pressable>
          </View>
        )}

        {/* Native: grey-marker toast */}
        {toastRegion !== null && Platform.OS !== 'web' && (
          <View style={styles.toast} pointerEvents="none">
            <Text style={styles.toastText}>
              No {toastRegion} data for {days}d — set it as your Watch Zone in Settings to load.
            </Text>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e5e7eb',
  },
  title: { fontSize: 18, fontWeight: '700', color: '#111827' },
  timeLabel: { fontSize: 12, color: '#6b7280', fontWeight: '500' },

  mapContainer: { flex: 1 },

  legend: {
    position: 'absolute',
    bottom: 16,
    right: 12,
    backgroundColor: 'rgba(0,0,0,0.72)',
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    gap: 6,
  },
  legendRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendLabel: { fontSize: 11, color: '#f9fafb', fontWeight: '500' },

  // Native marker preview popup
  markerPopup: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    backgroundColor: '#fff',
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 20,
    paddingTop: 16,
    paddingBottom: 28,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: -3 },
    shadowOpacity: 0.15,
    shadowRadius: 10,
    elevation: 12,
  },
  popupHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 10,
  },
  popupRegion: { fontSize: 18, fontWeight: '700', color: '#111827', flex: 1 },
  popupDismiss: { padding: 4 },
  popupDismissText: { fontSize: 16, color: '#9ca3af' },

  popupBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    alignSelf: 'flex-start',
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 20,
    marginBottom: 16,
  },
  popupBadgeDot: { width: 8, height: 8, borderRadius: 4 },
  popupBadgeText: { fontSize: 13, fontWeight: '700' },

  viewDetailsBtn: {
    backgroundColor: '#1d4ed8',
    borderRadius: 10,
    paddingVertical: 13,
    alignItems: 'center',
  },
  viewDetailsBtnText: { color: '#fff', fontSize: 15, fontWeight: '600' },

  // Toast for grey markers (native only)
  toast: {
    position: 'absolute',
    top: 12,
    left: 24,
    right: 24,
    backgroundColor: 'rgba(17,24,39,0.88)',
    borderRadius: 10,
    paddingVertical: 10,
    paddingHorizontal: 14,
    alignItems: 'center',
  },
  toastText: {
    color: '#f9fafb',
    fontSize: 13,
    textAlign: 'center',
    lineHeight: 18,
  },
});
