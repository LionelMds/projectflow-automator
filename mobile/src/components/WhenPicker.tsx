import { useState } from 'react';
import { LayoutAnimation, Pressable, StyleSheet, Text, View } from 'react-native';

import { Caps } from '@/components/Text';
import { Wheel, WheelBand, type WheelItem } from '@/components/Wheel';
import type { WorkCalendar } from '@/lib/planning/calendar';
import { wheelDayLabel } from '@/lib/planning/display';
import { formatHhmm, type Instant } from '@/lib/planning/instant';
import { formatSpan } from '@/lib/planning/quickEntry';
import { fromWheel, wheelDays, wheelParts, whenNotes, WHEEL_HOURS, WHEEL_MINUTES, type When } from '@/lib/planning/when';
import { colors, fonts, touch } from '@/theme/tokens';

type Edge = 'start' | 'end';

const HOUR_ITEMS: WheelItem[] = WHEEL_HOURS.map((hour) => ({
  key: String(hour),
  label: String(hour).padStart(2, '0'),
  // The workshop works from 06:30 to 16:30.
  muted: hour < 6 || hour > 16,
}));
const MINUTE_ITEMS: WheelItem[] = WHEEL_MINUTES.map((minute) => ({ key: String(minute), label: String(minute).padStart(2, '0') }));

/**
 * « Début » and « Fin » as the iPhone's Calendar shows them: a touched row opens its wheels
 * (day, hour, minutes) under it, the other one closes. The start moves the end with it.
 */
export function WhenPicker({
  value,
  onChange,
  calendar,
  today,
  disabled = false,
  reveal = null,
}: {
  value: When;
  /** The new span, and the edge turned. */
  onChange: (next: When, edge: Edge) => void;
  calendar: WorkCalendar;
  today: number;
  disabled?: boolean;
  /** The edge being written elsewhere (a text read as it is typed): its wheels open, and turn with `value`. */
  reveal?: Edge | null;
}) {
  const [open, setOpen] = useState<Edge | null>(reveal);
  // Opened by what is being written, never closed by it: the hand may be on the wheels.
  const [revealed, setRevealed] = useState(reveal);
  if (reveal !== revealed) {
    setRevealed(reveal);
    if (reveal) setOpen(reveal);
  }
  const toggle = (edge: Edge) => {
    LayoutAnimation.configureNext(LayoutAnimation.create(220, 'easeInEaseOut', 'opacity'));
    setOpen((current) => (current === edge ? null : edge));
  };
  const backwards = value.end <= value.start;
  const notes = whenNotes(calendar, value);

  return (
    <View style={styles.picker}>
      <EdgeRow
        label="Début"
        value={value.start}
        today={today}
        open={open === 'start'}
        disabled={disabled}
        onPress={() => toggle('start')}
      />
      {open === 'start' && !disabled ? (
        <InstantWheels
          label="Début"
          value={value.start}
          today={today}
          calendar={calendar}
          onChange={(start) => onChange({ start, end: value.end }, 'start')}
        />
      ) : null}
      <EdgeRow
        label="Fin"
        value={value.end}
        today={today}
        open={open === 'end'}
        disabled={disabled}
        struck={backwards}
        onPress={() => toggle('end')}
      />
      {open === 'end' && !disabled ? (
        <InstantWheels
          label="Fin"
          value={value.end}
          today={today}
          calendar={calendar}
          onChange={(end) => onChange({ start: value.start, end }, 'end')}
        />
      ) : null}
      {!backwards ? <Text style={styles.length}>Durée : {formatSpan(calendar, value.start, value.end)} de travail</Text> : null}
      {notes.map((note) => (
        <Text key={note} style={styles.note}>
          ▲ {note}
        </Text>
      ))}
    </View>
  );
}

function EdgeRow({
  label,
  value,
  today,
  open,
  disabled,
  struck = false,
  onPress,
}: {
  label: string;
  value: Instant;
  today: number;
  open: boolean;
  disabled: boolean;
  struck?: boolean;
  onPress: () => void;
}) {
  const { day, hour, minute } = wheelParts(value);
  const date = wheelDayLabel(day, today);
  const time = formatHhmm(hour * 60 + minute);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${label} : ${date} ${time}`}
      accessibilityHint={disabled ? undefined : open ? 'Ferme les roues' : 'Ouvre les roues du jour et de l’heure'}
      accessibilityState={{ expanded: open, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={styles.edge}
    >
      <Caps style={styles.edgeLabel}>{label}</Caps>
      <View style={styles.values}>
        <Text style={[styles.value, open && styles.valueOpen, struck && styles.struck]}>{date}</Text>
        <Text style={[styles.value, styles.time, open && styles.valueOpen, struck && styles.struck]}>{time}</Text>
      </View>
    </Pressable>
  );
}

/** The day, hour and minutes wheels of one instant, side by side behind one band. */
function InstantWheels({
  label,
  value,
  today,
  calendar,
  onChange,
}: {
  label: string;
  value: Instant;
  today: number;
  calendar: WorkCalendar;
  onChange: (value: Instant) => void;
}) {
  const parts = wheelParts(value);
  // The days are set when the wheels open: they hold the day shown whatever is turned after.
  // A day set from outside them (a date typed) that they do not hold sets them again.
  const daysAround = (shown: number) => {
    const { first, count } = wheelDays(today, shown);
    const items: WheelItem[] = Array.from({ length: count }, (_, index) => {
      const day = first + index;
      return { key: String(day), label: wheelDayLabel(day, today), muted: !calendar.isWorkday(day) };
    });
    return { first, items };
  };
  const [days, setDays] = useState(() => daysAround(parts.day));
  if (parts.day < days.first || parts.day >= days.first + days.items.length) setDays(daysAround(parts.day));
  return (
    <View style={styles.wheels}>
      <WheelBand />
      <Wheel
        accessibilityLabel={`${label} : jour`}
        items={days.items}
        index={parts.day - days.first}
        onChange={(index) => onChange(fromWheel(days.first + index, parts.hour, parts.minute))}
        align="right"
        flex={2.2}
      />
      <Wheel
        accessibilityLabel={`${label} : heure`}
        items={HOUR_ITEMS}
        index={parts.hour}
        onChange={(hour) => onChange(fromWheel(parts.day, hour, parts.minute))}
        align="right"
      />
      <Wheel
        accessibilityLabel={`${label} : minutes`}
        items={MINUTE_ITEMS}
        index={parts.minute === 30 ? 1 : 0}
        onChange={(index) => onChange(fromWheel(parts.day, parts.hour, WHEEL_MINUTES[index]))}
        align="left"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  picker: { gap: 0 },
  edge: {
    flexDirection: 'row',
    alignItems: 'center',
    minHeight: touch + 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  edgeLabel: { flex: 1 },
  values: { flexDirection: 'row', gap: 6 },
  value: {
    fontFamily: fonts.bodyMedium,
    fontSize: 16,
    color: colors.text,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: colors.closed,
    overflow: 'hidden',
  },
  time: { fontVariant: ['tabular-nums'], minWidth: 64, textAlign: 'center' },
  valueOpen: { color: colors.accent700, backgroundColor: colors.accent100 },
  struck: { color: colors.err, textDecorationLine: 'line-through' },
  wheels: { flexDirection: 'row', marginVertical: 4 },
  length: { fontFamily: fonts.body, fontSize: 13, color: colors.muted, paddingTop: 8 },
  note: { fontFamily: fonts.body, fontSize: 13, color: colors.absenceInk, paddingTop: 4 },
});
