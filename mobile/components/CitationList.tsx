import React from 'react';
import { View, Text, Pressable, StyleSheet, Linking } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import type { Citation } from '../types/api';

interface Props {
  citations: Citation[];
}

type CitationKind =
  | 'link'      // real article URL — blue clickable label
  | 'grounding' // vertexaisearch redirect — greyed out, non-clickable
  | 'source';   // CPJ / RSF / conflict_ / other structured ID

function classify(id: string): CitationKind {
  if (
    id.startsWith('CPJ') ||
    id.startsWith('RSF') ||
    id.startsWith('conflict_') ||
    id.startsWith('FALLBACK')
  ) return 'source';

  if (!id.startsWith('http://') && !id.startsWith('https://')) return 'source';
  if (id.includes('vertexaisearch.cloud.google.com')) return 'grounding';
  return 'link';
}

function CitationRow({ citation, isLast }: { citation: Citation; isLast: boolean }) {
  const kind = classify(citation.id);

  const icon =
    kind === 'link'      ? <Ionicons name="link-outline" size={14} color="#2563eb" /> :
    kind === 'grounding' ? <Ionicons name="earth-outline" size={14} color="#d1d5db" /> :
                           <Ionicons name="document-text-outline" size={14} color="#9ca3af" />;

  const inner = (
    <View style={styles.row}>
      <View style={styles.iconCol}>{icon}</View>
      <Text
        style={[
          styles.description,
          kind === 'link'      && styles.descriptionLink,
          kind === 'grounding' && styles.descriptionMuted,
        ]}
        numberOfLines={3}
      >
        {citation.description}
      </Text>
    </View>
  );

  return (
    <>
      {kind === 'link' ? (
        <Pressable
          onPress={() => Linking.openURL(citation.id)}
          style={({ pressed }) => pressed && styles.pressed}
        >
          {inner}
        </Pressable>
      ) : (
        inner
      )}
      {!isLast && <View style={styles.divider} />}
    </>
  );
}

const MAX_CITATIONS = 5;

export function CitationList({ citations }: Props) {
  const visible = citations.filter((c) => !c.id.startsWith('FALLBACK'));
  if (visible.length === 0) return null;

  const shown = visible.slice(0, MAX_CITATIONS);
  const overflow = visible.length - shown.length;

  return (
    <View style={styles.container}>
      <Text style={styles.heading}>Sources</Text>
      {shown.map((c, i) => (
        <CitationRow key={c.id + i} citation={c} isLast={i === shown.length - 1 && overflow === 0} />
      ))}
      {overflow > 0 && (
        <Text style={styles.overflow}>and {overflow} more source{overflow === 1 ? '' : 's'}</Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { marginTop: 12 },
  heading: {
    fontSize: 12,
    fontWeight: '700',
    color: '#6b7280',
    marginBottom: 8,
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingVertical: 8,
    gap: 8,
  },
  iconCol: {
    marginTop: 1,
    width: 16,
    alignItems: 'center',
  },
  description: {
    flex: 1,
    fontSize: 13,
    color: '#374151',
    lineHeight: 18,
  },
  descriptionLink: {
    color: '#2563eb',
  },
  descriptionMuted: {
    color: '#9ca3af',
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: '#f3f4f6',
    marginLeft: 24,
  },
  pressed: { opacity: 0.7 },
  overflow: {
    fontSize: 12,
    color: '#9ca3af',
    marginTop: 8,
    marginLeft: 24,
  },
});
