import { useEffect, useImperativeHandle, useRef, useState, type ComponentRef, type Ref } from 'react';
import { Animated, Platform, StyleSheet, Text, View } from 'react-native';

import { Caps, DossierNumber } from '@/components/Text';
import type { WorkCalendar } from '@/lib/planning/calendar';
import { taskHours } from '@/lib/planning/commands';
import { describeInstant } from '@/lib/planning/display';
import { instantDay, parseInstant } from '@/lib/planning/instant';
import { STATUS_LABELS, taskTypeOf, type PlanningData, type PlanningTask } from '@/lib/planning/models';
import { formatSpan } from '@/lib/planning/quickEntry';
import { dayMonth, hhmm, parseDate, parseWallTime } from '@/lib/planning/walltime';
import { colors, fonts } from '@/theme/tokens';

/**
 * A look at a task without opening it: a bubble by the finger for as long as the task is
 * held, gone when it is let go. It is shown and hidden through its handle, so that holding
 * a task draws nothing else again.
 */
export type TaskPeekHandle = {
  /** `pageX`, `pageY`: where the finger is, in the window. */
  show: (task: PlanningTask, pageX: number, pageY: number) => void;
  hide: () => void;
};

/** A task, where the finger is in the layer, and the size of the layer the bubble must stay in. */
type Peek = { task: PlanningTask; x: number; y: number; width: number; height: number };

const WIDTH = 300;
const MARGIN = 10;
/** The bubble stands clear of the finger: over it, or under it near the top of the screen. */
const OVER = 22;
const UNDER = 34;
const ROOM_OVER = 190;
const TAIL = 12;
const NATIVE = Platform.OS !== 'web';

/** What the bubble says of a task, top to bottom; empty lines are left out. */
function peekLines(data: PlanningData, task: PlanningTask, calendar: WorkCalendar | null) {
  const title = (task.title ?? '').trim();
  const designation = (task.project_cache?.designation ?? '').trim();
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  const scheduled = start !== null && end !== null;
  const from = parseWallTime(task.start);
  const to = parseWallTime(task.end);
  const deadline = task.deadline ? parseDate(task.deadline) : null;
  const status = task.status ?? 'confirmed';
  let when = 'À planifier';
  if (scheduled && task.off_hours && from && to) when = `☾ Nuit du ${dayMonth(instantDay(start))}, ${hhmm(from.minutes)}–${hhmm(to.minutes)}`;
  else if (scheduled) when = `${describeInstant(start)} → ${describeInstant(end)}`;
  return {
    kind: taskTypeOf(data.meta, task.task_type),
    number: (task.project_number ?? '').trim(),
    title: title || designation || 'Sans titre',
    under: [title && designation && designation !== title ? designation : '', (task.project_cache?.client ?? '').trim(), (task.project_cache?.location ?? '').trim()]
      .filter(Boolean)
      .join(' · '),
    when,
    length: scheduled && !task.off_hours && calendar && end > start ? `${formatSpan(calendar, start, end)} de travail` : '',
    offStandard: task.off_standard && !task.off_hours ? 'Hors horaires standard' : '',
    people: Object.keys(task.assignments ?? {})
      .map((id) => data.resources.find((resource) => resource.id === id))
      .map((person) => (person ? person.name || person.initials || '' : ''))
      .filter(Boolean)
      .join(', '),
    status: status === 'confirmed' ? '' : STATUS_LABELS[status],
    urgent: Boolean(task.urgent),
    deadline: deadline !== null ? `Échéance ${dayMonth(deadline)}` : '',
    notes: (task.notes ?? '').trim().replace(/\s+/g, ' '),
  };
}

