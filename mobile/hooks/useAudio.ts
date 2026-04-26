import { useState, useRef, useCallback } from 'react';
import { Platform } from 'react-native';
import { Audio } from 'expo-av';

interface UseAudioResult {
  isRecording: boolean;
  audioUri: string | null;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<string | null>;
  clearAudio: () => void;
  error: string | null;
}

export function useAudio(): UseAudioResult {
  const recordingRef = useRef<Audio.Recording | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [audioUri, setAudioUri] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const startRecording = useCallback(async () => {
    if (Platform.OS === 'web') return;
    setError(null);
    try {
      const { granted } = await Audio.requestPermissionsAsync();
      if (!granted) {
        setError('Microphone permission denied');
        return;
      }
      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      });
      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY,
      );
      recordingRef.current = recording;
      setIsRecording(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Recording failed');
    }
  }, []);

  const stopRecording = useCallback(async (): Promise<string | null> => {
    if (Platform.OS === 'web' || !recordingRef.current) return null;
    try {
      await recordingRef.current.stopAndUnloadAsync();
      const uri = recordingRef.current.getURI();
      recordingRef.current = null;
      setIsRecording(false);
      setAudioUri(uri ?? null);
      return uri ?? null;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Stop recording failed');
      setIsRecording(false);
      return null;
    }
  }, []);

  const clearAudio = useCallback(() => {
    setAudioUri(null);
    setError(null);
  }, []);

  return { isRecording, audioUri, startRecording, stopRecording, clearAudio, error };
}
