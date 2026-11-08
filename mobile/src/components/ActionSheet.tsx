import { Check } from 'lucide-react-native';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ActivityIndicator, Animated, Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { colors, fonts, touch } from '@/theme/tokens';

export type SheetAction = {
  key: string;
  label: string;
  /** Drawn in the colour of the row (accent, or red for a destructive action). */
  icon?: (color: string) => ReactNode;
  detail?: string;
  destructive?: boolean;
  disabled?: boolean;
  /** A choice of a picker: ticked when selected. */
  selected?: boolean;
  /** Pickers stay open between choices; other actions close the sheet. */
  keepOpen?: boolean;
  /** A promise keeps the row busy until it settles; a failure is shown in the sheet. */
  onPress: () => void | Promise<unknown>;
};

export type SheetSection = {
  title?: string;
  /** « segments »: one row of equal buttons (a status). */
  layout?: 'list' | 'segments';
  actions: SheetAction[];
};

type Props = {
  visible: boolean;
  kicker?: string;
  title?: string;
  subtitle?: string;
  /** Colour of the band over the header (the task type). */
  accent?: string;
  sections: SheetSection[];
  onClose: () => void;
  /** Label of the closing button (« Annuler », « Terminé » for a picker). */
  closeLabel?: string;
};

/**
 * The menu of a long press, from the bottom of the screen: a header naming what it acts
 * on, then its actions grouped as the desktop's context menus are, the destructive one last.
 */
