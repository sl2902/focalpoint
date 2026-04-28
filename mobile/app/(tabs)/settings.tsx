/**
 * Settings screen.
 *
 * Controls:
 * - Watch zone dropdown (9 countries)
 * - Optional watch zone area text field (e.g. "Northern Gaza")
 * - Language selector (en, ar, fr, tr, es)
 * - Time window selector (1, 3, 7, 14, 30 days — default 7)
 * - Discreet mode toggle
 * - Notifications toggle
 */

import React from 'react';
import {
  View,
  Text,
  TextInput,
  Switch,
  ScrollView,
  Pressable,
  StyleSheet,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useSettingsStore, type DaysOption } from '../../store/useSettingsStore';
import { useDiscreetStore } from '../../store/useDiscreetStore';
import { WATCH_ZONES, type WatchZone } from '../../constants/watchZones';
import { LANGUAGES } from '../../constants/languages';

const DATA_SOURCES = [
  { icon: 'flash-outline' as const,         label: 'GDELT Cloud',           detail: 'Conflict events' },
  { icon: 'newspaper-outline' as const,     label: 'GDELT Doc API',         detail: 'Media sentiment' },
  { icon: 'people-outline' as const,        label: 'CPJ',                   detail: 'Journalist incidents' },
  { icon: 'shield-outline' as const,        label: 'RSF Press Freedom',     detail: 'Press freedom index' },
];

const DAYS_OPTIONS: { label: string; value: DaysOption }[] = [
  { label: 'Last 24 hours', value: 1 },
  { label: 'Last 3 days',   value: 3 },
  { label: 'Last 7 days',   value: 7 },
  { label: 'Last 14 days',  value: 14 },
  { label: 'Last 30 days',  value: 30 },
];