export function TaskPeek({
  ref,
  data,
  calendarFor,
}: {
  ref: Ref<TaskPeekHandle>;
  data: PlanningData | null;
  calendarFor: ((resourceId: string | null) => WorkCalendar) | null;
}) {
  const [peek, setPeek] = useState<Peek | null>(null);
  // 0: away, 1: there. Moved off the JavaScript thread, so the bubble keeps up with the finger.
  const [shown] = useState(() => new Animated.Value(0));
  const box = useRef<ComponentRef<typeof View>>(null);
  const holding = useRef(false);

  useImperativeHandle(ref, () => ({
    show: (task, pageX, pageY) => {
      holding.current = true;
      // The finger is given in the window: the layer is measured where it is now, so that
      // a screen turned or resized since is no matter.
      const place = (left: number, top: number, width: number, height: number) => {
        if (holding.current) setPeek({ task, x: pageX - left, y: pageY - top, width, height });
      };
      const node = box.current as unknown as { getBoundingClientRect?: () => { left: number; top: number; width: number; height: number } } | null;
      if (node?.getBoundingClientRect) {
        // A browser answers at once.
        const rect = node.getBoundingClientRect();
        place(rect.left, rect.top, rect.width, rect.height);
      } else box.current?.measureInWindow(place);
    },
    hide: () => {
      if (!holding.current) return;
      holding.current = false;
      Animated.timing(shown, { toValue: 0, duration: 120, useNativeDriver: NATIVE }).start(({ finished }) => {
        // Let go and held again before it was gone: the new bubble stays.
        if (finished && !holding.current) setPeek(null);
      });
    },
  }));

  // A bubble just put on screen comes out of the finger.
  useEffect(() => {
    if (!peek) return;
    shown.setValue(0);
    Animated.spring(shown, { toValue: 1, speed: 26, bounciness: 7, useNativeDriver: NATIVE }).start();
  }, [peek, shown]);

  const lines = peek && data ? peekLines(data, peek.task, calendarFor ? taskHours(peek.task, calendarFor) : null) : null;
  const width = peek ? Math.min(WIDTH, Math.max(0, peek.width - 2 * MARGIN)) : 0;
  const over = peek ? peek.y > ROOM_OVER : true;
  const left = peek ? Math.min(Math.max(peek.x - width / 2, MARGIN), Math.max(MARGIN, peek.width - width - MARGIN)) : 0;
  const tail = peek ? Math.min(Math.max(peek.x - left, TAIL + 6), width - TAIL - 6) : 0;

  return (
    <View ref={box} pointerEvents="none" collapsable={false} style={StyleSheet.absoluteFill}>
      {peek && lines ? (
        <Animated.View
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
          style={[
            styles.bubble,
            { width, left },
            over ? { bottom: peek.height - peek.y + OVER } : { top: peek.y + UNDER },
            {
              opacity: shown,
              transform: [
                { translateY: shown.interpolate({ inputRange: [0, 1], outputRange: [over ? 10 : -10, 0] }) },
                { scale: shown.interpolate({ inputRange: [0, 1], outputRange: [0.94, 1] }) },
              ],
            },
          ]}
        >
          {/* The tail points at the finger, from the side of the bubble that faces it. */}
          <View style={[styles.tail, over ? styles.tailUnder : styles.tailOver, { left: tail - TAIL / 2 }]} />
          <View style={styles.card}>
            <View style={[styles.band, { backgroundColor: lines.kind.color }]} />
            <View style={styles.body}>
              <View style={styles.head}>
                {lines.number ? <DossierNumber style={styles.number}>{lines.number}</DossierNumber> : <Caps>Sans dossier</Caps>}
                <View style={styles.marks}>
                  {lines.urgent ? <Text style={[styles.mark, styles.urgent]}>Urgent</Text> : null}
                  {lines.status ? <Text style={styles.mark}>{lines.status}</Text> : null}
                </View>
              </View>
              <Text style={styles.title} numberOfLines={2}>
                {lines.title}
              </Text>
              {lines.under ? (
                <Text style={styles.under} numberOfLines={2}>
                  {lines.under}
                </Text>
              ) : null}
              <View style={styles.rule} />
              <Text style={styles.when} numberOfLines={2}>
                {lines.when}
              </Text>
              {lines.length || lines.offStandard || lines.deadline ? (
                <Text style={styles.detail}>{[lines.length, lines.offStandard, lines.deadline].filter(Boolean).join(' · ')}</Text>
              ) : null}
              <View style={styles.kind}>
                <View style={[styles.swatch, { backgroundColor: lines.kind.color }]} />
                <Text style={styles.detail} numberOfLines={2}>
                  {[lines.kind.label, lines.people || 'Non affecté'].join(' · ')}
                </Text>
              </View>
              {lines.notes ? (
                <Text style={styles.notes} numberOfLines={3}>
                  {lines.notes}
                </Text>
              ) : null}
              <Text style={styles.hint}>Touchez deux fois pour ouvrir</Text>
            </View>
          </View>
        </Animated.View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  bubble: { position: 'absolute', boxShadow: '0 10px 28px rgba(29,31,32,0.26)' },
  card: { borderWidth: 1, borderColor: colors.accent900, backgroundColor: colors.surface, overflow: 'hidden' },
  band: { height: 5 },
  body: { gap: 3, paddingHorizontal: 12, paddingTop: 9, paddingBottom: 10 },
  head: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  number: { fontSize: 18 },
  marks: { flexDirection: 'row', gap: 6 },
  mark: {
    fontFamily: fonts.headingBold,
    fontSize: 11,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    color: colors.accent900,
    paddingHorizontal: 6,
    paddingVertical: 2,
    backgroundColor: colors.accent100,
    overflow: 'hidden',
  },
  urgent: { color: '#ffffff', backgroundColor: colors.err },
  title: { fontFamily: fonts.bodySemiBold, fontSize: 17, lineHeight: 21, color: colors.text },
  under: { fontFamily: fonts.body, fontSize: 13, lineHeight: 17, color: colors.muted },
  rule: { height: 1, marginVertical: 5, backgroundColor: colors.lineSoft },
  when: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text, fontVariant: ['tabular-nums'] },
  detail: { flexShrink: 1, fontFamily: fonts.body, fontSize: 13, lineHeight: 17, color: colors.subtle },
  kind: { flexDirection: 'row', alignItems: 'center', gap: 7, paddingTop: 2 },
  swatch: { width: 10, height: 10 },
  notes: { fontFamily: fonts.body, fontSize: 13, lineHeight: 17, color: colors.muted, fontStyle: 'italic', paddingTop: 4 },
  hint: { fontFamily: fonts.heading, fontSize: 11, letterSpacing: 0.9, textTransform: 'uppercase', color: '#8d9298', paddingTop: 6 },
  // A square turned on its corner, half of it behind the card.
  tail: {
    position: 'absolute',
    width: TAIL,
    height: TAIL,
    backgroundColor: colors.surface,
    borderColor: colors.accent900,
    transform: [{ rotate: '45deg' }],
  },
  tailUnder: { bottom: -TAIL / 2 + 0.5, borderRightWidth: 1, borderBottomWidth: 1 },
  tailOver: { top: -TAIL / 2 + 0.5, borderLeftWidth: 1, borderTopWidth: 1 },
});
