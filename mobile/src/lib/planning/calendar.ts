/**
 * Working calendar of the workshop (`core/planning/calendar.py`): hours per weekday,
 * Geneva and Vaud public holidays, company closures.
 *
 * Monday to Thursday are worked 06:30-12:00 and 13:00-16:30, Friday 06:30-12:00 (41 h 30
 * per week); Saturday and Sunday are never worked. Instants are wall-clock minutes
 * (`instant.ts`); durations are worked minutes, so the lunch break, Friday afternoon, the
 * weekend and holidays never count. Days are day numbers (`walltime.ts`).
 */

import {
  dayStart,
  type Instant,
  instantDay,
  MINUTES_PER_DAY,
  minuteOfDay,
  mod,
  roundHalfEven,
  ValueError,
} from './instant';
import { dateParts, dayNumber, parseDate, weekday } from './walltime';

/** Worked minutes since midnight, start included, end excluded. */
export type WorkPeriod = readonly [number, number];

/** Worked periods per weekday (0 = Monday); a missing weekday is not worked. */
export type WorkWeek = Readonly<Record<number, readonly WorkPeriod[]>>;

export const FULL_DAY: readonly WorkPeriod[] = [
  [6 * 60 + 30, 12 * 60],
  [13 * 60, 16 * 60 + 30],
];
export const FRIDAY: readonly WorkPeriod[] = [[6 * 60 + 30, 12 * 60]];
export const DEFAULT_WORK_WEEK: WorkWeek = { 0: FULL_DAY, 1: FULL_DAY, 2: FULL_DAY, 3: FULL_DAY, 4: FRIDAY };
export const SNAP_MINUTES = 30;
export const CANTONS: readonly string[] = ['GE', 'VD'];
/** A search for a worked instant stops after ten years without one. */
export const MAX_SEARCH_DAYS = 3660;
export const DAYS_PER_WEEK = 7;

const JEUNE_GENEVOIS_SHIFT = 4; // Thursday after the first Sunday of September.
const ASCENSION_SHIFT = 39;
const WHIT_MONDAY_SHIFT = 50;
const NO_WORKDAY = 'Aucun jour ouvrable dans le calendrier.';

export type Holiday = { day: number; name: string; cantons: readonly string[] };

/** Gregorian Easter Sunday (anonymous Gregorian algorithm), as a day number. */
export function easterSunday(year: number): number {
  const golden = year % 19;
  const century = Math.floor(year / 100);
  const yearOfCentury = year % 100;
  const leapCenturies = Math.floor(century / 4);
  const centuryRest = century % 4;
  const correction = Math.floor((century + 8) / 25);
  const moon = Math.floor((century - correction + 1) / 3);
  const epact = mod(19 * golden + century - leapCenturies - moon + 15, 30);
  const quarter = Math.floor(yearOfCentury / 4);
  const quarterRest = yearOfCentury % 4;
  const dayOfWeek = mod(32 + 2 * centuryRest + 2 * quarter - epact - quarterRest, 7);
  const shift = Math.floor((golden + 11 * epact + 22 * dayOfWeek) / 451);
  const total = epact + dayOfWeek - 7 * shift + 114;
  return dayNumber(year, Math.floor(total / 31), (total % 31) + 1);
}

function nthSunday(year: number, month: number, nth: number): number {
  const first = dayNumber(year, month, 1);
  return first + mod(6 - weekday(first), 7) + 7 * (nth - 1);
}

/** Official public holidays of Geneva and Vaud in `year`, limited to `cantons`. */
export function cantonalHolidays(year: number, cantons: Iterable<string> = CANTONS): Map<number, Holiday> {
  const easter = easterSunday(year);
  const both = ['GE', 'VD'];
  const entries: [number, string, string[]][] = [
    [dayNumber(year, 1, 1), 'Nouvel An', both],
    [dayNumber(year, 1, 2), 'Saint-Berchtold', ['VD']],
    [easter - 2, 'Vendredi saint', both],
    [easter + 1, 'Lundi de Pâques', both],
    [easter + ASCENSION_SHIFT, 'Ascension', both],
    [easter + WHIT_MONDAY_SHIFT, 'Lundi de Pentecôte', both],
    [dayNumber(year, 8, 1), 'Fête nationale', both],
    [nthSunday(year, 9, 1) + JEUNE_GENEVOIS_SHIFT, 'Jeûne genevois', ['GE']],
    [nthSunday(year, 9, 3) + 1, 'Lundi du Jeûne', ['VD']],
    [dayNumber(year, 12, 25), 'Noël', both],
    [dayNumber(year, 12, 31), 'Restauration de la République', ['GE']],
  ];
  const wanted = new Set(Array.from(cantons, (canton) => canton.toUpperCase()));
  const holidays = new Map<number, Holiday>();
  for (const [day, name, where] of entries) {
    const kept = where.filter((canton) => wanted.has(canton));
    if (kept.length) holidays.set(day, { day, name, cantons: kept });
  }
  return holidays;
}

