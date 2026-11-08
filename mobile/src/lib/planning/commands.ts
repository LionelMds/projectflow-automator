/**
 * Editing commands of the planning, ported from the desktop (`core/planning/commands.py`):
 * pure functions from the current documents to the changes to write. Every written
 * document is stamped with the author and time; instants are snapped to the half hour and
 * kept on worked time of the calendar of the row concerned, so a task keeps its amount of
 * work when it moves over a lunch break, a Friday afternoon, a weekend or a holiday.
 *
 * A task without dates waits in « À planifier » (its assignments are the people foreseen);
 * a scheduled task without assignment sits on the « Non affecté » row.
 */

import { calendarFromMeta, snap, SNAP_MINUTES, WorkCalendar } from '@/lib/planning/calendar';
import { creation, envelope, update, type Change } from '@/lib/planning/documents';
import { formatDate, formatInstant, parseInstant, type Instant } from '@/lib/planning/instant';
import {
  ABSENCE_LABELS,
  isScheduled,
  STATUS_LABELS,
  taskTypesOf,
  type Absence,
  type Author,
  type DayPart,
  type PlanningData,
  type PlanningTask,
  type Resource,
  type TaskStatus,
} from '@/lib/planning/models';
import { fittedNight, nightError, nightStartAt } from '@/lib/planning/night';
import { initialRanks, rankBetween } from '@/lib/planning/ranks';
import type { Json } from '@/lib/planning/store';
import { parseDate } from '@/lib/planning/walltime';
import { UNASSIGNED } from '@/lib/planning/week';

export { UNASSIGNED };
export const MIN_TASK_MINUTES = SNAP_MINUTES;
/** Duration of a card without estimate: one full worked day (Monday to Thursday). */
export const DEFAULT_TASK_MINUTES = 540;
export const MIN_ESTIMATE_DAYS = 0.5;
export const MAX_ESTIMATE_DAYS = 250;
const INVALID_DAYS_MESSAGE = 'Valeur invalide pour le champ estimate_days.';

export class PlanningCommandError extends Error {}

export type CalendarFor = (resourceId: string | null) => WorkCalendar;
export type CommandContext = { data: PlanningData; author: Author; now?: string; calendarFor: CalendarFor };

/** Calendar of a row from the shared settings: hours, closures, canton of each person. */
export function calendarResolver(data: PlanningData): CalendarFor {
  const cache = new Map<string, WorkCalendar>();
  const resources = new Map(data.resources.map((resource) => [resource.id, resource]));
  return (resourceId) => {
    const canton = (resourceId ? resources.get(resourceId)?.canton : '') || '';
    const key = canton.toUpperCase() || '*';
    let calendar = cache.get(key);
    if (!calendar) {
      calendar = calendarFromMeta(data.meta, canton);
      cache.set(key, calendar);
    }
    return calendar;
  };
}

export function contextFor(data: PlanningData, author: Author): CommandContext {
  return { data, author, calendarFor: calendarResolver(data) };
}

// ------------------------------------------------------------------ helpers ----

function json(document: object): Json {
  return document as Json;
}

function liveTasks(data: PlanningData): PlanningTask[] {
  return data.tasks.filter((task) => !task.deleted_at);
}

/** Cards of « À planifier » in their shared order. */
export function backlog(data: PlanningData): PlanningTask[] {
  // As the desktop's `PlanningSnapshot.backlog`: finished cards keep their rank too, and
  // ties fall back on the creation time then the id, compared as plain text.
  const key = (task: PlanningTask) => [task.backlog_rank ?? '', task.created_at ?? '', task.id];
  return liveTasks(data)
    .filter((task) => !isScheduled(task))
    .sort((a, b) => {
      const [left, right] = [key(a), key(b)];
      for (let i = 0; i < left.length; i += 1) if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
      return 0;
    });
}

function taskOf(ctx: CommandContext, id: string, deletedOk = false): PlanningTask {
  const task = ctx.data.tasks.find((candidate) => candidate.id === id);
  if (!task) throw new PlanningCommandError('Tâche introuvable.');
  if (task.deleted_at && !deletedOk) throw new PlanningCommandError('Cette tâche a été supprimée.');
  return task;
}

