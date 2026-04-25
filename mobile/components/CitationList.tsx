import React from 'react';
import { View, Text, StyleSheet, Linking } from 'react-native';
import type { Citation } from '../types/api';

interface Props {
  citations: Citation[];
}

function isUrl(id: string): boolean {
  return id.startsWith('http://') || id.startsWith('https://');
}

export function CitationList({ citations }: Props) {
  if (citations.length === 0) return null;

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Sources</Text>
      {citations.map((c, i) => (
        <View key={c.id + i} style={styles.row}>
          <Text style={styles.bullet}>•</Text>
          <View style={styles.content}>
            <Text
              style={[styles.id, isUrl(c.id) && styles.link]}
              onPress={isUrl(c.id) ? () => Linking.openURL(c.id) : undefined}
            >
              {c.id}
            </Text>
            <Text style={styles.description}>{c.description}</Text>
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: 12 },
  heading: {
    fontSize: 13,
    fontWeight: '700',
    color: '#374151',
    marginBottom: 6,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  row: {
    flexDirection: 'row',
    marginBottom: 6,
  },
  bullet: {
    color: '#9ca3af',
    marginRight: 6,
    marginTop: 1,
  },
  content: { flex: 1 },
  id: {
    fontSize: 12,
    color: '#6b7280',
    fontFamily: 'monospace',
  },
  link: {
    color: '#2563eb',
    textDecorationLine: 'underline',
  },
  description: {
    fontSize: 13,
    color: '#374151',
    marginTop: 1,
  },
});
