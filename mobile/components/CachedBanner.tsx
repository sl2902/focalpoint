import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

interface Props {
  lastFetchedAt: Date | null;
}

export function CachedBanner({ lastFetchedAt }: Props) {
  const timeLabel = lastFetchedAt
    ? lastFetchedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : 'unknown';

  return (
    <View style={styles.banner}>
      <Text style={styles.text}>
        CACHED — last updated {timeLabel}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    backgroundColor: '#fef9c3',
    borderBottomWidth: 1,
    borderBottomColor: '#fde047',
    paddingVertical: 6,
    paddingHorizontal: 16,
    alignItems: 'center',
  },
  text: {
    fontSize: 12,
    fontWeight: '600',
    color: '#854d0e',
    letterSpacing: 0.3,
  },
});