function resourceOf(ctx: CommandContext, id: string): Resource {
  const resource = ctx.data.resources.find((candidate) => candidate.id === id && !candidate.deleted_at);
  if (!resource) throw new PlanningCommandError('Personne introuvable dans le planning.');
  return resource;
}

function assignable(ctx: CommandContext, id: string): Resource {
  const resource = resourceOf(ctx, id);
  if (resource.active === false) throw new PlanningCommandError(`${resource.name} est archivé(e) : affectation impossible.`);
  return resource;
}

function checkedResourceIds(ctx: CommandContext, ids: readonly string[]): string[] {
  const unique = [...new Set(ids.filter((id) => id !== UNASSIGNED))];
  for (const id of unique) assignable(ctx, id);
  return unique;
}

function activeIds(ctx: CommandContext, task: PlanningTask): string[] {
  const active = new Set(ctx.data.resources.filter((r) => !r.deleted_at && r.active !== false).map((r) => r.id));
  return Object.keys(task.assignments ?? {}).filter((id) => active.has(id));
}

function checkTaskType(ctx: CommandContext, key: string): void {
  if (!taskTypesOf(ctx.data.meta).some((kind) => kind.key === key)) {
    throw new PlanningCommandError(`Type de tâche inconnu : ${key}`);
  }
}

function calendarOf(ctx: CommandContext, resourceId: string | null | undefined): WorkCalendar {
  return ctx.calendarFor(!resourceId || resourceId === UNASSIGNED ? null : resourceId);
}

function taskCalendar(ctx: CommandContext, task: PlanningTask): WorkCalendar {
  return calendarOf(ctx, Object.keys(task.assignments ?? {})[0]);
}

function spanOf(task: PlanningTask): [Instant, Instant] {
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  if (start === null || end === null) throw new PlanningCommandError("Cette tâche n'est pas planifiée.");
  return [start, end];
}

/** Worked minutes rounded up to the half hour, at least 30; 0 means one worked day. */
function duration(minutes: number): number {
  if (minutes <= 0) return DEFAULT_TASK_MINUTES;
  return Math.max(MIN_TASK_MINUTES, Math.ceil(minutes / SNAP_MINUTES) * SNAP_MINUTES);
}

/** Worked minutes of a bar; one lying on days closed since then keeps its weekday hours. */
function worked(calendar: WorkCalendar, start: Instant, end: Instant): number {
  let minutes = calendar.workBetween(start, end);
  if (minutes <= 0) minutes = new WorkCalendar({ week: calendar.week, cantons: [] }).workBetween(start, end);
  return Math.max(MIN_TASK_MINUTES, minutes);
}

function span(calendar: WorkCalendar, start: Instant, minutes: number): [Instant, Instant] {
  const begin = calendar.workingStart(snap(start));
  return [begin, calendar.addWork(begin, duration(minutes))];
}

/** Days to the half day, halves up, ½ j at least (`round_days`, `_estimate_days`). */
export function estimateDays(days: number): number {
  if (!(days >= 0 && days <= MAX_ESTIMATE_DAYS)) throw new PlanningCommandError(INVALID_DAYS_MESSAGE);
  if (!days) return 0;
  return Math.max(Math.floor(days * 2 + 0.5) / 2, MIN_ESTIMATE_DAYS);
}

export function endOfDays(calendar: WorkCalendar, begin: Instant, days: number): Instant {
  return Math.max(calendar.addDays(begin, days), calendar.addWork(begin, MIN_TASK_MINUTES));
}

function checkSpan(calendar: WorkCalendar, start: Instant, end: Instant): void {
  if (end <= start) throw new PlanningCommandError('La fin doit suivre le début.');
  if (calendar.workBetween(start, end) < MIN_TASK_MINUTES) {
    throw new PlanningCommandError('Une tâche dure au moins 30 minutes de travail.');
  }
}

function newSpan(
  calendar: WorkCalendar,
  start: Instant,
  end: Instant | null,
  estimateMinutes: number,
  days = 0,
): [Instant, Instant] {
  const rounded = estimateDays(days);
  if (end === null && rounded) {
    const begin = calendar.workingStart(snap(start));
    return [begin, endOfDays(calendar, begin, rounded)];
  }
  if (end === null) return span(calendar, start, estimateMinutes);
  const begin = calendar.workingStart(snap(start));
  const finish = calendar.workingEnd(snap(end));
  checkSpan(calendar, begin, finish);
  return [begin, finish];
}

