import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, fonts, touch } from '@/theme/tokens';

/** A square toggle: status, half-day, person, task type (with its colour). */
export function Chip({
  label,
  on,
  onPress,
  disabled,
  color,
  hint,
}: {
  label: string;
  on: boolean;
  onPress: () => void;
  disabled?: boolean;
  color?: string;
  hint?: string;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected: on, disabled }}
      accessibilityLabel={hint ? `${label}, ${hint}` : label}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.chip,
        color ? { borderColor: color } : null,
        on && (color ? { backgroundColor: `${color}33` } : styles.chipOn),
        disabled && styles.disabled,
      ]}
    >
      {color ? <View style={[styles.swatch, { backgroundColor: color }]} /> : null}
      <Text style={[styles.chipText, on && styles.chipTextOn]}>{label}</Text>
    </Pressable>
  );
}

export function IconButton({ label, onPress, disabled, children }: { label: string; onPress: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={disabled}
      onPress={onPress}
      style={[styles.iconButton, disabled && styles.disabled]}
    >
      {children}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    minHeight: touch,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  chipOn: { backgroundColor: colors.accent100, borderColor: colors.accent700 },
  chipText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.5, color: colors.subtle },
  chipTextOn: { color: colors.accent900 },
  swatch: { width: 10, height: 10 },
  disabled: { opacity: 0.45 },
  iconButton: {
    width: touch,
    height: touch,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
});
