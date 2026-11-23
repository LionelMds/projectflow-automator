/**
 * « Alerte notification », as the desktop computes it (`core/planning/alerts.py`): when a
 * task rings, for whom, and what it says. A task carries `reminders`: « 30 min avant »
 * follows its start, « à un moment précis » stays where it was set.
 *
 * The planning has no server. A phone cannot look every minute as a poste does: it hands
 * the system, at each reading of the planning, the alerts still to come
 * (`plannedNotifications`), which then ring even with the app closed. A task moved by
 * somebody else rings at its new time once the phone has read the planning again.
 */

import { dayStart, formatHhmm, formatInstant, instantDay, minuteOfDay, MINUTES_PER_DAY, MINUTES_PER_HOUR, parseInstant, type Instant } from './instant';
import { assigneeIds, taskLabel, type PlanningData, type PlanningTask, type Reminder } from './models';
import { dateParts, weekday } from './walltime';

/** « Avant le début » offered in one touch, in minutes (`BEFORE_PRESETS`). */
export const BEFORE_PRESETS: readonly number[] = [0, 5, 15, 30, 60, 120, MINUTES_PER_DAY];
/** The ones shown as chips on the phone; the others stay reachable from the desktop. */
export const PHONE_PRESETS: readonly number[] = [0, 15, 30, 60, MINUTES_PER_DAY];
export const MAX_REMINDERS = 6;
export const MAX_REMINDER_MINUTES = 14 * MINUTES_PER_DAY;
/** iOS keeps 64 pending notifications per app: the nearest ones are handed over. */
export const MAX_PLANNED = 48;
/** Alerts further than this are handed over at a later reading of the planning. */
export const PLANNED_DAYS = 60;
export const UNTITLED = 'Tâche du planning';
const WEEKDAY_SHORT = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'];

export type AlertAudience = 'mine' | 'all';

export type PlannedNotification = {
  /** Task, alert and time it is due: a task that moves gets a new one. */
  id: string;
  taskId: string;
  due: Instant;
  title: string;
  body: string;
};

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/** The instant of an « at » alert, null for an alert before the start or an unreadable one. */
export function reminderAt(reminder: Reminder): Instant | null {
  return reminder.kind === 'at' && reminder.at ? parseInstant(reminder.at) : null;
}

function leadMinutes(reminder: Reminder): number {
  return Number.isInteger(reminder.minutes) ? Math.max(reminder.minutes ?? 0, 0) : 0;
}

/** What tells two alerts of one task apart (« b30 », « a2026-11-24T07:00 »), as `Reminder.key`. */
export function reminderKey(reminder: Reminder): string {
  const at = reminderAt(reminder);
  return at !== null ? `a${formatInstant(at)}` : `b${leadMinutes(reminder)}`;
}

/** « 30 min », « 1 h 30 », « 2 j » */
export function leadText(minutes: number): string {
  if (minutes >= MINUTES_PER_DAY && minutes % MINUTES_PER_DAY === 0) return `${minutes / MINUTES_PER_DAY} j`;
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  const rest = minutes % MINUTES_PER_HOUR;
  if (hours && rest) return `${hours} h ${pad2(rest)}`;
  return hours ? `${hours} h` : `${rest} min`;
}

/** « lun 23.11 à 07:00 »; « aujourd'hui à 07:00 » and « demain à 07:00 » near `today`. */
export function momentText(value: Instant, today?: number): string {
  const day = instantDay(value);
  const clock = formatHhmm(minuteOfDay(value));
  if (today !== undefined) {
    if (day === today) return `aujourd'hui à ${clock}`;
    if (day === today + 1) return `demain à ${clock}`;
  }
  const { date, month } = dateParts(day);
  return `${WEEKDAY_SHORT[weekday(day)]} ${pad2(date)}.${pad2(month)} à ${clock}`;
}

/** How an alert reads on its chip: « 30 min avant », « Au début », « lun 23.11 à 07:00 ». */
export function reminderText(reminder: Reminder, today?: number): string {
  const at = reminderAt(reminder);
  if (at !== null) return momentText(at, today);
  const minutes = leadMinutes(reminder);
  return minutes === 0 ? 'Au début' : `${leadText(minutes)} avant`;
}

/** When `reminder` rings; null for « avant le début » of a task without dates. */
export function reminderDue(task: PlanningTask, reminder: Reminder): Instant | null {
  if (reminder.kind === 'at') return reminderAt(reminder);
  const start = parseInstant(task.start ?? '');
  return start === null ? null : start - leadMinutes(reminder);
}

/**
 * The alerts as the desktop stores them (`set_reminders`): the same one given twice counts
 * once, the furthest before the start first, then the set moments. Unreadable ones are left out.
 */