function nightSpan(start: Instant, end: Instant | null, minutes: number): [Instant, Instant] {
  let begin = nightStartAt(start);
  let finish: Instant;
  if (end === null) [begin, finish] = fittedNight(begin, minutes);
  else finish = snap(end);
  const error = nightError(begin, finish);
  if (error) throw new PlanningCommandError(error);
  return [begin, finish];
}

function save(ctx: CommandContext, kind: 'task' | 'absence' | 'resource', document: object, fields: Json): Change[] {
  const change = update(kind, json(document), fields, ctx.author, ctx.now);
  return change ? [change] : [];
}

function wallTimes(begin: Instant, finish: Instant): Json {
  return { start: formatInstant(begin), end: formatInstant(finish) };
}

/** Rank for a card inserted at `position` of `cards`; renumbers them when no room is left. */
function backlogRank(ctx: CommandContext, cards: PlanningTask[], position: number): [string, Change[]] {
  const before = position > 0 ? cards[position - 1].backlog_rank : null;
  const after = position < cards.length ? cards[position].backlog_rank : null;
  try {
    return [rankBetween(before, after ?? null), []];
  } catch {
    const ranks = initialRanks(cards.length + 1);
    const changes = cards.flatMap((card, index) =>
      save(ctx, 'task', card, { backlog_rank: ranks[index + (index >= position ? 1 : 0)] }),
    );
    return [ranks[position], changes];
  }
}

// -------------------------------------------------------------------- tasks ----

export type NewTask = {
  title: string;
  taskType: string;
  projectNumber?: string;
  projectCache?: Json;
  start?: Instant | null;
  end?: Instant | null;
  resourceIds?: readonly string[];
  estimateMinutes?: number;
  estimateDays?: number;
  requiredPeople?: number;
  status?: TaskStatus;
  deadline?: number | null;
  urgent?: boolean;
  notes?: string;
  origin?: string;
  offHours?: boolean;
};

/** Create a task, always the first change returned (`create_task`). */
export function createTask(ctx: CommandContext, input: NewTask): Change[] {
  const people = checkedResourceIds(ctx, input.resourceIds ?? []);
  checkTaskType(ctx, input.taskType);
  const days = estimateDays(input.estimateDays ?? 0);
  const minutes = input.estimateMinutes ?? 0;
  if (!(Number.isInteger(minutes) && minutes >= 0)) throw new PlanningCommandError('Valeur invalide pour le champ estimate_minutes.');
  const data: Json = {
    ...envelope(),
    status: input.status ?? 'confirmed',
    task_type: input.taskType,
    title: input.title.trim(),
    project_number: (input.projectNumber ?? '').trim(),
    project_cache: {
      client: '',
      contact: '',
      designation: '',
      managed_by: '',
      atelier_release: '',
      location: '',
      read_at: '',
      ...(input.projectCache ?? {}),
    },
    project_fingerprint: '',
    start: null,
    end: null,
    estimate_minutes: minutes,
    estimate_days: days,
    required_people: Math.max(1, input.requiredPeople ?? 1),
    assignments: Object.fromEntries(people.map((id) => [id, {}])),
    deadline: input.deadline === null || input.deadline === undefined ? null : formatDate(input.deadline),
    urgent: input.urgent ?? false,
    notes: input.notes ?? '',
    backlog_rank: '',
    origin: input.origin ?? 'grid',
    planner_task_id: '',
    off_hours: input.offHours ?? false,
  };
  const start = input.start ?? null;
  const end = input.end ?? null;
  let extra: Change[] = [];
  if (start === null) {
    if (end !== null) throw new PlanningCommandError("Une tâche planifiée a besoin d'un début.");
    const cards = backlog(ctx.data);
    const [rank, changes] = backlogRank(ctx, cards, cards.length);
    data.backlog_rank = rank;
    extra = changes;
  } else if (input.offHours) {
    Object.assign(data, wallTimes(...nightSpan(start, end, minutes)));
    data.estimate_days = 0;
  } else {
    Object.assign(data, wallTimes(...newSpan(calendarOf(ctx, people[0]), start, end, minutes, days)));
  }
  return [creation('task', data, ctx.author, ctx.now), ...extra];
}

