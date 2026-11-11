import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';

import { colors, fonts, touch } from '@/theme/tokens';

/**
 * A checkbox or radio row of the Industry forms (`.check` of the design): square box,
 * deep accent when on, the whole row as the touch target.
 */
export function Choice({
  label,
  detail,
  checked,
  onPress,
  kind = 'check',
  disabled = false,
  trailing,
  style,
}: {
  label: string;
  detail?: string;
  checked: boolean;
  onPress?: () => void;
  kind?: 'check' | 'radio';
  disabled?: boolean;
  trailing?: ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const radio = kind === 'radio';
  return (
    <Pressable
      accessibilityRole={radio ? 'radio' : 'checkbox'}
      accessibilityState={{ checked, disabled }}
      accessibilityLabel={detail ? `${label}, ${detail}` : label}
      disabled={disabled || !onPress}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && styles.pressed, disabled && styles.disabled, style]}
    >
      <View style={[styles.box, radio && styles.round, checked && styles.checked]}>
        {checked ? radio ? <View style={styles.dot} /> : <View style={styles.tick} /> : null}
      </View>
      <View style={styles.texts}>
        <Text style={styles.label}>{label}</Text>
        {detail ? <Text style={styles.detail}>{detail}</Text> : null}
      </View>
      {trailing}
    </Pressable>
  );
}

/** A small selectable tag: bucket, member, suggestion. */
export function Chip({
  label,
  selected = false,
  onPress,
  disabled = false,
}: {
  label: string;
  selected?: boolean;
  onPress: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected, disabled }}
      accessibilityLabel={label}
      disabled={disabled}
      onPress={onPress}
      hitSlop={{ top: 4, bottom: 4 }}
      style={({ pressed }) => [styles.chip, selected && styles.chipSelected, pressed && styles.pressed, disabled && styles.disabled]}
    >
      <Text style={[styles.chipText, selected && styles.chipTextSelected]} numberOfLines={1}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: touch, paddingVertical: 6 },
  pressed: { backgroundColor: colors.accent100 },
  disabled: { opacity: 0.5 },
  box: {
    width: 22,
    height: 22,
    borderWidth: 1,
    borderColor: colors.accent,
    backgroundColor: colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  round: { borderRadius: 11 },
  checked: { backgroundColor: colors.accent700, borderColor: colors.accent700 },
  tick: {
    width: 6,
    height: 11,
    marginTop: -2,
    borderRightWidth: 2,
    borderBottomWidth: 2,
    borderColor: '#ffffff',
    transform: [{ rotate: '45deg' }],
  },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#ffffff' },
  texts: { flex: 1, gap: 2 },
  label: { fontFamily: fonts.body, fontSize: 15, color: colors.text },
  detail: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
  chip: {
    minHeight: 36,
    paddingHorizontal: 10,
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    maxWidth: '100%',
  },
  chipSelected: { backgroundColor: colors.accent700, borderColor: colors.accent700 },
  chipText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.text },
  chipTextSelected: { color: '#ffffff' },
});
