/**
 * Night work (« travail de nuit », `core/planning/night.py`): tasks outside the working
 * calendar, e.g. airport jobs.
 *
 * A night task keeps its exact wall-clock times (snapped to the half hour only) and lasts
 * real elapsed time, never worked time. A night belongs to the evening it starts: it
 * starts between 18:00 and 23:30, or after midnight before 06:00 (then it is still the
 * previous evening's night), and ends by 06:30 the next morning at the latest. Nights
 * start Monday to Thursday evenings only: a Thursday night ends on Friday morning, but
 * there is no night work on Friday evening nor during the weekend. Around every night the
 * person rests `MIN_REST_MINUTES` (8 h) before its start and after its end.
 */

import { SNAP_MINUTES, snap } from './calendar';
import {
  dayStart,
  formatHhmm,
  type Instant,
  instantDay,
  MINUTES_PER_DAY,
  MINUTES_PER_HOUR,
  minuteOfDay,
} from './instant';
import { dateParts, weekday } from './walltime';

/** A night starts at 18:00 at the earliest (in the evening of its day). */
export const NIGHT_EARLIEST_START = 18 * MINUTES_PER_HOUR;
/** A start after midnight but before 06:00 belongs to the previous evening's night. */
export const NIGHT_MORNING_START = 6 * MINUTES_PER_HOUR;
/** A night ends at 06:30 the next morning at the latest. */
export const NIGHT_LATEST_END = 6 * MINUTES_PER_HOUR + 30;
export const DEFAULT_NIGHT_START = 21 * MINUTES_PER_HOUR;
export const DEFAULT_NIGHT_END = 5 * MINUTES_PER_HOUR;
/** 21:00 to 05:00: 8 h. */
export const DEFAULT_NIGHT_MINUTES = MINUTES_PER_DAY - DEFAULT_NIGHT_START + DEFAULT_NIGHT_END;
export const MIN_NIGHT_MINUTES = SNAP_MINUTES;
/** Evenings a night may start: Monday to Thursday. */
export const NIGHT_WEEKDAYS: ReadonlySet<number> = new Set([0, 1, 2, 3]);
/** Rest kept before and after a night: 8 h (not the usual 11 h). */
export const MIN_REST_MINUTES = 480;

export const MOON = '\u{1F319}';
export const EN_DASH = '–';
export const ARROW = '→';
export const NIGHT_WEEKEND_MESSAGE = 'Pas de travail de nuit le vendredi soir ni le week-end';
export const NIGHT_HOURS_MESSAGE = 'Une nuit commence entre 18:00 et 06:00 et finit à 06:30 au plus tard';
export const NIGHT_SHORT_MESSAGE = 'Une nuit dure au moins 30 minutes';
export const WEEKDAY_SHORT = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'] as const;

/** Evening day of the night starting at `start`; null for a daytime start. */
export function nightOf(start: Instant): number | null {
  const minute = minuteOfDay(start);
  if (minute >= NIGHT_EARLIEST_START) return instantDay(start);
  if (minute < NIGHT_MORNING_START) return instantDay(start) - 1;
  return null;
}

/** Day whose column shows a night task: its evening, else the day it starts. */
export function nightDay(start: Instant): number {
  return nightOf(start) ?? instantDay(start);
}

/** Night of `day`: from 18:00 to 06:30 the next morning. */
export function nightWindow(day: number): [Instant, Instant] {
  const base = dayStart(day);
  return [base + NIGHT_EARLIEST_START, base + MINUTES_PER_DAY + NIGHT_LATEST_END];
}

/** Whether a night may start on the evening of `day` (Monday to Thursday). */
export function nightAllowed(day: number): boolean {
  return NIGHT_WEEKDAYS.has(weekday(day));
}