export function ActionSheet({ visible, kicker, title, subtitle, accent, sections, onClose, closeLabel = 'Annuler' }: Props) {
  const insets = useSafeAreaInsets();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [shift] = useState(() => new Animated.Value(1));
  const translateY = useMemo(() => shift.interpolate({ inputRange: [0, 1], outputRange: [0, 320] }), [shift]);

  useEffect(() => {
    if (!visible) return;
    shift.setValue(1);
    Animated.spring(shift, { toValue: 0, useNativeDriver: true, bounciness: 0, speed: 18 }).start();
  }, [visible, shift]);

  const close = () => {
    setError('');
    setPending(null);
    onClose();
  };

  const run = async (action: SheetAction) => {
    if (action.disabled || pending) return;
    setError('');
    const result = action.onPress();
    if (!(result instanceof Promise)) {
      if (!action.keepOpen) close();
      return;
    }
    setPending(action.key);
    try {
      await result;
      setPending(null);
      if (!action.keepOpen) close();
    } catch (exc) {
      setPending(null);
      setError(exc instanceof Error ? exc.message : 'Action impossible.');
    }
  };

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close} statusBarTranslucent>
      <Pressable accessibilityLabel="Fermer le menu" style={styles.backdrop} onPress={close} />
      <Animated.View style={[styles.sheet, { paddingBottom: insets.bottom + 10, transform: [{ translateY }] }]}>
        {accent ? <View style={[styles.band, { backgroundColor: accent }]} /> : null}
        {kicker || title || subtitle ? (
          <View style={styles.header}>
            {kicker ? <Text style={styles.kicker}>{kicker}</Text> : null}
            {title ? (
              <Text style={styles.title} numberOfLines={2}>
                {title}
              </Text>
            ) : null}
            {subtitle ? (
              <Text style={styles.subtitle} numberOfLines={2}>
                {subtitle}
              </Text>
            ) : null}
          </View>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
        <ScrollView style={styles.body} contentContainerStyle={styles.bodyContent} bounces={false}>
          {sections
            .filter((section) => section.actions.length)
            .map((section, index) => (
              <View key={section.title ?? index} style={styles.section}>
                {section.title ? <Text style={styles.sectionTitle}>{section.title}</Text> : null}
                {section.layout === 'segments' ? (
                  <View style={styles.segments}>
                    {section.actions.map((action) => (
                      <Pressable
                        key={action.key}
                        accessibilityRole="button"
                        accessibilityState={{ selected: action.selected, disabled: action.disabled }}
                        disabled={action.disabled}
                        onPress={() => void run(action)}
                        style={({ pressed }) => [
                          styles.segment,
                          action.selected && styles.segmentOn,
                          pressed && styles.pressed,
                          action.disabled && styles.disabled,
                        ]}
                      >
                        {pending === action.key ? (
                          <ActivityIndicator color={colors.accent700} />
                        ) : (
                          <Text style={[styles.segmentText, action.selected && styles.segmentTextOn]} numberOfLines={1}>
                            {action.label}
                          </Text>
                        )}
                      </Pressable>
                    ))}
                  </View>
                ) : (
                  <View style={styles.list}>
                    {section.actions.map((action) => {
                      const tint = action.destructive ? colors.err : colors.accent700;
                      return (
                        <Pressable
                          key={action.key}
                          accessibilityRole="button"
                          accessibilityState={{ selected: action.selected, disabled: action.disabled }}
                          accessibilityLabel={action.detail ? `${action.label}, ${action.detail}` : action.label}
                          disabled={action.disabled}
                          onPress={() => void run(action)}
                          style={({ pressed }) => [styles.row, pressed && styles.pressed, action.disabled && styles.disabled]}
                        >
                          {action.icon ? <View style={styles.icon}>{action.icon(tint)}</View> : null}
                          <View style={styles.rowText}>
                            <Text style={[styles.label, action.destructive && styles.destructive]} numberOfLines={1}>
                              {action.label}
                            </Text>
                            {action.detail ? (
                              <Text style={styles.detail} numberOfLines={1}>
                                {action.detail}
                              </Text>
                            ) : null}
                          </View>
                          {pending === action.key ? <ActivityIndicator color={tint} /> : null}
                          {action.selected ? <Check size={20} color={colors.accent700} strokeWidth={2.4} /> : null}
                        </Pressable>
                      );
                    })}
                  </View>
                )}
              </View>
            ))}
        </ScrollView>
        <Pressable accessibilityRole="button" onPress={close} style={({ pressed }) => [styles.close, pressed && styles.pressed]}>
          <Text style={styles.closeText}>{closeLabel}</Text>
        </Pressable>
      </Animated.View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { ...StyleSheet.absoluteFill, backgroundColor: 'rgba(29,31,32,0.38)' },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    maxHeight: '86%',
    backgroundColor: colors.bg,
    borderTopWidth: 1,
    borderTopColor: colors.line,
  },
  band: { height: 4 },
  header: { paddingHorizontal: 20, paddingTop: 14, paddingBottom: 10, gap: 2, borderBottomWidth: 1, borderBottomColor: colors.line },
  kicker: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.8, color: colors.accent700 },
  title: { fontFamily: fonts.headingBold, fontSize: 21, textTransform: 'uppercase', color: colors.text },
  subtitle: { fontFamily: fonts.body, fontSize: 14, color: colors.muted },
  error: {
    marginHorizontal: 16,
    marginTop: 10,
    padding: 10,
    fontFamily: fonts.body,
    fontSize: 14,
    color: colors.err,
    backgroundColor: colors.errSoft,
  },
  body: { flexGrow: 0 },
  bodyContent: { paddingVertical: 6 },
  section: { paddingHorizontal: 12, paddingTop: 8, gap: 6 },
  sectionTitle: {
    paddingHorizontal: 8,
    fontFamily: fonts.heading,
    fontSize: 12,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
    color: colors.muted,
  },
  list: { borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: touch + 6,
    paddingHorizontal: 14,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.line,
  },
  icon: { width: 22, alignItems: 'center' },
  rowText: { flex: 1, paddingVertical: 8 },
  label: { fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.text },
  destructive: { color: colors.err },
  detail: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
  segments: { flexDirection: 'row', borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  segment: {
    flex: 1,
    minHeight: touch,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 4,
    borderLeftWidth: StyleSheet.hairlineWidth,
    borderLeftColor: colors.line,
  },
  segmentOn: { backgroundColor: colors.accent700 },
  segmentText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.6, textTransform: 'uppercase', color: colors.accent700 },
  segmentTextOn: { color: '#ffffff' },
  pressed: { backgroundColor: colors.accent100 },
  disabled: { opacity: 0.4 },
  close: {
    marginHorizontal: 12,
    marginTop: 12,
    minHeight: touch,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
  },
  closeText: { fontFamily: fonts.headingBold, fontSize: 16, letterSpacing: 1, textTransform: 'uppercase', color: colors.accent700 },
});
