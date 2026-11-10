/**
 * Texts and axis details of the planning screens, as the desktop writes them
 * (`ui/planning/planning_tab.py` `period_text`, `core/planning/timescale.py`).
 */

import type { WorkCalendar } from '@/lib/planning/calendar';
import { dayStart, formatHhmm, instantDay, minuteOfDay, type Instant } from '@/lib/planning/instant';
import type { PlanningMeta } from '@/lib/planning/models';
import { dateParts, dayNumber, isoWeek, mondayOf, parseDate, weekday } from '@/lib/planning/walltime';
import { weekdaysBetween, type DayInfo } from '@/lib/planning/week';

export type ViewKind = 'day' | 'week' | 'month';
export const VIEW_LABELS: Record<ViewKind, string> = { day: 'Jour', week: 'Semaine', month: 'Mois' };

const WEEKDAY_NAMES = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'];
const WEEKDAY_SHORT = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'];
const MONTH_NAMES = [
  'janvier',
  'février',
  'mars',
  'avril',
  'mai',
  'juin',
  'juillet',
  'août',
  'septembre',
  'octobre',
  'novembre',
  'décembre',
];
const MONTH_SHORT = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

function capitalized(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

function shortDay(day: number): string {
  const { date, month } = dateParts(day);
  return `${date} ${MONTH_SHORT[month - 1]}`;
}

/** « S46 · 9 – 13 nov. », « Jeu 12 nov. 2026 », « Nov. 2026 » (the short `period_text`). */
export function periodText(view: ViewKind, anchor: number): string {
  const { year, month } = dateParts(anchor);
  if (view === 'day') return `${capitalized(WEEKDAY_SHORT[weekday(anchor)])} ${shortDay(anchor)} ${year}`;
  if (view === 'month') return `${capitalized(MONTH_NAMES[month - 1])} ${year}`;
  const first = mondayOf(anchor);
  const last = first + 4;
  const start = dateParts(first).month === dateParts(last).month ? String(dateParts(first).date) : shortDay(first);
  return `Semaine ${isoWeek(first)} · ${start} – ${shortDay(last)}`;
}

/** The days a view shows around `anchor`: one day, Monday-Friday, or the month's weekdays. */
export function viewDays(view: ViewKind, anchor: number): number[] {
  if (view === 'day') return [anchor];
  if (view === 'week') return weekdaysBetween(mondayOf(anchor), mondayOf(anchor) + 4);
  const { year, month } = dateParts(anchor);
  const first = dayNumber(year, month, 1);
  const next = month === 12 ? dayNumber(year + 1, 1, 1) : dayNumber(year, month + 1, 1);
  return weekdaysBetween(first, next - 1);
}

/** Anchor moved by `steps` views: shown days for the day view (Friday + 1 is Monday). */
export function shiftView(view: ViewKind, anchor: number, steps: number): number {
  if (view === 'week') return anchor + 7 * steps;
  if (view === 'month') {
    const { year, month } = dateParts(anchor);
    const index = year * 12 + month - 1 + steps;
    return dayNumber(Math.floor(index / 12), (index % 12) + 1, 1);
  }
  let day = anchor;
  for (let i = 0; i < Math.abs(steps); i += 1) {
    do day += Math.sign(steps);
    while (weekday(day) > 4);
  }
  return day;
}

/** A weekday shown by the views: Saturday and Sunday move to the next Monday. */
export function shownDay(day: number): number {
  return weekday(day) > 4 ? day + (7 - weekday(day)) : day;
}

/** Holiday or closure label of a day, and which half-days are worked (calendar of the default canton). */
export function dayInfoFrom(calendar: WorkCalendar, meta: PlanningMeta | null): (day: number) => DayInfo {
  const closures = (meta?.closures ?? [])
    .map((closure) => ({ first: parseDate(closure.first_day), last: parseDate(closure.last_day), label: closure.label || 'Fermeture' }))
    .filter((closure): closure is { first: number; last: number; label: string } => closure.first !== null && closure.last !== null);
  return (day) => {
    const holiday = calendar.holiday(day);
    const closure = closures.find((candidate) => candidate.first <= day && day <= candidate.last);
    const periods = calendar.dayPeriods(day).map(([start, end]) => [minuteOfDay(start), minuteOfDay(end)] as const);
    const morning = periods.some(([start]) => start < 12 * 60);
    const afternoon = periods.some(([, end]) => end > 12 * 60);
    const label = holiday?.name ?? closure?.label ?? '';
    return { label, closed: !periods.length, worked: [morning, afternoon] };
  };
}

/** Start of the morning or of the afternoon of `day` on `calendar` (12:00 when it has no afternoon). */
export function halfDayStart(calendar: WorkCalendar, day: number, afternoon: boolean): Instant {
  const week = calendar.periodsOfWeekday(weekday(day));
  if (!afternoon) return dayStart(day) + (week[0]?.[0] ?? 6 * 60 + 30);
  return dayStart(day) + (week[1]?.[0] ?? 12 * 60);
}

/** « jeu 12.11 06:30 » */
export function describeInstant(value: Instant): string {
  const day = instantDay(value);
  const { date, month } = dateParts(day);
  return `${WEEKDAY_SHORT[weekday(day)]} ${String(date).padStart(2, '0')}.${String(month).padStart(2, '0')} ${formatHhmm(minuteOfDay(value))}`;
}

/** « jeudi 12.11 » */
export function describeDay(day: number): string {
  const { date, month } = dateParts(day);
  return `${WEEKDAY_NAMES[weekday(day)]} ${String(date).padStart(2, '0')}.${String(month).padStart(2, '0')}`;
}

/** « Aujourd’hui », « Demain », else « Lun 9 nov. », as the iPhone's date wheel names a day. */
export function wheelDayLabel(day: number, today: number): string {
  if (day === today) return 'Aujourd’hui';
  if (day === today + 1) return 'Demain';
  return `${capitalized(WEEKDAY_SHORT[weekday(day)])} ${shortDay(day)}`;
}

/** « Lu 9 », the column heading. */
export function columnHeading(day: number, compact = false): string {
  const { date } = dateParts(day);
  if (compact) return String(date);
  return `${capitalized(WEEKDAY_SHORT[weekday(day)]).slice(0, 2)} ${date}`;
}
