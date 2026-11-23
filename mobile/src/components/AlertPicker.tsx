import { Bell } from 'lucide-react-native';
import { useState } from 'react';
import { LayoutAnimation, Pressable, StyleSheet, Text, View } from 'react-native';

import { Chip } from '@/components/Chip';
import { Wheel, WheelBand, type WheelItem } from '@/components/Wheel';
import { selectionFeedback } from '@/lib/haptics';
import { MAX_REMINDERS, PHONE_PRESETS, reminderAt, reminderKey, reminderText, toggledReminder } from '@/lib/planning/alerts';
import { wheelDayLabel } from '@/lib/planning/display';
import { formatInstant, type Instant } from '@/lib/planning/instant';
import type { Reminder } from '@/lib/planning/models';
import { fromWheel, wheelDays, wheelParts, WHEEL_HOURS, WHEEL_MINUTES } from '@/lib/planning/when';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const HOUR_ITEMS: WheelItem[] = WHEEL_HOURS.map((hour) => ({ key: String(hour), label: String(hour).padStart(2, '0') }));
const MINUTE_ITEMS: WheelItem[] = WHEEL_MINUTES.map((minute) => ({ key: String(minute), label: String(minute).padStart(2, '0') }));
const DEFAULT_HOUR = 7;

function presetLabel(minutes: number): string {
  return reminderText({ kind: 'before', minutes });
}

/**
 * « Alerte notification » of a task: the usual times before its start as chips to switch on
 * and off, the alerts set elsewhere (another delay, a set moment) as chips to remove, and
 * « À un moment précis » which opens the wheels of a day and a time.
 */
export function AlertPicker({
  reminders,
  start,
  today,
  disabled = false,
  onChange,
}: {
  reminders: readonly Reminder[];
  /** Start of the task, null while it is not on the planning. */
  start: Instant | null;
  today: number;
  disabled?: boolean;
  onChange: (next: Reminder[]) => void;
}) {
  const [moment, setMoment] = useState<Instant | null>(null);
  const keys = new Set(reminders.map(reminderKey));
  const full = reminders.length >= MAX_REMINDERS;
  // Set on the desktop or at a moment: not among the chips offered here.
  const others = reminders.filter((reminder) => reminderAt(reminder) !== null || !PHONE_PRESETS.includes(reminder.minutes ?? 0));
  const waiting = start === null && reminders.some((reminder) => reminderAt(reminder) === null);

  const toggle = (reminder: Reminder) => {
    selectionFeedback();
    onChange(toggledReminder(reminders, reminder));
  };
  const openMoment = () => {
    LayoutAnimation.configureNext(LayoutAnimation.create(220, 'easeInEaseOut', 'opacity'));
    // A likely moment: an hour before the start, else tomorrow at 07:00.
    setMoment((current) => (current !== null ? null : start !== null ? start - 60 : fromWheel(today + 1, DEFAULT_HOUR, 0)));
  };

  return (
    <View style={styles.picker}>
      <View style={styles.chips}>
        {PHONE_PRESETS.map((minutes) => {
          const reminder: Reminder = { kind: 'before', minutes };
          const on = keys.has(reminderKey(reminder));
          return (
            <Chip
              key={minutes}
              label={presetLabel(minutes)}
              hint={on ? 'alerte active, toucher pour la retirer' : 'ajouter cette alerte'}
              on={on}
              disabled={disabled || (!on && full)}
              onPress={() => toggle(reminder)}
            />
          );
        })}
        {others.map((reminder) => (
          <Chip
            key={reminderKey(reminder)}
            label={`${reminderText(reminder, today)}  ×`}
            hint="toucher pour retirer cette alerte"
            on
            disabled={disabled}
            onPress={() => toggle(reminder)}
          />
        ))}
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: moment !== null, disabled: disabled || full }}
        accessibilityLabel="Alerte à un moment précis"
        disabled={disabled || (full && moment === null)}
        onPress={openMoment}
        style={({ pressed }) => [styles.add, pressed && styles.pressed, (disabled || (full && moment === null)) && styles.disabled]}
      >
        <Bell size={16} color={colors.accent700} strokeWidth={iconStroke} />
        <Text style={styles.addText}>{moment === null ? 'À un moment précis…' : 'Annuler'}</Text>
      </Pressable>
      {moment !== null ? (
        <View style={styles.moment}>
          <MomentWheels value={moment} today={today} onChange={setMoment} />
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              onChange(toggledReminder(reminders.filter((reminder) => reminderKey(reminder) !== `a${formatInstant(moment)}`), { kind: 'at', at: formatInstant(moment) }));
              selectionFeedback();
              setMoment(null);
            }}
            style={({ pressed }) => [styles.confirm, pressed && styles.confirmPressed]}
          >
            <Text style={styles.confirmText}>Ajouter l’alerte</Text>
          </Pressable>
        </View>
      ) : null}
      {waiting ? <Text style={styles.note}>« Avant le début » sonnera une fois la tâche posée sur le planning.</Text> : null}
      {full ? <Text style={styles.note}>Au plus {MAX_REMINDERS} alertes par tâche.</Text> : null}
    </View>
  );
}

function MomentWheels({ value, today, onChange }: { value: Instant; today: number; onChange: (value: Instant) => void }) {
  const parts = wheelParts(value);
  // The days are set when the wheels open: they hold the day shown whatever is turned after.
  const [days] = useState(() => {
    const { first, count } = wheelDays(today, parts.day);
    const items: WheelItem[] = Array.from({ length: count }, (_, index) => ({ key: String(first + index), label: wheelDayLabel(first + index, today) }));
    return { first, items };
  });
  return (
    <View style={styles.wheels}>
      <WheelBand />
      <Wheel
        accessibilityLabel="Alerte : jour"
        items={days.items}
        index={parts.day - days.first}
        onChange={(index) => onChange(fromWheel(days.first + index, parts.hour, parts.minute))}
        align="right"
        flex={2.2}
      />
      <Wheel accessibilityLabel="Alerte : heure" items={HOUR_ITEMS} index={parts.hour} onChange={(hour) => onChange(fromWheel(parts.day, hour, parts.minute))} align="right" />
      <Wheel
        accessibilityLabel="Alerte : minutes"
        items={MINUTE_ITEMS}
        index={parts.minute === 30 ? 1 : 0}
        onChange={(index) => onChange(fromWheel(parts.day, parts.hour, WHEEL_MINUTES[index]))}
        align="left"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  picker: { gap: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  add: { flexDirection: 'row', alignItems: 'center', gap: 6, minHeight: touch, alignSelf: 'flex-start' },
  addText: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.accent700 },
  pressed: { opacity: 0.6 },
  disabled: { opacity: 0.45 },
  moment: { borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface, paddingHorizontal: 12, paddingBottom: 8 },
  wheels: { flexDirection: 'row', marginVertical: 4 },
  confirm: { minHeight: touch, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.accent700 },
  confirmPressed: { backgroundColor: colors.accent800 },
  confirmText: { fontFamily: fonts.heading, fontSize: 16, letterSpacing: 1, textTransform: 'uppercase', color: colors.surface },
  note: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
});
