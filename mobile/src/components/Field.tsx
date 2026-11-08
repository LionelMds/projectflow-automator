import { StyleSheet, TextInput, View, type TextInputProps } from 'react-native';

import { Caps } from '@/components/Text';
import { colors, fonts } from '@/theme/tokens';

/**
 * A captioned text field (`caption_field` on the desktop). A field whose value
 * differs from what was read is tinted pale green until it is saved, as in the
 * desktop répertoire.
 */
export function Field({
  label,
  dirty = false,
  ...input
}: TextInputProps & { label: string; dirty?: boolean }) {
  return (
    <View style={styles.field}>
      <Caps>{label}</Caps>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor="#7a7f85"
        style={[styles.input, dirty && styles.dirty, input.editable === false && styles.readOnly]}
        {...input}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  field: { gap: 4 },
  input: {
    minHeight: 44,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    fontFamily: fonts.body,
    fontSize: 16,
    color: colors.text,
  },
  dirty: { backgroundColor: colors.dirty, borderColor: colors.dirtyLine },
  readOnly: { opacity: 0.6 },
});
