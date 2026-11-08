import { StyleSheet, Text, View } from 'react-native';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { colors, fonts } from '@/theme/tokens';

/** A framed message with an optional action: sign in, configure, retry. */
export function Notice({
  message,
  tone = 'info',
  action,
}: {
  message: string;
  tone?: 'info' | 'error';
  action?: { label: string; onPress: () => void };
}) {
  const error = tone === 'error';
  return (
    <Blueprint style={[styles.box, error && styles.error]} marks={!error}>
      <Text accessibilityRole={error ? 'alert' : undefined} style={[styles.text, error && styles.errorText]}>
        {message}
      </Text>
      {action ? (
        <View style={styles.action}>
          <Button label={action.label} onPress={action.onPress} />
        </View>
      ) : null}
    </Blueprint>
  );
}

const styles = StyleSheet.create({
  box: { padding: 14, gap: 12 },
  error: { borderColor: colors.err, backgroundColor: colors.errSoft },
  text: { fontFamily: fonts.body, fontSize: 15, lineHeight: 21, color: colors.text },
  errorText: { color: colors.err },
  action: { alignSelf: 'flex-start' },
});