/**
 * Put a card on the grid, or plan a scheduled task again (`schedule_task`). Empty
 * `resourceIds` keep the people foreseen who are still active; `[UNASSIGNED]` puts it on
 * « Non affecté ». Without `end` a card lasts its estimate, a task keeps its worked minutes.
 */
export function scheduleTask(
  ctx: CommandContext,
  id: string,
  { start, resourceIds = [], end = null }: { start: Instant; resourceIds?: readonly string[]; end?: Instant | null },
): Change[] {
  const task = taskOf(ctx, id);
  const people = resourceIds.length ? checkedResourceIds(ctx, resourceIds) : activeIds(ctx, task);
  const assignments = Object.fromEntries(people.map((rid) => [rid, { ...(task.assignments?.[rid] ?? {}) }]));
  if (task.off_hours) {
    const elapsed = isScheduled(task) ? spanOf(task)[1] - spanOf(task)[0] : 0;
    return save(ctx, 'task', task, { ...wallTimes(...nightSpan(start, end, elapsed || (task.estimate_minutes ?? 0))), assignments });
  }
  let minutes: number;
  let days: number;
  if (isScheduled(task)) {
    const [begin, finish] = spanOf(task);
    minutes = worked(taskCalendar(ctx, task), begin, finish);
    days = 0;
  } else {
    minutes = task.estimate_minutes ?? 0;
    days = task.estimate_days ?? 0;
  }
  const [begin, finish] = newSpan(calendarOf(ctx, people[0]), start, end, minutes, days);
  return save(ctx, 'task', task, { ...wallTimes(begin, finish), assignments });
}

function replaced(assignments: Record<string, Record<string, unknown>>, source: string | null, target: string | null) {
  if (source === null) return target !== null ? { ...assignments, [target]: {} } : { ...assignments };
  const result: Record<string, Record<string, unknown>> = {};
  for (const [rid, value] of Object.entries(assignments)) {
    if (rid !== source) result[rid] = value;
    else if (target !== null) result[target] = value;
  }
  return result;
}

/**
 * New start, same worked minutes (`move_task`). A row change replaces only `fromResource`
 * by `toResource`; UNASSIGNED is the « Non affecté » row.
 */
export function moveTask(
  ctx: CommandContext,
  id: string,
  { newStart, fromResource = null, toResource }: { newStart: Instant; fromResource?: string | null; toResource?: string | null },
): Change[] {
  const task = taskOf(ctx, id);
  const [start, end] = spanOf(task);
  const source = fromResource === null || fromResource === UNASSIGNED ? null : fromResource;
  const target = toResource === undefined ? source : toResource === null || toResource === UNASSIGNED ? null : toResource;
  let assignments = { ...(task.assignments ?? {}) };
  const minutes = worked(taskCalendar(ctx, task), start, end);
  if (target !== source) {
    if (source === null && Object.keys(assignments).length) {
      throw new PlanningCommandError("Cette tâche n'est pas sur la ligne Non affecté.");
    }
    if (source !== null && !(source in assignments)) {
      throw new PlanningCommandError("Cette personne n'est pas affectée à cette tâche.");
    }
    if (target !== null) {
      if (target in assignments) return [];
      assignable(ctx, target);
    }
    assignments = replaced(assignments, source, target);
  }
  let begin: Instant;
  let finish: Instant;
  if (task.off_hours) {
    begin = snap(newStart);
    finish = begin + (end - start);
    const error = nightError(begin, finish);
    if (error) throw new PlanningCommandError(error);
  } else {
    [begin, finish] = span(calendarOf(ctx, Object.keys(assignments)[0]), newStart, minutes);
  }
  return save(ctx, 'task', task, { ...wallTimes(begin, finish), assignments });
}

