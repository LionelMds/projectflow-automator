/**
 * Board of the planning, as on the desktop grid: one row per person or subcontractor
 * grouped by team, plus a « Non affecté » row, over the shown working days.
 *
 * Week and month views lay bars on half-days (06:30-12:00 and 13:00-16:30) of the
 * working weekdays only, as the desktop axis does (`core/planning/timescale.py`):
 * Saturday and Sunday never appear, holidays and closures stay, flagged as not worked.
 * The day view lays them on the minutes of one day, 06:30 to 16:30.
 */

import {
  ABSENCE_LABELS,
  assigneeIds,
  isScheduled,
  taskLabel,
  taskTypeOf,
  teamsOf,
  type PlanningData,
  type PlanningTask,
  type Resource,
} from '@/lib/planning/models';
import { parseDate, parseWallTime, weekday, type WallTime } from '@/lib/planning/walltime';

export const DAYS = 5;
export const SLOTS = DAYS * 2;
/** First worked minute (06:30), noon, the start of the afternoon and the end of the day. */
export const DAY_START = 6 * 60 + 30;
export const NOON = 12 * 60;
export const AFTERNOON = 13 * 60;
export const DAY_END = 16 * 60 + 30;
/** Friday afternoon, the one half-day of a default week that is never worked. */
export const CLOSED_SLOTS: readonly number[] = [9];

export type Segment = {
  id: string;
  kind: 'task' | 'absence';
  /** In units of the axis: half-days (week, month) or minutes from 06:30 (day). */
  startSlot: number;
  span: number;
  lane: number;
  label: string;
  color: string;
  provisional: boolean;
  done: boolean;
  /** « ×2 » when several people share the task, « 1/2 » when someone is missing. */
  badge: string;
  task?: PlanningTask;
  absenceId?: string;
};

export type WeekRow = { key: string; label: string; name: string; lanes: number; segments: Segment[] };
export type WeekGroup = { key: string; name: string; rows: WeekRow[] };
/** What a shown day is: a holiday or closure label, and which half-days are worked. */
export type DayInfo = { label: string; closed: boolean; worked: [boolean, boolean] };
export type Column = DayInfo & { day: number };
export type Board = { units: number; columns: Column[]; groups: WeekGroup[]; backlog: PlanningTask[] };
export type WeekView = Board & { monday: number };

export type Axis = { kind: 'slots'; days: number[] } | { kind: 'day'; day: number };

export const UNASSIGNED = '__unassigned__';

/** Default week of the workshop: Monday-Thursday all day, Friday morning only. */
export function defaultDayInfo(day: number): DayInfo {
  const index = weekday(day);
  if (index > 4) return { label: '', closed: true, worked: [false, false] };
  return { label: '', closed: false, worked: [true, index !== 4] };
}

/** The working weekdays (Monday to Friday) from `first` to `last` included. */
export function weekdaysBetween(first: number, last: number): number[] {
  const days: number[] = [];
  for (let day = first; day <= last; day += 1) if (weekday(day) < 5) days.push(day);
  return days;
}

type Placed = Omit<Segment, 'lane'>;

class Layout {
  readonly units: number;
  private readonly days: number[];

  constructor(readonly axis: Axis) {
    this.days = axis.kind === 'slots' ? axis.days : [axis.day];
    this.units = axis.kind === 'slots' ? axis.days.length * 2 : DAY_END - DAY_START;
  }

  /** Index of the first shown day at or after `day` (length when none). */
  private firstFrom(day: number): number {
    const index = this.days.findIndex((shown) => shown >= day);
    return index === -1 ? this.days.length : index;
  }

  /** Index of the last shown day at or before `day` (-1 when none). */
  private lastUpTo(day: number): number {
    for (let i = this.days.length - 1; i >= 0; i -= 1) if (this.days[i] <= day) return i;
    return -1;
  }

