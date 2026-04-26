/**
 * Query screen — journalist submits a free-text or voice question.
 *
 * Region defaults to the watch zone from Settings but can be changed
 * for a one-off query via the dropdown — it never writes back to the store.
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  ScrollView,
  Modal,
  StyleSheet,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { SeverityBadge } from '../../components/SeverityBadge';
import { CitationList } from '../../components/CitationList';
import { LoadingOverlay } from '../../components/LoadingOverlay';
import { useAudio } from '../../hooks/useAudio';
import { useSettingsStore } from '../../store/useSettingsStore';
import { postQuery } from '../../services/query';
import { WATCH_ZONES } from '../../constants/watchZones';
import type { QueryResponse } from '../../types/api';

export default function QueryScreen() {
  const watchZone = useSettingsStore((s) => s.watchZone);
  const language = useSettingsStore((s) => s.language);

  const [region, setRegion] = useState(watchZone);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { isRecording, audioUri, startRecording, stopRecording, clearAudio } =
    useAudio();

  const handleSubmit = async () => {
    if (!text.trim() && !audioUri) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await postQuery({
        region,
        text: text.trim() || undefined,
        language,
        audioUri: audioUri ?? undefined,
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Query failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Intelligence Query</Text>

        {/* Region dropdown */}
        <Text style={styles.label}>Region</Text>
        <Pressable
          onPress={() => setDropdownOpen(true)}
          style={styles.dropdownBtn}
        >
          <Text style={styles.dropdownValue}>{region}</Text>
          <Ionicons name="chevron-down" size={18} color="#6b7280" />
        </Pressable>

        {/* Question input */}
        <Text style={styles.label}>Question</Text>
        <TextInput
          style={styles.input}
          placeholder="e.g. Is it safe to travel to northern Gaza today?"
          placeholderTextColor="#9ca3af"
          value={text}
          onChangeText={setText}
          multiline
          maxLength={500}
        />

        {/* Voice button */}
        <Pressable
          onPressIn={startRecording}
          onPressOut={() => stopRecording()}
          style={[styles.voiceBtn, isRecording && styles.voiceBtnActive]}
        >
          <Text style={styles.voiceBtnText}>
            {isRecording ? 'Recording…' : 'Hold for Voice'}
          </Text>
        </Pressable>

        {audioUri && (
          <View style={styles.audioPill}>
            <Text style={styles.audioPillText}>Audio recorded</Text>
            <Pressable onPress={clearAudio}>
              <Text style={styles.audioRemove}>✕</Text>
            </Pressable>
          </View>
        )}

        {/* Submit */}
        <Pressable
          onPress={handleSubmit}
          disabled={loading || (!text.trim() && !audioUri)}
          style={({ pressed }) => [
            styles.submitBtn,
            (loading || (!text.trim() && !audioUri)) && styles.submitBtnDisabled,
            pressed && styles.submitBtnPressed,
          ]}
        >
          <Text style={styles.submitText}>Submit</Text>
        </Pressable>

        {error && <Text style={styles.error}>{error}</Text>}

        {result && (
          <View style={styles.result}>
            <SeverityBadge severity={result.severity} />
            {result.was_sanitised && (
              <Text style={styles.sanitisedNote}>
                Your query was modified to remove unsafe content.
              </Text>
            )}
            <Text style={styles.answer}>{result.answer}</Text>
            <CitationList citations={result.source_citations} />
          </View>
        )}
      </ScrollView>

      {loading && (
        <LoadingOverlay message="Searching for current intelligence... this may take a moment." />
      )}

      {/* Region picker modal */}
      <Modal
        visible={dropdownOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setDropdownOpen(false)}
      >
        <Pressable style={styles.modalBackdrop} onPress={() => setDropdownOpen(false)}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Select Region</Text>
            {WATCH_ZONES.map((zone) => (
              <Pressable
                key={zone}
                onPress={() => { setRegion(zone); setDropdownOpen(false); }}
                style={[styles.modalItem, region === zone && styles.modalItemActive]}
              >
                <Text style={[styles.modalItemText, region === zone && styles.modalItemTextActive]}>
                  {zone}
                </Text>
                {region === zone && (
                  <Ionicons name="checkmark" size={18} color="#1d4ed8" />
                )}
              </Pressable>
            ))}
          </View>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  scroll: { padding: 16 },
  title: { fontSize: 22, fontWeight: '700', color: '#111827', marginBottom: 4 },
  label: { fontSize: 13, fontWeight: '600', color: '#374151', marginBottom: 6, marginTop: 12 },

  dropdownBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 11,
  },
  dropdownValue: { fontSize: 14, color: '#111827', fontWeight: '500' },

  input: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 8,
    padding: 12,
    fontSize: 14,
    color: '#111827',
    minHeight: 80,
    textAlignVertical: 'top',
  },
  voiceBtn: {
    marginTop: 12,
    backgroundColor: '#1d4ed8',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  voiceBtnActive: { backgroundColor: '#dc2626' },
  voiceBtnText: { color: '#fff', fontWeight: '600', fontSize: 15 },
  audioPill: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 8,
    backgroundColor: '#dbeafe',
    borderRadius: 20,
    paddingHorizontal: 12,
    paddingVertical: 6,
    alignSelf: 'flex-start',
    gap: 8,
  },
  audioPillText: { fontSize: 13, color: '#1d4ed8' },
  audioRemove: { fontSize: 13, color: '#1d4ed8', fontWeight: '700' },
  submitBtn: {
    marginTop: 16,
    backgroundColor: '#2563eb',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
  },
  submitBtnDisabled: { backgroundColor: '#93c5fd' },
  submitBtnPressed: { opacity: 0.85 },
  submitText: { color: '#fff', fontWeight: '700', fontSize: 16 },
  error: { marginTop: 12, color: '#dc2626', fontSize: 14 },
  result: {
    marginTop: 20,
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 16,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    gap: 10,
  },
  sanitisedNote: { fontSize: 12, color: '#d97706', fontStyle: 'italic' },
  answer: { fontSize: 15, color: '#111827', lineHeight: 22 },

  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.4)',
    justifyContent: 'center',
    padding: 32,
  },
  modalSheet: {
    backgroundColor: '#fff',
    borderRadius: 12,
    paddingVertical: 8,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 12,
    elevation: 10,
  },
  modalTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: '#6b7280',
    paddingHorizontal: 16,
    paddingVertical: 10,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  modalItem: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingVertical: 13,
    borderTopWidth: 1,
    borderTopColor: '#f3f4f6',
  },
  modalItemActive: { backgroundColor: '#eff6ff' },
  modalItemText: { fontSize: 15, color: '#111827' },
  modalItemTextActive: { color: '#1d4ed8', fontWeight: '600' },
});
