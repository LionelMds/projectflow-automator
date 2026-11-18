import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Caps } from '@/components/Text';
import { awayOver } from '@/lib/planning/away';
import { calendarResolver } from '@/lib/planning/commands';
import { columnHeading, dayInfoFrom, shownDay } from '@/lib/planning/display';
import { formatInstant, instantDay, parseInstant } from '@/lib/planning/instant';
import type { PlanningData, PlanningTask } from '@/lib/planning/models';
import { isoWeek, mondayOf } from '@/lib/planning/walltime';
import { buildBoard, UNASSIGNED, weekdaysBetween, type WeekRow } from '@/lib/planning/week';
import type { When } from '@/lib/planning/when';
import { colors, fonts } from '@/theme/tokens';

/** The task being created, laid on the board as any other so that it takes its place among them. */
const NEW_TASK = '__nouvelle-tache__';
const NAME_WIDTH = 44;
const LANE = 20;
const BAR = 16;
/** More people than this and the board would push the wheels off the screen. */
const MAX_ROWS = 4;

/** Whether `person` already has a task somewhere in the span, whichever week is shown. */
function busyOver(data: PlanningData, person: string, when: When): boolean {
  return data.tasks.some((task) => {
    if (task.deleted_at || !(person in (task.assignments ?? {}))) return false;
    const start = parseInstant(task.start ?? '');
    const end = parseInstant(task.end ?? '');
    return start !== null && end !== null && start < when.end && when.start < end;
  });
}

/**
 * Where a task about to be created will sit: one week, one row for each of its people
 * (« Non affecté » without any), their tasks and absences as on the planning, and the new
 * task among them. The week is the one of the end of the span being set (`edge`): turning
 * the end into the next week shows that week. Under it, what is in the way of the whole
 * span, or that the way is clear.
 */
export function Placement({
  data,
  when,
  people,
  taskType,
  night = false,
  edge = 'start',
}: {
  data: PlanningData;
  when: When;
  people: readonly string[];
  taskType: string;
  night?: boolean;
  /** The end of the span the week shown is taken from. */
  edge?: 'start' | 'end';
}) {
  const crew = people.join(' ');
  const view = useMemo(() => {
    const ids = crew ? crew.split(' ') : [];
    // A start on a Saturday or a Sunday is worked from the Monday after: that week is the one shown.
    // Its last day is the one of its last minute: a span ending at midnight ends the day before.
    const lastDay = instantDay(Math.max(when.start, when.end - 1));
    const monday = edge === 'end' ? mondayOf(lastDay) : mondayOf(shownDay(instantDay(when.start)));
    const task = {
      id: NEW_TASK,
      title: '',
      task_type: taskType,
      status: 'confirmed',
      start: formatInstant(when.start),
      end: formatInstant(when.end),
      assignments: Object.fromEntries(ids.map((id) => [id, {}])),
      off_hours: night,
    } as PlanningTask;
    const board = buildBoard(
      { ...data, tasks: [...data.tasks, task] },
      { kind: 'slots', days: weekdaysBetween(monday, monday + 4) },
      { dayInfo: dayInfoFrom(calendarResolver(data)(null), data.meta), compact: true },
    );
    const rows = board.groups.flatMap((group) => group.rows).filter((row) => (ids.length ? ids.includes(row.key) : row.key === UNASSIGNED));
    const away = awayOver(data, when);
    // What stands where the task goes, person by person.
    const notes: string[] = [];
    const clear: string[] = [];
    for (const row of rows) {
      const busy = row.key !== UNASSIGNED && busyOver(data, row.key, when);
      const reason = away.get(row.key);
      if (reason) notes.push(`${row.label} est absent sur ce créneau (${reason}).`);
      if (busy) notes.push(`${row.label} a déjà une tâche sur ce créneau.`);
      if (!reason && !busy && row.key !== UNASSIGNED) clear.push(row.label);
    }
    // The task may begin before the week shown, or go on after it.
    const before = instantDay(when.start) < monday;
    const after = lastDay > monday + 4;
    return { monday, board, rows, notes, clear, before, after, markDay: edge === 'end' ? lastDay : instantDay(when.start) };
  }, [data, when, crew, taskType, night, edge]);

  const { monday, board, rows, notes, clear, before, after, markDay } = view;
  const shown = rows.slice(0, MAX_ROWS);
  const pct = (unit: number) => `${(unit / board.units) * 100}%` as const;
  return (
    <View style={styles.box} accessibilityLabel="Aperçu de l’emplacement dans le planning">
      <View style={styles.title}>
        <Caps>Emplacement</Caps>
        <Text style={styles.week}>
          Semaine {isoWeek(monday)}
          {before ? ' · commencée avant' : ''}
          {after ? ' · continue après' : ''}
        </Text>
      </View>
      {/* Another week is another grid, laid afresh as the planning lays its pages: on a phone,
          turning the days and bars of one week into those of the next in place brought the app down. */}
      <View key={monday} collapsable={false}>
        <View style={styles.row}>
          <View style={{ width: NAME_WIDTH }} />
          <View style={styles.days}>
            {board.columns.map((column) => (
              <Text key={column.day} style={[styles.day, column.day === markDay && styles.dayOn]} numberOfLines={1}>
                {columnHeading(column.day)}
              </Text>
            ))}
          </View>
        </View>
        {shown.map((row) => (
          <Line key={row.key} row={row} columns={board.columns} pct={pct} before={before} after={after} />
        ))}
      </View>
      {rows.length > shown.length ? <Text style={styles.more}>+ {rows.length - shown.length} autres personnes</Text> : null}
      {notes.map((note) => (
        <Text key={note} style={styles.note}>
          ▲ {note}
        </Text>
      ))}
      {!notes.length && clear.length ? <Text style={styles.clear}>Créneau libre pour {clear.join(', ').replace(/, ([^,]*)$/, ' et $1')}.</Text> : null}
    </View>
  );
}

