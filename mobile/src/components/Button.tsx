import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';

import { CornerMarks } from '@/components/Blueprint';
import { colors, fonts, touch } from '@/theme/tokens';

type Variant = 'primary' | 'secondary';

/**
 * Square-cornered action. The primary one is the single solid object of a screen
 * and wears the corner marks, like `PrimaryButton` on the desktop.
 */
export function Button({
  label,
  onPress,
  variant = 'secondary',
  disabled = false,
  busy = false,
  icon,
  style,
  accessibilityHint,
}: {
  label: string;
  onPress: () => void;
  variant?: Variant;
  disabled?: boolean;
  busy?: boolean;
  icon?: ReactNode;
  style?: StyleProp<ViewStyle>;
  accessibilityHint?: string;
}) {
  const primary = variant === 'primary';
  const inactive = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: inactive, busy }}
      disabled={inactive}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        primary ? styles.primary : styles.secondary,
        pressed && (primary ? styles.primaryPressed : styles.secondaryPressed),
        inactive && styles.disabled,
        style,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={primary ? '#ffffff' : colors.accent700} />
      ) : (
        <View style={styles.content}>
          {icon}
          <Text style={[styles.label, primary && styles.primaryLabel]}>{label}</Text>
        </View>
      )}
      {primary ? <CornerMarks /> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: touch,
    paddingHorizontal: 14,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
  },
  content: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  primary: { backgroundColor: colors.accent700, borderColor: colors.accent700 },
  primaryPressed: { backgroundColor: colors.accent800 },
  secondary: { backgroundColor: 'transparent', borderColor: colors.line },
  secondaryPressed: { backgroundColor: colors.accent100 },
  disabled: { opacity: 0.45 },
  label: {
    fontFamily: fonts.heading,
    fontSize: 15,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
    color: colors.accent700,
  },
  primaryLabel: { color: '#ffffff' },
});
