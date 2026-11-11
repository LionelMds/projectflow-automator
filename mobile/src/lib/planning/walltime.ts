/**
 * Swiss wall-clock times, as the desktop keeps them (`core/planning/walltime.py`):
 * « 2026-10-26T06:30 », no time zone, so 06:30 stays 06:30 everywhere. Here a day
 * is a whole number of days since 1970-01-01 (computed in UTC, never shifted by the
 * phone's zone) and a time is that day plus minutes since midnight.
 */

export const MINUTES_PER_DAY = 1440;
const MS_PER_DAY = 86_400_000;
const WALL_TIME_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/;
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/;

export type WallTime = { day: number; minutes: number };

export function dayNumber(year: number, month: number, day: number): number {
  return Math.round(Date.UTC(year, month - 1, day) / MS_PER_DAY);
}

export function parseDate(text: string): number | null {
  const match = DATE_RE.exec(text.trim());
  return match ? dayNumber(Number(match[1]), Number(match[2]), Number(match[3])) : null;
}

export function parseWallTime(text: string | null | undefined): WallTime | null {
  if (!text) return null;
  const match = WALL_TIME_RE.exec(text.trim());
  if (!match) return null;
  const [, year, month, day, hour, minute] = match.map(Number);
  return { day: dayNumber(year, month, day), minutes: hour * 60 + minute };
}

/** Day of the week, Monday = 0 … Sunday = 6 (Python's `weekday()`). */
export function weekday(day: number): number {
  // 1970-01-01 was a Thursday.
  return (((day + 3) % 7) + 7) % 7;
}

export function mondayOf(day: number): number {
  return day - weekday(day);
}

export function todayNumber(now: Date = new Date()): number {
  return dayNumber(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

export function dateParts(day: number): { year: number; month: number; date: number } {
  const value = new Date(day * MS_PER_DAY);
  return { year: value.getUTCFullYear(), month: value.getUTCMonth() + 1, date: value.getUTCDate() };
}

/** ISO 8601 week number, as the desktop's week labels. */
export function isoWeek(day: number): number {
  const thursday = day - weekday(day) + 3;
  const { year } = dateParts(thursday);
  const firstThursday = mondayOf(dayNumber(year, 1, 4)) + 3;
  return 1 + Math.round((thursday - firstThursday) / 7);
}

const WEEKDAYS = ['Lu', 'Ma', 'Me', 'Je', 'Ve', 'Sa', 'Di'];
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

export function shortDay(day: number): string {
  return `${WEEKDAYS[weekday(day)]} ${dateParts(day).date}`;
}

/** « 26.10 » */
export function dayMonth(day: number): string {
  const { month, date } = dateParts(day);
  return `${String(date).padStart(2, '0')}.${String(month).padStart(2, '0')}`;
}

/** « 9 – 13 nov. » for the working days of a week. */
export function weekRange(monday: number): string {
  const first = dateParts(monday);
  const last = dateParts(monday + 4);
  const end = `${last.date} ${MONTHS[last.month - 1]}`;
  return first.month === last.month ? `${first.date} – ${end}` : `${first.date} ${MONTHS[first.month - 1]} – ${end}`;
}

/** « 06:30 » */
export function hhmm(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

/** « lun 26.10 06:30 » */
export function describeWallTime(time: WallTime): string {
  const names = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'];
  return `${names[weekday(time.day)]} ${dayMonth(time.day)} ${hhmm(time.minutes)}`;
}
