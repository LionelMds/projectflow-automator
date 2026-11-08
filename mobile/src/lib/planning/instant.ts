/**
 * Planning instants as the desktop computes them (`core/planning/walltime.py`): a plain
 * integer, the day number times 1440 plus the minutes since midnight. Arithmetic, sorting
 * and comparison are those of whole minutes; no time zone, no `Date` in between. The day
 * number is the one of `walltime.ts` (days since 1970-01-01), so a Python `date` is a
 * `number` here.
 */

import { dateParts, dayNumber, MINUTES_PER_DAY, mondayOf, weekday } from './walltime';

export type Instant = number;

export { MINUTES_PER_DAY };
export const MINUTES_PER_HOUR = 60;
export const HOURS_PER_DAY = 24;

const INSTANT_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/;

/** The desktop's `ValueError`: bad hours, no working day ahead, unknown canton… */
export class ValueError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValueError';
  }
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

/** Python's `divmod` / `%` on integers: the rest has the sign of the divisor. */
export function mod(value: number, divisor: number): number {
  return ((value % divisor) + divisor) % divisor;
}

/** Python's `round()`: to the nearest integer, halves to the even one. */
export function roundHalfEven(value: number): number {
  const floor = Math.floor(value);
  const rest = value - floor;
  if (rest > 0.5) return floor + 1;
  if (rest < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

/** Instant of `day` at `hour:minute`; 24:00 is accepted as the end of the day. */
export function instant(day: number, hour = 0, minute = 0): Instant {
  const endOfDay = hour === HOURS_PER_DAY && minute === 0;
  const inDay = hour >= 0 && hour < HOURS_PER_DAY && minute >= 0 && minute < MINUTES_PER_HOUR;
  if (!inDay && !endOfDay) {
    throw new ValueError(`Heure hors de la journee: ${pad2(hour)}:${pad2(minute)}`);
  }
  return day * MINUTES_PER_DAY + hour * MINUTES_PER_HOUR + minute;
}

/** Day number of an instant (Python's `wall_date`). */
export function instantDay(value: Instant): number {
  return Math.floor(value / MINUTES_PER_DAY);
}

export function minuteOfDay(value: Instant): number {
  return mod(value, MINUTES_PER_DAY);
}

export function dayStart(day: number): Instant {
  return day * MINUTES_PER_DAY;
}

/** « 2026-10-26 » */
export function formatDate(day: number): string {
  const { year, month, date } = dateParts(day);
  return `${String(year).padStart(4, '0')}-${pad2(month)}-${pad2(date)}`;
}

/** ISO text without zone, « 2026-10-26T06:30 ». */
export function formatInstant(value: Instant): string {
  return `${formatDate(instantDay(value))}T${formatHhmm(minuteOfDay(value))}`;
}

/** `390` -> « 06:30 », `1440` -> « 24:00 ». */
export function formatHhmm(minutes: number): string {
  const hours = Math.floor(minutes / MINUTES_PER_HOUR);
  return `${pad2(hours)}:${pad2(mod(minutes, MINUTES_PER_HOUR))}`;
}

export function daysInMonth(year: number, month: number): number {
  if (month === 2) {
    const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
    return leap ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/** Day number of a real calendar date (years 100 to 9999), null for « 31.02 ». */
export function makeDate(year: number, month: number, day: number): number | null {
  const valid =
    Number.isInteger(year) &&
    Number.isInteger(month) &&
    Number.isInteger(day) &&
    year >= 100 &&
    year <= 9999 &&
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= daysInMonth(year, month);
  return valid ? dayNumber(year, month, day) : null;
}

/** Reads « 2026-10-26T06:30 » (space, seconds, blanks around allowed); null when unreadable. */
export function parseInstant(text: string): Instant | null {
  const match = INSTANT_RE.exec(text.trim());
  if (!match) return null;
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const date = makeDate(year, month, day);
  if (date === null) return null;
  try {
    return instant(date, hour, minute);
  } catch (error) {
    if (error instanceof ValueError) return null;
    throw error;
  }
}

/** Python's `date.isocalendar()`: ISO year, week and weekday (Monday = 1). */
export function isoCalendar(day: number): { year: number; week: number; weekday: number } {
  const thursday = day - weekday(day) + 3;
  const { year } = dateParts(thursday);
  const firstMonday = mondayOf(dayNumber(year, 1, 4));
  return { year, week: Math.floor((day - firstMonday) / 7) + 1, weekday: weekday(day) + 1 };
}

/** Python's `date.fromisocalendar()`; null where Python raises (week 53 of a 52-week year…). */
export function fromIsoCalendar(year: number, week: number, isoWeekday: number): number | null {
  if (!Number.isInteger(year) || year < 100 || year > 9999) return null;
  const weeks = isoCalendar(dayNumber(year, 12, 28)).week;
  if (!Number.isInteger(week) || week < 1 || week > weeks) return null;
  if (!Number.isInteger(isoWeekday) || isoWeekday < 1 || isoWeekday > 7) return null;
  return mondayOf(dayNumber(year, 1, 4)) + (week - 1) * 7 + (isoWeekday - 1);
}
