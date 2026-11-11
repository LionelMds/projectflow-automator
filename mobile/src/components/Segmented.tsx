import { Minus, Plus } from 'lucide-react-native';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

/** One choice among a few, side by side: a status, the morning or the afternoon. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled,
  accessibilityLabel,
}: {
  options: { value: T; label: string }[];
  value: T | null;
  onChange: (value: T) => void;
  disabled?: boolean;
  accessibilityLabel?: string;
}) {
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={accessibilityLabel} style={[styles.segments, disabled && styles.disabled]}>
      {options.map((option, index) => {
        const on = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="radio"
            accessibilityState={{ checked: on, disabled }}
            disabled={disabled}
            onPress={() => !on && onChange(option.value)}
            style={({ pressed }) => [styles.segment, index > 0 && styles.divider, on && styles.on, pressed && !on && styles.pressed]}
          >
            <Text style={[styles.text, on && styles.textOn]} numberOfLines={1}>
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** A value with « − » and « + » around it: a duration, a number of people. */
export function Stepper({
  value,
  onLess,
  onMore,
  lessLabel,
  moreLabel,
  lessDisabled,
  moreDisabled,
}: {
  value: string;
  onLess: () => void;
  onMore: () => void;
  lessLabel: string;
  moreLabel: string;
  lessDisabled?: boolean;
  moreDisabled?: boolean;
}) {
  return (
    <View style={styles.stepper}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={lessLabel}
        disabled={lessDisabled}
        onPress={onLess}
        style={({ pressed }) => [styles.step, pressed && styles.pressed, lessDisabled && styles.disabled]}
      >
        <Minus size={18} color={colors.accent700} strokeWidth={iconStroke} />
      </Pressable>
      <Text style={styles.value} numberOfLines={1}>
        {value}
      </Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={moreLabel}
        disabled={moreDisabled}
        onPress={onMore}
        style={({ pressed }) => [styles.step, pressed && styles.pressed, moreDisabled && styles.disabled]}
      >
        <Plus size={18} color={colors.accent700} strokeWidth={iconStroke} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  segments: { flexDirection: 'row', borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  segment: { flex: 1, minHeight: touch, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4 },
  divider: { borderLeftWidth: StyleSheet.hairlineWidth, borderLeftColor: colors.line },
  on: { backgroundColor: colors.accent700 },
  pressed: { backgroundColor: colors.accent100 },
  disabled: { opacity: 0.4 },
  text: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.6, textTransform: 'uppercase', color: colors.accent700 },
  textOn: { color: '#ffffff' },
  stepper: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  step: { width: touch, height: touch, alignItems: 'center', justifyContent: 'center' },
  value: { flex: 1, textAlign: 'center', fontFamily: fonts.headingBold, fontSize: 17, color: colors.text },
});