/** Why `[start, end)` is not a valid night, null when it is one. */
export function nightError(start: Instant, end: Instant): string | null {
  const day = nightOf(start);
  if (day === null) return NIGHT_HOURS_MESSAGE;
  if (!nightAllowed(day)) return NIGHT_WEEKEND_MESSAGE;
  if (end - start < MIN_NIGHT_MINUTES) return NIGHT_SHORT_MESSAGE;
  if (end > nightWindow(day)[1]) return NIGHT_HOURS_MESSAGE;
  return null;
}

export function isNightSpan(start: Instant, end: Instant): boolean {
  return nightError(start, end) === null;
}

/** 21:00 on `day` to 05:00 the next morning. */
export function defaultNight(day: number): [Instant, Instant] {
  const base = dayStart(day);
  return [base + DEFAULT_NIGHT_START, base + MINUTES_PER_DAY + DEFAULT_NIGHT_END];
}

/** Start of a night placed at `value`: snapped, or 21:00 of its day when in daytime. */
export function nightStartAt(value: Instant): Instant {
  const snapped = snap(value);
  if (nightOf(snapped) !== null) return snapped;
  return defaultNight(instantDay(value))[0];
}

/** Elapsed minutes of a night estimate: rounded up to 30 minutes, 8 h when none. */
export function nightMinutes(minutes: number): number {
  if (minutes <= 0) return DEFAULT_NIGHT_MINUTES;
  return Math.max(MIN_NIGHT_MINUTES, Math.ceil(minutes / SNAP_MINUTES) * SNAP_MINUTES);
}

/** Night from `start` lasting `minutes`, cut at 06:30 the next morning. */
export function fittedNight(start: Instant, minutes: number): [Instant, Instant] {
  const day = nightDay(start);
  const end = Math.min(start + nightMinutes(minutes), nightWindow(day)[1]);
  return [start, Math.max(end, start + MIN_NIGHT_MINUTES)];
}

/** Evening `nights` nights after `day` (before when negative), Friday to Sunday skipped. */
export function shiftNights(day: number, nights: number): number {
  const step = nights >= 0 ? 1 : -1;
  let current = day;
  for (let count = 0; count < Math.abs(nights); count += 1) {
    current += step;
    while (!nightAllowed(current)) current += step;
  }
  return current;
}

/** Rest kept around a night: 8 h before its start and 8 h after its end. */
export function restSpans(start: Instant, end: Instant): [[Instant, Instant], [Instant, Instant]] {
  return [
    [start - MIN_REST_MINUTES, start],
    [end, end + MIN_REST_MINUTES],
  ];
}

/** Hours of a night, « 22:00 » to « 05:00 » joined by an en dash. */
export function nightHours(start: Instant, end: Instant): string {
  return `${formatHhmm(minuteOfDay(start))}${EN_DASH}${formatHhmm(minuteOfDay(end))}`;
}

/** Elapsed time: `480` -> « 8 h », `450` -> « 7 h 30 », `30` -> « 30 min ». */
export function elapsedText(minutes: number): string {
  const total = Math.max(minutes, 0);
  const hours = Math.floor(total / MINUTES_PER_HOUR);
  const rest = total % MINUTES_PER_HOUR;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${String(rest).padStart(2, '0')}` : `${hours} h`;
}

/** « jeu 29.10 22:00 » */
export function momentText(value: Instant): string {
  const day = instantDay(value);
  const { month, date } = dateParts(day);
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${WEEKDAY_SHORT[weekday(day)]} ${pad(date)}.${pad(month)} ${formatHhmm(minuteOfDay(value))}`;
}

/** « 🌙 nuit jeu 29.10 22:00 → ven 30.10 05:00 · 7 h » */
export function nightLabel(start: Instant, end: Instant): string {
  return `${MOON} nuit ${momentText(start)} ${ARROW} ${momentText(end)} · ${elapsedText(end - start)}`;
}

/** « Repos : dès lun 13:00 avant · jusqu'à mar 13:00 après » */
export function restText(start: Instant, end: Instant): string {
  const [[before], [, after]] = restSpans(start, end);
  return `Repos : dès ${momentText(before)} avant · jusqu'à ${momentText(after)} après`;
}