  /** [start, end) in units, or null when outside the shown range. */
  span(start: WallTime, end: WallTime): [number, number] | null {
    if (this.axis.kind === 'day') {
      const base = this.axis.day * 1440;
      const from = Math.max(start.day * 1440 + start.minutes, base + DAY_START);
      const to = Math.min(end.day * 1440 + end.minutes, base + DAY_END);
      return to > from ? [from - base - DAY_START, to - base - DAY_START] : null;
    }
    // The half-day holding the first worked minute, and the one holding the last.
    let first: number;
    const startIndex = this.firstFrom(start.day);
    if (startIndex < this.days.length && this.days[startIndex] === start.day) {
      first = startIndex * 2 + (start.minutes >= NOON ? 1 : 0);
    } else first = startIndex * 2;
    let endDay = end.day;
    let endMinute = end.minutes - 1;
    if (endMinute < DAY_START) {
      endDay -= 1;
      endMinute = NOON;
    }
    let last: number;
    const endIndex = this.lastUpTo(endDay);
    if (endIndex >= 0 && this.days[endIndex] === endDay) last = endIndex * 2 + (endMinute >= NOON ? 1 : 0);
    else last = endIndex * 2 + 1;
    if (first > last || first >= this.units || last < 0) return null;
    return [Math.max(0, first), Math.min(this.units - 1, last) + 1];
  }

  /** A whole day (night work fills the column of its evening). */
  wholeDay(day: number): [number, number] | null {
    if (this.axis.kind === 'day') return day === this.axis.day ? [0, this.units] : null;
    const index = this.days.indexOf(day);
    return index === -1 ? null : [index * 2, index * 2 + 2];
  }

  /** Days of an absence; the part counts for a single day only, as on the desktop. */
  days_(firstDay: number, lastDay: number, part: string | undefined): [number, number] | null {
    const single = firstDay === lastDay;
    if (this.axis.kind === 'day') {
      const day = this.axis.day;
      if (day < firstDay || day > lastDay) return null;
      if (single && part === 'morning') return [0, NOON - DAY_START];
      if (single && part === 'afternoon') return [AFTERNOON - DAY_START, this.units];
      return [0, this.units];
    }
    const from = this.firstFrom(firstDay);
    const to = this.lastUpTo(lastDay);
    if (from > to || from >= this.days.length || to < 0) return null;
    let first = from * 2;
    let last = to * 2 + 1;
    if (single && part === 'morning') last -= 1;
    if (single && part === 'afternoon') first += 1;
    return [first, last + 1];
  }

  shownDays(): number[] {
    return this.days;
  }
}

function withLanes(segments: Placed[]): { segments: Segment[]; lanes: number } {
  const ends: number[] = [];
  const placed = [...segments]
    .sort((a, b) => a.startSlot - b.startSlot || b.span - a.span)
    .map((segment) => {
      let lane = ends.findIndex((end) => end <= segment.startSlot);
      if (lane === -1) lane = ends.length;
      ends[lane] = segment.startSlot + segment.span;
      return { ...segment, lane };
    });
  return { segments: placed, lanes: Math.max(1, ends.length) };
}

/**
 * Text of a bar on a phone-wide board: the dossier number without its year
 * (« 4998 Montage »), then the task title; the full label is in the detail.
 */
export function barLabel(task: PlanningTask): string {
  const number = (task.project_number ?? '').trim().replace(/^\d{4}-/, '');
  const title = (task.title ?? '').trim() || (task.project_cache?.designation ?? '').trim();
  return [number, title].filter(Boolean).join(' ') || taskLabel(task);
}

/** « 4998 », the compact label of the month view. */
export function shortBarLabel(task: PlanningTask): string {
  const number = (task.project_number ?? '').trim().replace(/^\d{4}-/, '');
  return number || barLabel(task);
}

function badgeOf(task: PlanningTask): string {
  const people = assigneeIds(task).length;
  const required = Math.max(1, task.required_people ?? 1);
  // With nobody at all, the « Non affecté » row already says it.
  if (people > 0 && people < required) return `${people}/${required}`;
  if (people > 1) return `×${people}`;
  return '';
}

function resourceLabel(resource: Resource): string {
  return (resource.initials ?? '').trim() || resource.name;
}

function absenceSegment(id: string, units: [number, number], label: string, absenceId?: string): Placed {
  return {
    id,
    kind: 'absence',
    startSlot: units[0],
    span: units[1] - units[0],
    label,
    color: '',
    provisional: false,
    done: false,
    badge: '',
    absenceId,
  };
}

