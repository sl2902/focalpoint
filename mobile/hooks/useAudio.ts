import { useState, useCallback } from 'react';
import { Platform } from 'react-native';
import {
  useAudioRecorder,
  useAudioRecorderState,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  RecordingPresets,
} from 'expo-audio';

const RECORDING_OPTIONS = {
  ...RecordingPresets.HIGH_QUALITY,
  isMeteringEnabled: true,
};

interface UseAudioResult {
  isRecording: boolean;
  audioUri: string | null;
  meteringLevel: number | null;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<string | null>;
  clearAudio: () => void;
  error: string | null;
}

export function useAudio(): UseAudioResult {
  const recorder = useAudioRecorder(RECORDING_OPTIONS);
  // Polls recorder state every 100ms — drives metering re-renders while recording.
  const recorderState = useAudioRecorderState(recorder, 100);
  const [isRecording, setIsRecording] = useState(false);
  const [audioUri, setAudioUri] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const startRecording = useCallback(async () => {
    if (Platform.OS === 'web') return;
    setError(null);
    try {
      const { granted } = await requestRecordingPermissionsAsync();
      if (!granted) {
        setError('Microphone permission denied');
        return;
      }
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      setIsRecording(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Recording failed');
    }
  }, [recorder]);

  const stopRecording = useCallback(async (): Promise<string | null> => {
    if (Platform.OS === 'web') return null;
    try {
      await recorder.stop();
      const uri = recorder.uri;
      setIsRecording(false);
      setAudioUri(uri ?? null);
      return uri ?? null;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stop recording failed');
      setIsRecording(false);
      return null;
    }
  }, [recorder]);

  const clearAudio = useCallback(() => {
    setAudioUri(null);
    setError(null);
  }, []);

  const meteringLevel =
    recorderState.isRecording && recorderState.metering !== undefined
      ? recorderState.metering
      : null;

  return { isRecording, audioUri, meteringLevel, startRecording, stopRecording, clearAudio, error };
}
