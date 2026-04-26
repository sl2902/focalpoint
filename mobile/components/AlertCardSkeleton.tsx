import React, { useEffect, useRef } from 'react';
import { Animated, View, StyleSheet } from 'react-native';

export function AlertCardSkeleton() {
  const opacity = useRef(new Animated.Value(0.5)).current;

  useEffect(() => {
    Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.5, duration: 700, useNativeDriver: true }),
      ])
    ).start();
  }, [opacity]);

  return (
    <Animated.View style={[styles.card, { opacity }]}>
      <View style={styles.header}>
        <View style={styles.badge} />
        <View style={styles.region} />
        <View style={styles.time} />
      </View>
      <View style={styles.line} />
      <View style={styles.lineShort} />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#f3f4f6',
    borderRadius: 10,
    padding: 14,
    marginHorizontal: 16,
    marginVertical: 6,
  },
  header: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  badge: { width: 52, height: 20, borderRadius: 4, backgroundColor: '#d1d5db' },
  region: { flex: 1, height: 14, borderRadius: 4, backgroundColor: '#d1d5db' },
  time: { width: 40, height: 12, borderRadius: 4, backgroundColor: '#d1d5db' },
  line: { height: 12, borderRadius: 4, backgroundColor: '#d1d5db', marginBottom: 8 },
  lineShort: { height: 12, borderRadius: 4, backgroundColor: '#d1d5db', width: '70%' },
});
