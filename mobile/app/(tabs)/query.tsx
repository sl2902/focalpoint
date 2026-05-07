/**
 * Query screen — journalist submits a free-text or voice question.
 *
 * Region defaults to the watch zone from Settings but can be changed
 * for a one-off query via the dropdown — it never writes back to the store.
 *
 * Voice transcription:
 *   Primary path  — POST /transcribe (local Gemma 4 E4B on the backend)
 *   Fallback path — expo-speech-recognition (iOS native, used when backend returns 503)
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
import {
  ExpoSpeechRecognitionModule,
  useSpeechRecognitionEvent,
} from 'expo-speech-recognition';
import { SeverityBadge } from '../../components/SeverityBadge';
import { CitationList } from '../../components/CitationList';
import { LoadingOverlay } from '../../components/LoadingOverlay';
import { useAudio } from '../../hooks/useAudio';
import { useSettingsStore } from '../../store/useSettingsStore';
import { postQuery, postTranscribe } from '../../services/query';
import { WATCH_ZONES } from '../../constants/watchZones';
import type { QueryResponse } from '../../types/api';

// Maps 2-letter language code to BCP-47 tag for iOS speech recognition.
const SPEECH_RECOGNITION_LANG: Record<string, string> = {
  en: 'en-US',
  ar: 'ar-SA',
  fr: 'fr-FR',
  tr: 'tr-TR',
  es: 'es-ES',
};

// Per-bar sensitivity multipliers — symmetric, centre bars most responsive.
const METER_OFFSETS = [0.55, 0.75, 0.9, 1.0, 0.9, 0.75, 0.55];

/** Map dBFS (-160..0) to a bar height in px (2..24). */
function meterBarHeight(dB: number | null, offset: number): number {
  if (dB === null) return 2;
  // Practical range: -60 dB (silence) to 0 dB (full). Below -60 treat as silent.
  const normalized = Math.max(0, Math.min(1, (dB + 60) / 60));
  return Math.max(2, Math.min(24, normalized * 24 * offset));
}

