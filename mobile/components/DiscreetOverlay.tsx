import React from 'react';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import { useDiscreetStore } from '../store/useDiscreetStore';

/**
 * Full-screen dark overlay rendered in app/_layout.tsx (root layout).
 * Shown when discreetMode is active — hides all screen content.
 * Tap 3× rapidly to dismiss (panic exit for field use).
 */
export function DiscreetOverlay() {
  const { discreetMode, setDiscreetMode } = useDiscreetStore();
  const tapCountRef = React.useRef(0);
  const tapTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  if (!discreetMode) return null;

  const handleTap = () => {
    tapCountRef.current += 1;
    if (tapTimerRef.current) clearTimeout(tapTimerRef.current);
    tapTimerRef.current = setTimeout(() => {
      tapCountRef.current = 0;
    }, 800);

    if (tapCountRef.current >= 3) {
      tapCountRef.current = 0;
      setDiscreetMode(false);
    }
  };

  return (
    <Pressable style={styles.overlay} onPress={handleTap}>
      <View style={styles.hint}>
        <Text style={styles.hintText}>Tap 3× to exit discreet mode</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  overlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#000',
    zIndex: 999,
    justifyContent: 'flex-end',
    alignItems: 'center',
    paddingBottom: 40,
  },
  hint: {
    opacity: 0.15,
  },
  hintText: {
    color: '#fff',
    fontSize: 12,
  },
});