/** Move one edge of a bar; the task keeps at least 30 worked minutes (`resize_task`). */
export function resizeTask(ctx: CommandContext, id: string, { start = null, end = null }: { start?: Instant | null; end?: Instant | null }): Change[] {
  const task = taskOf(ctx, id);
  const [oldStart, oldEnd] = spanOf(task);
  if (task.off_hours) {
    const begin = start === null ? oldStart : snap(start);
    let finish = end === null ? oldEnd : snap(end);
    if (finish - begin < SNAP_MINUTES) finish = begin + SNAP_MINUTES;
    const error = nightError(begin, finish);
    if (error) throw new PlanningCommandError(error);
    return save(ctx, 'task', task, wallTimes(begin, finish));
  }
  const calendar = taskCalendar(ctx, task);
  let begin = start === null ? oldStart : calendar.workingStart(snap(start));
  let finish = end === null ? oldEnd : calendar.workingEnd(snap(end));
  const tooShort = calendar.workBetween(begin, finish) < MIN_TASK_MINUTES;
  if (tooShort && start === null && end !== null) finish = calendar.addWork(begin, MIN_TASK_MINUTES);
  else if (tooShort && start !== null && end === null) begin = calendar.subtractWork(finish, MIN_TASK_MINUTES);
  else checkSpan(calendar, begin, finish);
  return save(ctx, 'task', task, wallTimes(begin, finish));
}

/** Working days of a scheduled task as its bar says them (`task_days`). */
export function taskDays(ctx: CommandContext, task: PlanningTask): number {
  if (!isScheduled(task)) return 0;
  const [start, end] = spanOf(task);
  return taskCalendar(ctx, task).wholeDays(start, end) ?? 0;
}

/** Same start, a length in working days (the « Durée » of the detail). */
export function setTaskDays(ctx: CommandContext, id: string, days: number): Change[] {
  const task = taskOf(ctx, id);
  const [start] = spanOf(task);
  const rounded = estimateDays(days);
  if (!rounded) throw new PlanningCommandError(INVALID_DAYS_MESSAGE);
  const end = endOfDays(taskCalendar(ctx, task), start, rounded);
  return save(ctx, 'task', task, { end: formatInstant(end), estimate_days: rounded });
}

export function assign(ctx: CommandContext, id: string, resourceId: string): Change[] {
  const task = taskOf(ctx, id);
  if (task.assignments && resourceId in task.assignments) return [];
  assignable(ctx, resourceId);
  return save(ctx, 'task', task, { assignments: { ...(task.assignments ?? {}), [resourceId]: {} } });
}

/** Remove a person; without anybody left a scheduled task goes back to « À planifier ». */
export function unassign(ctx: CommandContext, id: string, resourceId: string): Change[] {
  const task = taskOf(ctx, id);
  if (!task.assignments || !(resourceId in task.assignments)) return [];
  const remaining = Object.fromEntries(Object.entries(task.assignments).filter(([rid]) => rid !== resourceId));
  if (Object.keys(remaining).length || !isScheduled(task)) return save(ctx, 'task', task, { assignments: remaining });
  return toBacklog(ctx, task, remaining, calendarOf(ctx, resourceId));
}

/** Back to « À planifier »: the assignees stay as people foreseen. */
export function unscheduleTask(ctx: CommandContext, id: string): Change[] {
  const task = taskOf(ctx, id);
  if (!isScheduled(task)) return [];
  return toBacklog(ctx, task, { ...(task.assignments ?? {}) }, taskCalendar(ctx, task));
}

function cardDays(calendar: WorkCalendar, task: PlanningTask): number {
  const [start, end] = spanOf(task);
  const days = task.estimate_days ?? 0;
  if (days > 0 && days <= MAX_ESTIMATE_DAYS) {
    const kept = estimateDays(days);
    if (endOfDays(calendar, start, kept) === end) return kept;
  }
  return calendar.wholeDays(start, end) ?? 0;
}

function toBacklog(
  ctx: CommandContext,
  task: PlanningTask,
  assignments: Record<string, Record<string, unknown>>,
  calendar: WorkCalendar,
): Change[] {
  const [start, end] = spanOf(task);
  const cards = backlog(ctx.data).filter((card) => card.id !== task.id);
  const [rank, extra] = backlogRank(ctx, cards, cards.length);
  const night = Boolean(task.off_hours);
  const moved = save(ctx, 'task', task, {
    start: null,
    end: null,
    assignments,
    estimate_minutes: night ? end - start : worked(calendar, start, end),
    estimate_days: night ? 0 : cardDays(calendar, task),
    backlog_rank: rank,
  });
  return [...moved, ...extra];
}

export function setStatus(ctx: CommandContext, id: string, status: TaskStatus): Change[] {
  if (!Object.prototype.hasOwnProperty.call(STATUS_LABELS, status)) throw new PlanningCommandError(`Statut inconnu: ${status}`);
  return save(ctx, 'task', taskOf(ctx, id), { status });
}