/** « ge » -> « GE »; throws ValueError for a canton the planning does not know. */
export function normalizeCanton(text: string): string {
  const canton = text.trim().toUpperCase();
  if (!CANTONS.includes(canton)) {
    throw new ValueError(`Canton inconnu pour le planning: '${text}'`);
  }
  return canton;
}

/** Round to the nearest `step` on the wall clock, halves up, inside the same day. */
export function snap(value: Instant, step: number = SNAP_MINUTES): Instant {
  const minutes = minuteOfDay(value);
  const snapped = Math.min(
    Math.max(Math.floor((minutes + Math.floor(step / 2)) / step) * step, 0),
    MINUTES_PER_DAY,
  );
  return value - minutes + snapped;
}

function checkedPeriods(periods: Iterable<readonly [unknown, unknown]>): WorkPeriod[] {
  const ordered: WorkPeriod[] = Array.from(
    periods,
    ([start, end]): WorkPeriod => [Math.trunc(Number(start)), Math.trunc(Number(end))],
  ).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let previousEnd = 0;
  for (const [start, end] of ordered) {
    if (!(previousEnd <= start && start < end && end <= MINUTES_PER_DAY)) {
      throw new ValueError('Horaires de travail invalides.');
    }
    previousEnd = end;
  }
  return ordered;
}

function halfRound(fraction: number): number {
  return roundHalfEven(fraction * 2) / 2;
}

/** Days of the periods `first` to `last` of a day of `count` periods: all is 1 j. */
function sameDayDays(first: number, last: number, count: number): number {
  const periods = last - first + 1;
  return periods === count ? 1 : periods / 2;
}

export type WorkCalendarOptions = {
  week?: WorkWeek;
  cantons?: Iterable<string>;
  closures?: Iterable<number>;
};

/** Worked periods of one canton, with company closures. */
export class WorkCalendar {
  private readonly weekPeriods: Map<number, readonly WorkPeriod[]>;
  private readonly cantonCodes: readonly string[];
  private readonly closures: ReadonlySet<number>;
  private readonly holidays = new Map<number, Map<number, Holiday>>();

  constructor({ week = DEFAULT_WORK_WEEK, cantons = ['GE'], closures = [] }: WorkCalendarOptions = {}) {
    this.weekPeriods = new Map();
    for (const [key, periods] of Object.entries(week)) {
      const day = Number(key);
      if (!(Number.isInteger(day) && day >= 0 && day < DAYS_PER_WEEK)) continue;
      const checked = checkedPeriods(periods ?? []);
      if (checked.length) this.weekPeriods.set(day, checked);
    }
    if (!this.weekPeriods.size) throw new ValueError('Horaires de travail vides.');
    this.cantonCodes = Array.from(cantons, normalizeCanton);
    this.closures = new Set(closures);
  }

  get week(): Record<number, readonly WorkPeriod[]> {
    return Object.fromEntries(this.weekPeriods);
  }

  get cantons(): readonly string[] {
    return this.cantonCodes;
  }

  /** Weekdays with worked periods (Monday to Friday by default). */
  get workdays(): ReadonlySet<number> {
    return new Set(this.weekPeriods.keys());
  }

  /** Earliest worked minute of any weekday: the left edge of a displayed day. */
  get dayStartMinute(): number {
    return Math.min(...Array.from(this.weekPeriods.values(), (periods) => periods[0][0]));
  }

  /** Latest worked minute of any weekday: the right edge of a displayed day. */
  get dayEndMinute(): number {
    return Math.max(...Array.from(this.weekPeriods.values(), (periods) => periods[periods.length - 1][1]));
  }

  /** Worked minutes of a week without holiday (2490, 41 h 30, by default). */
  get minutesPerWeek(): number {
    let total = 0;
    for (const periods of this.weekPeriods.values()) {
      for (const [start, end] of periods) total += end - start;
    }
    return total;
  }

  periodsOfWeekday(day: number): readonly WorkPeriod[] {
    return this.weekPeriods.get(day) ?? [];
  }

