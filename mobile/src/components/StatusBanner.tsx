import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import { Button } from '@/components/Button';
import { colors, fonts } from '@/theme/tokens';

export type BannerState =
  | { kind: 'progress'; message: string }
  | { kind: 'success'; message: string; detail?: string }
  | { kind: 'error'; message: string };

/**
 * The `StatusBanner` of the desktop forms: progress while an operation runs, then
 * its success with follow-up actions, or its error.
 */
export function StatusBanner({
  state,
  actions = [],
}: {
  state: BannerState;
  actions?: { label: string; onPress: () => void }[];
}) {
  const tone = state.kind;
  return (
    <View
      accessibilityLiveRegion="polite"
      style={[styles.box, tone === 'success' && styles.success, tone === 'error' && styles.error]}
    >
      <View style={styles.row}>
        {tone === 'progress' ? <ActivityIndicator color={colors.accent700} /> : <View style={[styles.mark, tone === 'error' && styles.markError]} />}
        <View style={styles.texts}>
          <Text accessibilityRole={tone === 'error' ? 'alert' : undefined} style={[styles.message, tone === 'error' && styles.errorText]}>
            {state.message}
          </Text>
          {state.kind === 'success' && state.detail ? <Text style={styles.detail}>{state.detail}</Text> : null}
          {tone === 'progress' ? <Text style={styles.detail}>Les actions reprennent à la fin de l’opération</Text> : null}
        </View>
      </View>
      {actions.length ? (
        <View style={styles.actions}>
          {actions.map((action) => (
            <Button key={action.label} label={action.label} onPress={action.onPress} style={styles.action} />
          ))}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { padding: 14, gap: 12, borderWidth: 1, borderColor: colors.accent, backgroundColor: colors.accent100 },
  success: { borderColor: colors.ok, backgroundColor: '#e8f3ec' },
  error: { borderColor: colors.err, backgroundColor: colors.errSoft },
  row: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  mark: { width: 10, height: 10, marginTop: 6, backgroundColor: colors.ok },
  markError: { backgroundColor: colors.err },
  texts: { flex: 1, gap: 2 },
  message: { fontFamily: fonts.bodySemiBold, fontSize: 15, lineHeight: 21, color: colors.text },
  errorText: { color: colors.err },
  detail: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  action: { flexGrow: 1, backgroundColor: colors.surface },
});