export function orderedReminders(reminders: readonly Reminder[]): Reminder[] {
  const unique = new Map<string, Reminder>();
  for (const reminder of reminders) {
    const at = reminderAt(reminder);
    if (reminder.kind === 'at' && at === null) continue;
    const minutes = leadMinutes(reminder);
    if (minutes > MAX_REMINDER_MINUTES) continue;
    const clean: Reminder = at !== null ? { kind: 'at', minutes: 0, at: formatInstant(at) } : { kind: 'before', minutes, at: null };
    const key = reminderKey(clean);
    if (!unique.has(key)) unique.set(key, clean);
  }
  return [...unique.values()].sort((left, right) => {
    const leftAt = reminderAt(left);
    const rightAt = reminderAt(right);
    if ((leftAt === null) !== (rightAt === null)) return leftAt === null ? -1 : 1;
    if (leftAt !== null && rightAt !== null) return leftAt - rightAt;
    return leadMinutes(right) - leadMinutes(left);
  });
}

/** Add or remove one alert; the list comes back ordered. */
export function toggledReminder(reminders: readonly Reminder[], reminder: Reminder): Reminder[] {
  const key = reminderKey(reminder);
  const has = reminders.some((existing) => reminderKey(existing) === key);
  return orderedReminders(has ? reminders.filter((existing) => reminderKey(existing) !== key) : [...reminders, reminder]);
}

/** The planning row of these initials, as the desktop's `my_resource_id` finds it. */
export function myResourceId(data: PlanningData, initials: string): string {
  const wanted = initials.trim().toLowerCase();
  if (!wanted) return '';
  const found = data.resources.find((resource) => !resource.deleted_at && (resource.initials ?? '').trim().toLowerCase() === wanted);
  return found?.id ?? '';
}

/** Whether the alerts of `task` ring on the phone of this person (`concerns`). */
export function concerns(task: PlanningTask, resourceId: string, initials: string): boolean {
  const mine = initials.trim().toLowerCase();
  if (!resourceId && !mine) return true;
  const people = assigneeIds(task);
  if (people.length) return Boolean(resourceId) && people.includes(resourceId);
  const author = (task.created_by?.initials ?? '').trim().toLowerCase();
  return !author || author === mine;
}

/** Second line of an alert: when the task starts, then who does it (`alert_body`). */
export function alertBody(task: PlanningTask, reminder: Reminder, data: PlanningData, today?: number): string {
  const start = parseInstant(task.start ?? '');
  const minutes = leadMinutes(reminder);
  let lead: string;
  if (start === null) lead = 'Rappel · tâche à planifier';
  else if (reminder.kind === 'at') lead = `Rappel · début ${momentText(start, today)}`;
  else if (minutes === 0) lead = `Commence maintenant · ${momentText(start, today)}`;
  else lead = `Commence dans ${leadText(minutes)} · ${momentText(start, today)}`;
  const people = assigneeIds(task)
    .map((id) => data.resources.find((resource) => resource.id === id))
    .filter((resource) => resource !== undefined)
    .map((resource) => resource.initials || resource.name);
  return people.length ? `${lead} · ${people.join(', ')}` : lead;
}

/**
 * The alerts still to come after `now`, the nearest first, at most `MAX_PLANNED` and within
 * `PLANNED_DAYS`. Finished and deleted tasks never ring. The text is written for the day
 * the alert rings (« aujourd'hui à 07:00 »).
 */
export function plannedNotifications(
  data: PlanningData,
  options: { now: Instant; initials: string; audience?: AlertAudience },
): PlannedNotification[] {
  const { now, audience = 'mine' } = options;
  const initials = audience === 'all' ? '' : options.initials;
  const resourceId = audience === 'all' ? '' : myResourceId(data, initials);
  const horizon = dayStart(instantDay(now) + PLANNED_DAYS);
  const found: PlannedNotification[] = [];
  for (const task of data.tasks) {
    if (task.deleted_at || task.status === 'done' || !task.reminders?.length) continue;
    if (!concerns(task, resourceId, initials)) continue;
    for (const reminder of orderedReminders(task.reminders)) {
      const due = reminderDue(task, reminder);
      if (due === null || due <= now || due > horizon) continue;
      found.push({
        id: `${task.id}|${reminderKey(reminder)}|${due}`,
        taskId: task.id,
        due,
        title: taskLabel(task) || UNTITLED,
        body: alertBody(task, reminder, data, instantDay(due)),
      });
    }
  }
  return found.sort((left, right) => left.due - right.due || left.id.localeCompare(right.id)).slice(0, MAX_PLANNED);
}

/** The instant of a device clock, as the planning counts it (local wall time). */
export function instantOf(date: Date): Instant {
  const day = Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86_400_000);
  return dayStart(day) + date.getHours() * MINUTES_PER_HOUR + date.getMinutes();
}

/** The device date of an instant: the same wall clock, in the phone's time zone. */
export function dateOf(value: Instant): Date {
  const { year, month, date } = dateParts(instantDay(value));
  const minutes = minuteOfDay(value);
  return new Date(year, month - 1, date, Math.floor(minutes / MINUTES_PER_HOUR), minutes % MINUTES_PER_HOUR);
}