  private holidaysOf(year: number): Map<number, Holiday> {
    let holidays = this.holidays.get(year);
    if (holidays === undefined) {
      holidays = cantonalHolidays(year, this.cantonCodes);
      this.holidays.set(year, holidays);
    }
    return holidays;
  }

  holiday(day: number): Holiday | null {
    return this.holidaysOf(dateParts(day).year).get(day) ?? null;
  }

  holidaysBetween(first: number, last: number): Holiday[] {
    const found: Holiday[] = [];
    for (let year = dateParts(first).year; year <= dateParts(last).year; year += 1) {
      for (const [day, holiday] of this.holidaysOf(year)) {
        if (first <= day && day <= last) found.push(holiday);
      }
    }
    return found.sort((a, b) => a.day - b.day);
  }

  isClosed(day: number): boolean {
    return this.closures.has(day);
  }

  isWorkday(day: number): boolean {
    return this.weekPeriods.has(weekday(day)) && !this.closures.has(day) && this.holiday(day) === null;
  }

  /** Worked minutes of `day` (540 Monday to Thursday, 330 Friday, 0 otherwise). */
  workedMinutes(day: number): number {
    return this.dayPeriods(day).reduce((total, [start, end]) => total + end - start, 0);
  }

  /** First working day strictly after `day`. */
  nextWorkday(day: number): number {
    let candidate = day;
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      candidate += 1;
      if (this.isWorkday(candidate)) return candidate;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /** Last working day strictly before `day`. */
  previousWorkday(day: number): number {
    let candidate = day;
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      candidate -= 1;
      if (this.isWorkday(candidate)) return candidate;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /** Worked periods of `day` as instants; empty for weekends, holidays, closures. */
  dayPeriods(day: number): [Instant, Instant][] {
    if (!this.isWorkday(day)) return [];
    const base = dayStart(day);
    return this.periodsOfWeekday(weekday(day)).map(([start, end]): [Instant, Instant] => [base + start, base + end]);
  }

  /** Worked minutes in `[start, end)`. */
  workBetween(start: Instant, end: Instant): number {
    if (end <= start) return 0;
    let total = 0;
    const last = instantDay(end - 1);
    for (let day = instantDay(start); day <= last; day += 1) {
      for (const [low, high] of this.dayPeriods(day)) {
        total += Math.max(0, Math.min(end, high) - Math.max(start, low));
      }
    }
    return total;
  }

  /** Whether the minute starting at `value` is worked. */
  isWorked(value: Instant): boolean {
    return this.dayPeriods(instantDay(value)).some(([low, high]) => low <= value && value < high);
  }

  /** First worked instant at or after `value`: a start never sits in a break. */
  workingStart(value: Instant): Instant {
    let day = instantDay(value);
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      for (const [low, high] of this.dayPeriods(day)) {
        if (value < high) return Math.max(value, low);
      }
      day += 1;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /** Last worked instant at or before `value`: an end never sits in a break. */
  workingEnd(value: Instant): Instant {
    let day = instantDay(value);
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      for (const [low, high] of this.dayPeriods(day).reverse()) {
        if (value > low) return Math.min(value, high);
      }
      day -= 1;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /**
   * Instant reached after `minutes` of work from `start`.
   *
   * An end that falls exactly at the end of a period stays there (12:00 or 16:30)
   * instead of jumping to the next period's start.
   */
  addWork(start: Instant, minutes: number): Instant {
    if (minutes < 0) return this.subtractWork(start, -minutes);
    const current = this.workingStart(start);
    let remaining = minutes;
    let day = instantDay(current);
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      for (const [low, high] of this.dayPeriods(day)) {
        if (high <= current) continue;
        const begin = Math.max(current, low);
        if (remaining <= high - begin) return begin + remaining;
        remaining -= high - begin;
      }
      day += 1;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /** Instant from which `minutes` of work end exactly at `end`. */
  subtractWork(end: Instant, minutes: number): Instant {
    if (minutes < 0) return this.addWork(end, -minutes);
    const current = this.workingEnd(end);
    let remaining = minutes;
    let day = instantDay(current);
    for (let step = 0; step < MAX_SEARCH_DAYS; step += 1) {
      for (const [low, high] of this.dayPeriods(day).reverse()) {
        if (low >= current) continue;
        const finish = Math.min(current, high);
        if (remaining <= finish - low) return finish - remaining;
        remaining -= finish - low;
      }
      day -= 1;
    }
    throw new ValueError(NO_WORKDAY);
  }

  /**
   * End of a duration counted in working days, as people say it (« 3 j »).
   *
   * Days are counted as `wholeDays` reads them back, so a bar says what it was given:
   * the period of `start` counts from its beginning (an afternoon start is half of its
   * day, a start at 09:00 the whole morning), a day of two periods has two halves and
   * Friday, worked until noon, is one whole day. The end is the first period end where
   * the count reaches `days`, rounded to the half day (½ j at least): « 2 j » from
   * Thursday 13:00 ends on Monday at 12:00. Where Friday makes the count jump by a whole
   * day, the end goes past rather than short (« 1 j » from Thursday 13:00 ends on Friday
   * at 12:00, nine hours of work).
   */
  addDays(start: Instant, days: number): Instant {
    const target = Math.max(roundHalfEven(days * 2), 1) / 2;
    const current = this.workingStart(start);
    const first = instantDay(current);
    let periods = this.dayPeriods(first);
    const index = periods.findIndex(([, high]) => current < high);
    for (let last = index; last < periods.length; last += 1) {
      if (sameDayDays(index, last, periods.length) >= target) return periods[last][1];
    }
    const leading = halfRound((periods.length - index) / periods.length);
    let day = first;
    for (let between = 0; between < MAX_SEARCH_DAYS; between += 1) {
      // `between` whole working days lie between the first day and this one.
      day = this.nextWorkday(day);
      periods = this.dayPeriods(day);
      for (let last = 0; last < periods.length; last += 1) {
        if (between + leading + halfRound((last + 1) / periods.length) >= target) return periods[last][1];
      }
    }
    throw new ValueError(NO_WORKDAY);
  }

  /**
   * Working days covered by `[start, end)` when it is exactly whole or half days.
   *
   * Null when the span starts or ends inside a period: it is then shown in hours.
   * `addDays` counts the same way.
   */
  wholeDays(start: Instant, end: Instant): number | null {
    if (end <= start) return null;
    const first = instantDay(start);
    const last = instantDay(end - 1);
    const firstPeriods = this.dayPeriods(first);
    const lastPeriods = this.dayPeriods(last);
    if (!firstPeriods.length || !lastPeriods.length) return null;
    const startIndex = firstPeriods.findIndex(([low]) => low === start);
    const endIndex = lastPeriods.findIndex(([, high]) => high === end);
    if (startIndex < 0 || endIndex < 0) return null;
    if (first === last) return sameDayDays(startIndex, endIndex, firstPeriods.length);
    const days = this.daysBetween(first, last).length;
    const leading = (firstPeriods.length - startIndex) / firstPeriods.length;
    const trailing = (endIndex + 1) / lastPeriods.length;
    return days - 2 + halfRound(leading) + halfRound(trailing);
  }

  /** Working days from `first` to `last` included. */
  daysBetween(first: number, last: number): number[] {
    const days: number[] = [];
    for (let day = first; day <= last; day += 1) {
      if (this.isWorkday(day)) days.push(day);
    }
    return days;
  }
}

/** Shared settings as stored in `meta__meta.json` (only what the calendar reads). */
export type CalendarMeta = {
  work_week?: Record<string, readonly (readonly [number, number])[]>;
  default_canton?: string;
  closures?: readonly { first_day: string; last_day: string }[];
};

function knownCanton(text: string | undefined): string {
  const canton = (text ?? '').trim().toUpperCase();
  return CANTONS.includes(canton) ? canton : '';
}

/** Every day of the closures, first and last included; unreadable ones are skipped. */
export function closureDays(closures: CalendarMeta['closures']): number[] {
  const days: number[] = [];
  for (const closure of closures ?? []) {
    const first = parseDate(closure.first_day ?? '');
    const last = parseDate(closure.last_day ?? '');
    if (first === null || last === null) continue;
    for (let day = first; day <= last; day += 1) days.push(day);
  }
  return days;
}

/**
 * Calendar of a row, as the desktop's `calendar_resolver` builds it: the shared hours
 * (`work_week`, keys may be JSON strings), the closures expanded to every day, and the
 * resource's canton, else the shared default canton (« GE » when unknown). A stored week
 * the calendar refuses falls back to the default week rather than failing.
 */
export function calendarFromMeta(meta: CalendarMeta | null | undefined, canton?: string): WorkCalendar {
  const chosen = knownCanton(canton) || knownCanton(meta?.default_canton) || CANTONS[0];
  const closures = closureDays(meta?.closures);
  if (meta?.work_week) {
    try {
      return new WorkCalendar({ week: meta.work_week, cantons: [chosen], closures });
    } catch (error) {
      if (!(error instanceof ValueError)) throw error;
    }
  }
  return new WorkCalendar({ cantons: [chosen], closures });
}
