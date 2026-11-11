import { StyleSheet, Text, View } from 'react-native';

import { Blueprint } from '@/components/Blueprint';
import { colors, fonts } from '@/theme/tokens';

export type JournalEntry = { time: string; message: string };

export function journalEntry(message: string, now: Date = new Date()): JournalEntry {
  const two = (value: number) => String(value).padStart(2, '0');
  return { time: `${two(now.getHours())}:${two(now.getMinutes())}:${two(now.getSeconds())}`, message };
}

/**
 * The journal of the desktop creation tab: one line per step, « ! » in red,
 * « -> » muted, the latest at the bottom.
 */
export function Journal({ entries, empty }: { entries: JournalEntry[]; empty: string }) {
  const count = entries.length;
  return (
    <Blueprint style={styles.box} marks={false}>
      <View style={styles.head}>
        <Text accessibilityRole="header" style={styles.title}>
          Journal
        </Text>
        <Text style={styles.count}>{count ? `${count} entrée${count > 1 ? 's' : ''}` : '0 opération'}</Text>
      </View>
      {count === 0 ? (
        <Text style={styles.empty}>{empty}</Text>
      ) : (
        entries.map((entry, index) => {
          const error = entry.message.startsWith('!');
          const note = entry.message.startsWith('->');
          return (
            <View key={index} style={styles.line}>
              <Text style={styles.time}>{entry.time}</Text>
              <Text selectable style={[styles.message, error && styles.error, note && styles.note]}>
                {entry.message}
              </Text>
            </View>
          );
        })
      )}
    </Blueprint>
  );
}

const styles = StyleSheet.create({
  box: { paddingHorizontal: 16, paddingVertical: 14, gap: 6 },
  head: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  title: { fontFamily: fonts.headingBold, fontSize: 16, letterSpacing: 1, textTransform: 'uppercase', color: colors.text },
  count: { fontFamily: fonts.body, fontSize: 12, color: colors.muted },
  empty: { fontFamily: fonts.body, fontSize: 14, color: colors.muted },
  line: { flexDirection: 'row', gap: 10 },
  time: { fontFamily: fonts.body, fontSize: 13, lineHeight: 19, color: colors.muted, width: 58 },
  message: { flex: 1, fontFamily: fonts.body, fontSize: 14, lineHeight: 19, color: colors.text },
  error: { color: colors.err },
  note: { color: colors.subtle },
});