function Line({
  row,
  columns,
  pct,
  before,
  after,
}: {
  row: WeekRow;
  columns: readonly { day: number; worked: [boolean, boolean] }[];
  pct: (unit: number) => `${number}%`;
  /** The new task begins before the week shown, goes on after it. */
  before: boolean;
  after: boolean;
}) {
  return (
    <View style={styles.row}>
      <Text style={styles.name} numberOfLines={1}>
        {row.label}
      </Text>
      <View style={[styles.track, { height: row.lanes * LANE + 4 }]}>
        {/* Every mark is a child of the track itself: no box around those of a day. */}
        {columns.flatMap((column, index) => [
          column.worked[0] ? null : <View key={`am:${column.day}`} style={[styles.closed, { left: pct(index * 2), width: pct(1) }]} />,
          column.worked[1] ? null : <View key={`pm:${column.day}`} style={[styles.closed, { left: pct(index * 2 + 1), width: pct(1) }]} />,
          <View key={`line:${column.day}`} style={[styles.dayLine, { left: pct(index * 2) }]} />,
        ])}
        {row.segments.map((segment) => {
          const mine = segment.task?.id === NEW_TASK;
          return (
            <View
              key={`${segment.id}:${segment.lane}`}
              style={[
                styles.bar,
                { left: pct(segment.startSlot), width: pct(segment.span), top: segment.lane * LANE + 2 },
                segment.kind === 'absence'
                  ? styles.absence
                  : mine
                    ? styles.mine
                    : { borderColor: segment.color, backgroundColor: `${segment.color}33` },
              ]}
            >
              {mine ? (
                <Text style={styles.mineText} numberOfLines={1}>
                  {before ? '◀ ' : ''}Nouvelle{after ? ' ▶' : ''}
                </Text>
              ) : null}
            </View>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { gap: 2, padding: 10, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  title: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 },
  week: { flexShrink: 1, fontFamily: fonts.body, fontSize: 12, color: colors.muted },
  row: { flexDirection: 'row', alignItems: 'center' },
  days: { flex: 1, flexDirection: 'row', borderBottomWidth: 1, borderBottomColor: colors.text, paddingBottom: 2 },
  day: { flex: 1, textAlign: 'center', fontFamily: fonts.heading, fontSize: 11, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.muted },
  dayOn: { color: '#ffffff', backgroundColor: colors.accent700 },
  name: { width: NAME_WIDTH, fontFamily: fonts.headingBold, fontSize: 13, letterSpacing: 0.4, color: colors.text },
  track: { flex: 1, borderBottomWidth: 1, borderBottomColor: colors.lineSoft },
  dayLine: { position: 'absolute', top: 0, bottom: 0, width: 1, backgroundColor: colors.lineSoft },
  closed: { position: 'absolute', top: 0, bottom: 0, backgroundColor: colors.closed },
  bar: { position: 'absolute', height: BAR, borderWidth: 1, justifyContent: 'center', overflow: 'hidden' },
  absence: { borderColor: colors.absence, backgroundColor: colors.absenceSoft },
  // The one solid bar: where the task will be.
  mine: { borderWidth: 2, borderColor: colors.accent900, backgroundColor: colors.accent },
  mineText: { fontFamily: fonts.headingBold, fontSize: 10, letterSpacing: 0.4, textTransform: 'uppercase', color: '#ffffff', paddingHorizontal: 3 },
  more: { fontFamily: fonts.body, fontSize: 12, color: colors.muted, paddingTop: 2 },
  note: { fontFamily: fonts.body, fontSize: 13, color: colors.absenceInk, paddingTop: 4 },
  clear: { fontFamily: fonts.body, fontSize: 13, color: colors.ok, paddingTop: 4 },
});
