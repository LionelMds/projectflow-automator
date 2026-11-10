/**
 * « Début » and « Fin » chosen on wheels, as in the iPhone's Calendar: a day, an hour and a
 * half hour (every instant of the planning is snapped to the half hour). Moving the start
 * keeps the worked time, as the Calendar keeps an event's length.
 */

import { snap, type WorkCalendar } from '@/lib/planning/calendar';
import { endOfDays, MIN_TASK_MINUTES } from '@/lib/planning/commands';
import { describeInstant, halfDayStart } from '@/lib/planning/display';
import { dayStart, instantDay, minuteOfDay, type Instant } from '@/lib/planning/instant';
import type { ParsedEntry } from '@/lib/planning/quickEntry';

export type When = { start: Instant; end: Instant };

export const WHEEL_HOURS: readonly number[] = Array.from({ length: 24 }, (_, hour) => hour);
export const WHEEL_MINUTES: readonly number[] = [0, 30];

/** The day, hour and half hour the wheels show for `value`. */
export function wheelParts(value: Instant): { day: number; hour: number; minute: number } {
  const minutes = minuteOfDay(value);
  return { day: instantDay(value), hour: Math.floor(minutes / 60), minute: minutes % 60 >= 30 ? 30 : 0 };
}

export function fromWheel(day: number, hour: number, minute: number): Instant {
  return dayStart(day) + hour * 60 + minute;
}

/** The days of the day wheel: a month back and a year ahead of today and of the days shown. */
export function wheelDays(today: number, ...shown: number[]): { first: number; count: number } {
  const first = Math.min(today, ...shown) - 31;
  const last = Math.max(today, ...shown) + 366;
  return { first, count: last - first + 1 };
}

/** A new start with the same worked time: the end follows, over evenings and weekends. */
export function movedStart(calendar: WorkCalendar, when: When, start: Instant): When {
  const worked = Math.max(MIN_TASK_MINUTES, calendar.workBetween(when.start, when.end));
  return { start, end: calendar.addWork(calendar.workingStart(snap(start)), worked) };
}

/**
 * The span first offered to a task without one: the next working morning, as long as its
 * estimate (hours typed, else days, else one day).
 */
export function firstWhen(
  calendar: WorkCalendar,
  today: number,
  estimate: { minutes?: number; days?: number } = {},
): When {
  const start = halfDayStart(calendar, calendar.nextWorkday(today), false);
  if (estimate.minutes && !estimate.days) return { start, end: calendar.addWork(start, estimate.minutes) };
  return { start, end: endOfDays(calendar, start, estimate.days || 1) };
}

/** What the planning will do with this span, when it is not saved as shown. */
export function whenNotes(calendar: WorkCalendar, when: When): string[] {
  if (when.end <= when.start) return ['La fin doit suivre le début.'];
  const notes: string[] = [];
  const begin = calendar.workingStart(snap(when.start));
  const finish = calendar.workingEnd(snap(when.end));
  if (begin !== snap(when.start)) notes.push(`Hors des heures de travail : la tâche commencera ${describeInstant(begin)}.`);
  if (finish !== snap(when.end)) notes.push(`Hors des heures de travail : la tâche finira ${describeInstant(finish)}.`);
  if (finish <= begin || calendar.workBetween(begin, finish) < MIN_TASK_MINUTES) {
    notes.push('Une tâche dure au moins 30 minutes de travail.');
  }
  return notes;
}

/** True when the planning refuses the span (end first, or less than 30 worked minutes). */
export function whenRefused(calendar: WorkCalendar, when: When): boolean {
  if (when.end <= when.start) return true;
  const begin = calendar.workingStart(snap(when.start));
  const finish = calendar.workingEnd(snap(when.end));
  return finish <= begin || calendar.workBetween(begin, finish) < MIN_TASK_MINUTES;
}

/** The quick entry with the span of the wheels, or sent to « À planifier » (`when` null). */
export function entryWithWhen(entry: ParsedEntry, calendar: WorkCalendar, when: When | null): ParsedEntry {
  if (entry.destination === 'absence' || entry.destination === 'invalid') return entry;
  if (when === null) return { ...entry, start: null, end: null, destination: 'backlog' };
  return {
    ...entry,
    start: when.start,
    end: when.end,
    estimateMinutes: entry.offHours ? when.end - when.start : calendar.workBetween(when.start, when.end),
    estimateDays: entry.offHours ? 0 : (calendar.wholeDays(when.start, when.end) ?? 0),
    destination: entry.resourceIds.length ? 'grid' : 'unassigned',
  };
}