export default function SettingsScreen() {
  const {
    watchZone, setWatchZone,
    watchZoneArea, setWatchZoneArea,
    language, setLanguage,
    days, setDays,
    discreetMode, setDiscreetMode,
    notifications, setNotifications,
  } = useSettingsStore();

  const syncDiscreet = useDiscreetStore((s) => s.setDiscreetMode);

  const handleDiscreetToggle = async (val: boolean) => {
    await setDiscreetMode(val);
    syncDiscreet(val);
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll}>
        <Text style={styles.title}>Settings</Text>

        {/* Watch Zone */}
        <Text style={styles.sectionHeader}>Watch Zone</Text>
        <View style={styles.chipGrid}>
          {WATCH_ZONES.map((z) => (
            <Pressable
              key={z}
              onPress={() => setWatchZone(z as WatchZone)}
              style={[styles.chip, watchZone === z && styles.chipActive]}
            >
              <Text style={[styles.chipText, watchZone === z && styles.chipTextActive]}>
                {z}
              </Text>
            </Pressable>
          ))}
        </View>

        <Text style={styles.label}>Specific area (optional)</Text>
        <TextInput
          style={styles.input}
          placeholder="e.g. Northern Gaza"
          placeholderTextColor="#9ca3af"
          value={watchZoneArea}
          onChangeText={setWatchZoneArea}
        />

        {/* Language */}
        <Text style={styles.sectionHeader}>Language</Text>
        <View style={styles.chipRow}>
          {LANGUAGES.map((l) => (
            <Pressable
              key={l.code}
              onPress={() => setLanguage(l.code)}
              style={[styles.chip, language === l.code && styles.chipActive]}
            >
              <Text style={[styles.chipText, language === l.code && styles.chipTextActive]}>
                {l.label}
              </Text>
            </Pressable>
          ))}
        </View>

        {/* Time Window */}
        <Text style={styles.sectionHeader}>Time Window</Text>
        {DAYS_OPTIONS.map((opt) => (
          <Pressable
            key={opt.value}
            onPress={() => setDays(opt.value)}
            style={[styles.radioRow, days === opt.value && styles.radioRowActive]}
          >
            <View style={[styles.radio, days === opt.value && styles.radioFilled]} />
            <Text style={[styles.radioLabel, days === opt.value && styles.radioLabelActive]}>
              {opt.label}
            </Text>
          </Pressable>
        ))}

        {/* Toggles */}
        <Text style={styles.sectionHeader}>Preferences</Text>
        <View style={styles.toggleRow}>
          <View>
            <Text style={styles.toggleLabel}>Discreet Mode</Text>
            <Text style={styles.toggleSub}>Dark screen, vibration only</Text>
          </View>
          <Switch
            value={discreetMode}
            onValueChange={handleDiscreetToggle}
            trackColor={{ true: '#2563eb' }}
          />
        </View>
        <View style={styles.toggleRow}>
          <View>
            <Text style={styles.toggleLabel}>Notifications</Text>
            <Text style={styles.toggleSub}>Push alerts for your watch zone</Text>
          </View>
          <Switch
            value={notifications}
            onValueChange={setNotifications}
            trackColor={{ true: '#2563eb' }}
          />
        </View>

        {/* Data Sources */}
        <Text style={styles.sectionHeader}>Data Sources</Text>
        <View style={styles.listCard}>
          {DATA_SOURCES.map((src, i) => (
            <React.Fragment key={src.label}>
              <View style={styles.dataSourceRow}>
                <View style={styles.dataSourceIcon}>
                  <Ionicons name={src.icon} size={16} color="#6b7280" />
                </View>
                <View style={styles.dataSourceText}>
                  <Text style={styles.dataSourceLabel}>{src.label}</Text>
                  <Text style={styles.dataSourceDetail}>{src.detail}</Text>
                </View>
              </View>
              {i < DATA_SOURCES.length - 1 && <View style={styles.listDivider} />}
            </React.Fragment>
          ))}
        </View>

        {/* About */}
        <Text style={styles.sectionHeader}>About</Text>
        <View style={styles.listCard}>
          <View style={styles.aboutRow}>
            <Ionicons name="information-circle-outline" size={18} color="#6b7280" />
            <View style={styles.aboutText}>
              <Text style={styles.aboutVersion}>FocalPoint v1.0</Text>
              <Text style={styles.aboutDescription}>
                Real-time conflict intelligence for field journalists.
              </Text>
            </View>
          </View>
        </View>

        <View style={styles.bottomSpacer} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  scroll: { padding: 16 },
  title: { fontSize: 22, fontWeight: '700', color: '#111827', marginBottom: 16 },
  sectionHeader: {
    fontSize: 13,
    fontWeight: '700',
    color: '#6b7280',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginTop: 20,
    marginBottom: 10,
  },
  label: { fontSize: 13, fontWeight: '600', color: '#374151', marginBottom: 6, marginTop: 10 },
  chipGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 7,
    backgroundColor: '#fff',
  },
  chipActive: { borderColor: '#2563eb', backgroundColor: '#eff6ff' },
  chipText: { fontSize: 13, color: '#374151' },
  chipTextActive: { color: '#2563eb', fontWeight: '600' },
  input: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 8,
    padding: 12,
    fontSize: 14,
    color: '#111827',
  },
  radioRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 8,
    gap: 10,
    backgroundColor: '#fff',
    marginBottom: 6,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  radioRowActive: { borderColor: '#2563eb', backgroundColor: '#eff6ff' },
  radio: {
    width: 18,
    height: 18,
    borderRadius: 9,
    borderWidth: 2,
    borderColor: '#d1d5db',
  },
  radioFilled: { borderColor: '#2563eb', backgroundColor: '#2563eb' },
  radioLabel: { fontSize: 14, color: '#374151' },
  radioLabelActive: { color: '#2563eb', fontWeight: '600' },
  toggleRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  toggleLabel: { fontSize: 15, fontWeight: '600', color: '#111827' },
  toggleSub: { fontSize: 12, color: '#9ca3af', marginTop: 2 },

  listCard: {
    backgroundColor: '#fff',
    borderRadius: 10,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    overflow: 'hidden',
  },
  listDivider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: '#f3f4f6',
    marginLeft: 44,
  },

  dataSourceRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 11,
    paddingHorizontal: 14,
    gap: 10,
  },
  dataSourceIcon: {
    width: 28,
    height: 28,
    borderRadius: 6,
    backgroundColor: '#f3f4f6',
    alignItems: 'center',
    justifyContent: 'center',
  },
  dataSourceText: { flex: 1 },
  dataSourceLabel: { fontSize: 14, fontWeight: '600', color: '#111827' },
  dataSourceDetail: { fontSize: 12, color: '#9ca3af', marginTop: 1 },

  aboutRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
    padding: 14,
  },
  aboutText: { flex: 1 },
  aboutVersion: { fontSize: 14, fontWeight: '700', color: '#111827' },
  aboutDescription: { fontSize: 13, color: '#6b7280', marginTop: 3, lineHeight: 18 },

  bottomSpacer: { height: 24 },
});