export type TaskFields = Partial<{
  title: string;
  task_type: string;
  project_number: string;
  /** The répertoire details of a new `project_number` (`_project_cache`); cleared without them. */
  project_cache: { client?: string; contact?: string; designation?: string; managed_by?: string; read_at?: string };
  notes: string;
  deadline: string | null;
  urgent: boolean;
  required_people: number;
  estimate_minutes: number;
  estimate_days: number;
}>;

/** Change the details of a task, not its dates nor its people (`update_task`). */
export function updateTask(ctx: CommandContext, id: string, fields: TaskFields): Change[] {
  const task = taskOf(ctx, id);
  const next: Json = { ...fields };
  if (typeof next.title === 'string') next.title = next.title.trim();
  if (typeof next.project_number === 'string') next.project_number = next.project_number.trim();
  if (typeof next.estimate_days === 'number') next.estimate_days = estimateDays(next.estimate_days);
  if (typeof next.required_people === 'number') next.required_people = Math.max(1, Math.round(next.required_people));
  // The desktop refuses to read a negative or fractional estimate, or an unreadable deadline.
  if ('estimate_minutes' in next && !(Number.isInteger(next.estimate_minutes) && Number(next.estimate_minutes) >= 0)) {
    throw new PlanningCommandError('Valeur invalide pour le champ estimate_minutes.');
  }
  if (typeof next.deadline === 'string' && !isDateText(next.deadline)) {
    throw new PlanningCommandError('Valeur invalide pour le champ deadline.');
  }
  if (typeof next.task_type === 'string') checkTaskType(ctx, next.task_type);
  if ('project_number' in next && next.project_number !== (task.project_number ?? '')) {
    // Another dossier: the details copied from the old one no longer apply.
    if (!('project_cache' in next)) next.project_cache = {};
    if (!('project_fingerprint' in next)) next.project_fingerprint = '';
  }
  return save(ctx, 'task', task, next);
}

/** Soft deletion: the task keeps its content and can be restored. */
export function deleteTask(ctx: CommandContext, id: string): Change[] {
  const task = taskOf(ctx, id, true);
  if (task.deleted_at) return [];
  return save(ctx, 'task', task, { deleted_at: ctx.now ?? nowText(), deleted_by: { ...ctx.author } });
}

export function restoreTask(ctx: CommandContext, id: string): Change[] {
  const task = taskOf(ctx, id, true);
  if (!task.deleted_at) return [];
  return save(ctx, 'task', task, { deleted_at: null, deleted_by: null });
}

function nowText(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** « 2026-10-30 », a real calendar date. */
function isDateText(text: string): boolean {
  const day = parseDate(text);
  return day !== null && formatDate(day) === text;
}

// ----------------------------------------------------------------- absences ----

export function createAbsence(
  ctx: CommandContext,
  input: { resourceId: string; firstDay: number; lastDay?: number | null; reason?: string; part?: DayPart; note?: string },
): Change[] {
  resourceOf(ctx, input.resourceId);
  const last = input.lastDay ?? input.firstDay;
  if (last < input.firstDay) throw new PlanningCommandError("La fin de l'absence précède son début.");
  const reason = input.reason ?? 'absent';
  if (!Object.prototype.hasOwnProperty.call(ABSENCE_LABELS, reason)) {
    throw new PlanningCommandError('Valeur invalide pour le champ reason.');
  }
  const data: Json = {
    ...envelope(),
    resource_id: input.resourceId,
    reason,
    first_day: formatDate(input.firstDay),
    last_day: formatDate(last),
    part: input.part ?? 'day',
    note: (input.note ?? '').trim(),
  };
  return [creation('absence', data, ctx.author, ctx.now)];
}

/** Soft deletion, like tasks. */
export function deleteAbsence(ctx: CommandContext, id: string): Change[] {
  const absence: Absence | undefined = ctx.data.absences.find((candidate) => candidate.id === id);
  if (!absence) throw new PlanningCommandError('Absence introuvable.');
  if (absence.deleted_at) return [];
  return save(ctx, 'absence', absence, { deleted_at: ctx.now ?? nowText(), deleted_by: { ...ctx.author } });
}