export function buildBoard(
  data: PlanningData,
  axis: Axis,
  options: { dayInfo?: (day: number) => DayInfo; compact?: boolean } = {},
): Board {
  const layout = new Layout(axis);
  const dayInfo = options.dayInfo ?? defaultDayInfo;
  const live = <T extends { deleted_at?: string | null }>(items: T[]) => items.filter((item) => !item.deleted_at);
  const tasks = live(data.tasks);
  const resources = live(data.resources).filter((resource) => resource.active !== false);
  const known = new Set(resources.map((resource) => resource.id));
  const perRow = new Map<string, Placed[]>();
  const push = (key: string, segment: Placed) => perRow.set(key, [...(perRow.get(key) ?? []), segment]);

  for (const task of tasks) {
    if (!isScheduled(task)) continue;
    const start = parseWallTime(task.start);
    const end = parseWallTime(task.end);
    if (!start || !end) continue;
    let units: [number, number] | null;
    if (task.off_hours) {
      // A night belongs to its evening: before 06:00 it is the previous day's night.
      const evening = start.minutes < 6 * 60 ? start.day - 1 : start.day;
      units = layout.wholeDay(evening);
    } else units = layout.span(start, end);
    if (!units) continue;
    const segment: Placed = {
      id: `task:${task.id}`,
      kind: 'task',
      startSlot: units[0],
      span: units[1] - units[0],
      label: options.compact ? shortBarLabel(task) : barLabel(task),
      color: taskTypeOf(data.meta, task.task_type).color,
      provisional: task.status === 'provisional',
      done: task.status === 'done',
      badge: badgeOf(task),
      task,
    };
    const owners = assigneeIds(task).filter((id) => known.has(id));
    for (const owner of owners.length ? owners : [UNASSIGNED]) push(owner, segment);
  }

  for (const absence of live(data.absences)) {
    const first = parseDate(absence.first_day);
    const last = parseDate(absence.last_day);
    if (first === null || last === null || !known.has(absence.resource_id)) continue;
    const units = layout.days_(first, last, absence.part);
    if (!units) continue;
    const label = ABSENCE_LABELS[absence.reason ?? 'absent'] ?? absence.reason ?? 'Absent';
    push(absence.resource_id, absenceSegment(`absence:${absence.id}`, units, label, absence.id));
  }

  for (const resource of resources) {
    for (const off of resource.recurring_off ?? []) {
      for (const day of layout.shownDays()) {
        if (weekday(day) !== off.weekday) continue;
        const units = layout.days_(day, day, off.part ?? 'day');
        if (!units) continue;
        const label = ABSENCE_LABELS[off.reason ?? 'ecole'] ?? 'Absent';
        push(resource.id, absenceSegment(`off:${resource.id}:${day}`, units, label));
      }
    }
  }

  const teams = [...teamsOf(data.meta)].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const groupKeys = teams.map((team) => team.key);
  for (const resource of resources) {
    const team = resource.team || 'atelier';
    if (!groupKeys.includes(team)) groupKeys.push(team);
  }

  const groups: WeekGroup[] = [];
  for (const key of groupKeys) {
    const members = resources
      .filter((resource) => (resource.team || 'atelier') === key)
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    if (!members.length) continue;
    groups.push({
      key,
      name: teams.find((team) => team.key === key)?.name ?? key,
      rows: members.map((resource) => ({
        key: resource.id,
        label: resourceLabel(resource),
        name: resource.name,
        ...withLanes(perRow.get(resource.id) ?? []),
      })),
    });
  }
  const unassigned = perRow.get(UNASSIGNED);
  if (unassigned?.length) {
    groups.push({
      key: UNASSIGNED,
      name: 'Non affecté',
      rows: [{ key: UNASSIGNED, label: '—', name: 'Non affecté', ...withLanes(unassigned) }],
    });
  }

  const order = (task: PlanningTask) => [task.backlog_rank ?? '', task.created_at ?? '', task.id];
  const backlog = tasks
    .filter((task) => !isScheduled(task) && task.status !== 'done')
    .sort((a, b) => {
      const [left, right] = [order(a), order(b)];
      for (let i = 0; i < left.length; i += 1) if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
      return 0;
    });

  const columns = layout.shownDays().map((day) => ({ day, ...dayInfo(day) }));
  return { units: layout.units, columns, groups, backlog };
}

export function buildWeek(data: PlanningData, monday: number, dayInfo?: (day: number) => DayInfo): WeekView {
  const days = Array.from({ length: DAYS }, (_, index) => monday + index);
  return { monday, ...buildBoard(data, { kind: 'slots', days }, { dayInfo }) };
}