export default function QueryScreen() {
  const watchZone = useSettingsStore((s) => s.watchZone);
  const language = useSettingsStore((s) => s.language);

  const [region, setRegion] = useState(watchZone);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fallback state — set to true when /transcribe returns 503.
  const [usingDeviceSpeech, setUsingDeviceSpeech] = useState(false);
  const [deviceSpeechRunning, setDeviceSpeechRunning] = useState(false);
  // Accumulates partial transcript so we can commit it when the session ends.
  const latestTranscriptRef = React.useRef('');
  // Tracks when the current recording started so we can enforce minimum duration.
  const recordingStartedAt = React.useRef(0);
  // Ephemeral hint shown when the user releases the button too quickly.
  const [shortRecordingHint, setShortRecordingHint] = useState<string | null>(null);

  const { isRecording, audioUri, meteringLevel, startRecording, stopRecording, clearAudio } =
    useAudio();

  // Always-current ref so handleSubmit reads the live input value, never a stale closure.
  const textRef = React.useRef(text);
  textRef.current = text;

  // Capture every interim result so we always have the freshest text available.
  useSpeechRecognitionEvent('result', (event) => {
    const transcript = event.results[0]?.transcript ?? '';
    latestTranscriptRef.current = transcript;
    if (event.isFinal) {
      setText(transcript);
      setTranscribing(false);
      setDeviceSpeechRunning(false);
      console.log('[transcribe] device speech final:', transcript);
    }
  });

  useSpeechRecognitionEvent('error', (event) => {
    console.log('[transcribe] device speech error:', event.error);
    setTranscribing(false);
    setDeviceSpeechRunning(false);
  });

  // 'end' always fires after stop() — commit whatever was captured even if isFinal never came.
  useSpeechRecognitionEvent('end', () => {
    if (latestTranscriptRef.current) {
      setText(latestTranscriptRef.current);
      console.log('[transcribe] device speech end, committing:', latestTranscriptRef.current);
      latestTranscriptRef.current = '';
    }
    setDeviceSpeechRunning(false);
    setTranscribing(false);
  });

  // --- Voice button handlers ---

  const handlePressIn = async () => {
    if (usingDeviceSpeech) {
      try {
        console.log('[transcribe] using device speech recognition path');
        const { granted } = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
        if (!granted) {
          console.log('[transcribe] device speech permission denied');
          return;
        }
        setTranscribing(true);
        setDeviceSpeechRunning(true);
        const lang = SPEECH_RECOGNITION_LANG[language] ?? 'en-US';
        latestTranscriptRef.current = '';
        ExpoSpeechRecognitionModule.start({ lang, interimResults: true, maxAlternatives: 1 });
      } catch (e) {
        console.log('[transcribe] device speech failed:', String(e));
        setTranscribing(false);
        setDeviceSpeechRunning(false);
      }
    } else {
      // Clear previous recording and text immediately so the journalist knows they're starting fresh.
      clearAudio();
      setText('');
      recordingStartedAt.current = Date.now();
      startRecording();
    }
  };

  const handlePressOut = async () => {
    if (usingDeviceSpeech) {
      try {
        ExpoSpeechRecognitionModule.stop();
      } catch (e) {
        console.log('[transcribe] device speech stop failed:', String(e));
        setTranscribing(false);
        setDeviceSpeechRunning(false);
      }
    } else {
      const durationMs = Date.now() - recordingStartedAt.current;
      console.log(`[record] recording duration: ${durationMs}ms`);
      if (durationMs < 1000) {
        await stopRecording();
        clearAudio();
        setShortRecordingHint('Hold longer to record');
        setTimeout(() => setShortRecordingHint(null), 2500);
        return;
      }
      await handleStopRecording();
    }
  };

  const handleStopRecording = async () => {
    const uri = await stopRecording();
    if (!uri) return;
    setTranscribing(true);
    try {
      console.log('[transcribe] trying server path (Gemma 4 E4B)');
      const res = await postTranscribe({ audioUri: uri, language });
      console.log('[transcribe] server path success:', res.text);
      if (res.text) {
        // Text confirms the recording succeeded — hide the chip.
        setText(res.text);
        clearAudio();
      }
      // Empty result: leave chip visible so journalist knows a recording exists.
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      console.log('[transcribe] server path failed, switching to device speech recognition:', msg);
      // Keep chip visible — journalist can see the recording exists and retry.
      setUsingDeviceSpeech(true);
    } finally {
      setTranscribing(false);
    }
  };

  const handleSubmit = async () => {
    const currentText = textRef.current.trim();
    if (!currentText) return;
    console.log('[query] sending to POST /query:', currentText);
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const response = await postQuery({ region, text: currentText, language });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Query failed');
    } finally {
      setLoading(false);
    }
  };

  const voiceButtonLabel = () => {
    if (deviceSpeechRunning || (usingDeviceSpeech && transcribing)) return 'Listening…';
    if (isRecording) return 'Recording…';
    if (transcribing) return 'Transcribing…';
    return 'Hold for Voice';
  };

  const voiceButtonStyle = () => {
    if (deviceSpeechRunning) return [styles.voiceBtn, styles.voiceBtnActive];
    if (isRecording) return [styles.voiceBtn, styles.voiceBtnActive];
    if (transcribing) return [styles.voiceBtn, styles.voiceBtnTranscribing];
    return [styles.voiceBtn];
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

        {/* Device speech fallback indicator */}
        {usingDeviceSpeech && (
          <View style={styles.deviceSpeechPill}>
            <Ionicons name="phone-portrait-outline" size={13} color="#1d4ed8" />
            <Text style={styles.deviceSpeechText}>Using device speech recognition</Text>
          </View>
        )}

        {/* Voice button */}
        <Pressable
          onPressIn={handlePressIn}
          onPressOut={handlePressOut}
          disabled={transcribing && !deviceSpeechRunning}
          style={voiceButtonStyle()}
        >
          <Text style={styles.voiceBtnText}>{voiceButtonLabel()}</Text>
        </Pressable>

        {isRecording && (
          <View style={styles.meterRow}>
            {METER_OFFSETS.map((offset, i) => (
              <View
                key={i}
                style={[styles.meterBar, { height: meterBarHeight(meteringLevel, offset) }]}
              />
            ))}
          </View>
        )}

        {shortRecordingHint && (
          <Text style={styles.shortRecordingHint}>{shortRecordingHint}</Text>
        )}

        {audioUri && !usingDeviceSpeech && (
          <View style={styles.audioPill}>
            <Text style={styles.audioPillText}>Audio recorded</Text>
            <Pressable onPress={() => { clearAudio(); setText(''); }}>
              <Text style={styles.audioRemove}>✕</Text>
            </Pressable>
          </View>
        )}

        {/* Submit */}
        <Pressable
          onPress={handleSubmit}
          disabled={loading || transcribing || isRecording || !text.trim()}
          style={({ pressed }) => [
            styles.submitBtn,
            (loading || transcribing || isRecording || !text.trim()) && styles.submitBtnDisabled,
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
                onPress={() => {
                  setRegion(zone);
                  setDropdownOpen(false);
                  setText('');
                  setResult(null);
                  setError(null);
                  clearAudio();
                  setUsingDeviceSpeech(false);
                  setDeviceSpeechRunning(false);
                }}
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

  deviceSpeechPill: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 10,
    gap: 5,
  },
  deviceSpeechText: { fontSize: 12, color: '#1d4ed8' },

  voiceBtn: {
    marginTop: 12,
    backgroundColor: '#1d4ed8',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
  },
  voiceBtnActive: { backgroundColor: '#dc2626' },
  voiceBtnTranscribing: { backgroundColor: '#d97706' },
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
  meterRow: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    justifyContent: 'center',
    marginTop: 10,
    gap: 4,
    height: 26,
  },
  meterBar: {
    width: 4,
    borderRadius: 2,
    backgroundColor: '#1e293b',
  },
  shortRecordingHint: { marginTop: 6, fontSize: 13, color: '#6b7280', textAlign: 'center' },
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
