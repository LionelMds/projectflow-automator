/** Port of `tests/unit/test_planning_calendar.py` (instants, holidays, working calendar). */

import {
  cantonalHolidays,
  calendarFromMeta,
  DEFAULT_WORK_WEEK,
  easterSunday,
  FRIDAY,
  FULL_DAY,
  type Holiday,
  snap,
  SNAP_MINUTES,
  WorkCalendar,
  type WorkWeek,
} from '@/lib/planning/calendar';
import {
  dayStart,
  formatDate,
  formatHhmm,
  formatInstant,
  type Instant,
  instant,
  instantDay,
  MINUTES_PER_DAY,
  minuteOfDay,
  parseInstant,
} from '@/lib/planning/instant';
import { dayNumber, weekday } from '@/lib/planning/walltime';

// Week of Monday 26 October 2026, without holiday.
const MON = dayNumber(2026, 10, 26);
const TUE = dayNumber(2026, 10, 27);
const WED = dayNumber(2026, 10, 28);
const THU = dayNumber(2026, 10, 29);
const FRI = dayNumber(2026, 10, 30);
const SAT = dayNumber(2026, 10, 31);
const SUN = dayNumber(2026, 11, 1);
const NEXT_MON = dayNumber(2026, 11, 2);
const PREVIOUS_FRI = dayNumber(2026, 10, 23);
// Easter 2026: Good Friday 3 April, Easter Monday 6 April.
const MAUNDY_THU = dayNumber(2026, 4, 2);
const GOOD_FRIDAY = dayNumber(2026, 4, 3);
const EASTER_MONDAY = dayNumber(2026, 4, 6);
const EASTER_TUE = dayNumber(2026, 4, 7);

function at(day: number, text: string): Instant {
  const [hours, minutes] = text.split(':').map(Number);
  return instant(day, hours, minutes);
}

const show = formatInstant;

const GE = new WorkCalendar();
const VD = new WorkCalendar({ cantons: ['VD'] });

// ------------------------------------------------------------- instants ----

describe('instants', () => {
  it('counts minutes from the day number', () => {
    const value = instant(MON, 6, 30);

    expect(value).toBe(MON * MINUTES_PER_DAY + 390);
    expect(instantDay(value)).toBe(MON);
    expect(minuteOfDay(value)).toBe(390);
    expect(dayStart(MON)).toBe(instant(MON));
    expect(instant(MON)).toBe(MON * MINUTES_PER_DAY);
  });

  it('accepts the end of the day as next midnight', () => {
    expect(instant(MON, 24, 0)).toBe(dayStart(TUE));
    expect(formatInstant(instant(MON, 24))).toBe('2026-10-27T00:00');
  });

  it.each([
    [24, 1],
    [-1, 0],
    [0, -1],
    [25, 0],
  ])('rejects %i:%i outside the day', (hour, minute) => {
    expect(() => instant(MON, hour, minute)).toThrow('Heure hors de la journee');
  });

  it('sorts and subtracts as minutes', () => {
    expect(at(FRI, '12:00')).toBeLessThan(at(SAT, '00:00'));
    expect(at(SAT, '00:00')).toBeLessThan(at(NEXT_MON, '06:30'));
    expect(at(TUE, '06:30') - at(MON, '16:30')).toBe(14 * 60);
  });

  it.each(['2026-10-26T06:30', '2026-01-01T00:00', '2026-12-31T23:59', '2028-02-29T16:30'])(
    'formats and parses %s back',
    (text) => {
      const value = parseInstant(text);

      expect(value).not.toBeNull();
      expect(formatInstant(value as Instant)).toBe(text);
      expect(parseInstant(formatInstant(value as Instant))).toBe(value);
    },
  );

  it.each(['2026-10-26 06:30', '2026-10-26T06:30:00', '2026-10-26T06:30:59.123', '  2026-10-26T06:30\n'])(
    'parses %j with a space, seconds or blanks around',
    (text) => {
      expect(parseInstant(text)).toBe(at(MON, '06:30'));
    },
  );

  it('parses 24:00 as next midnight', () => {
    expect(parseInstant('2026-10-26T24:00')).toBe(dayStart(TUE));
  });

  it.each([
    '',
    '2026-10-26',
    '26.10.2026 06:30',
    '2026-10-26T6:30',
    '2026-10-26T06:30Z',
    '2026-10-26T06:30+01:00',
    '2026-10-26T24:30',
    '2026-13-01T06:30',
    '2026-02-30T06:30',
    'lundi',
  ])('refuses the unreadable %j', (text) => {
    expect(parseInstant(text)).toBeNull();
  });

  it.each(['2026-10-26T06:60', '2026-10-26T11:75', '2026-10-26T23:60', '2026-10-26T24:30'])(
    'refuses minutes above 59 in %s',
    (text) => {
      expect(parseInstant(text)).toBeNull();
    },
  );

  it.each([
    [0, '00:00'],
    [390, '06:30'],
    [990, '16:30'],
    [1440, '24:00'],
  ])('formats %i as %s', (minutes, text) => {
    expect(formatHhmm(minutes)).toBe(text);
  });
});

// ------------------------------------------------------------ holidays ----

function names(holidays: Map<number, Holiday>): Record<string, string> {
  return Object.fromEntries(Array.from(holidays, ([day, holiday]) => [formatDate(day), holiday.name]));
}

describe('holidays', () => {
  it.each([
    [2024, dayNumber(2024, 3, 31)],
    [2025, dayNumber(2025, 4, 20)],
    [2026, dayNumber(2026, 4, 5)],
    [2027, dayNumber(2027, 3, 28)],
    [2038, dayNumber(2038, 4, 25)],
    [2285, dayNumber(2285, 3, 22)],
  ])('Easter Sunday of %i', (year, easter) => {
    expect(easterSunday(year)).toBe(easter);
    expect(weekday(easter)).toBe(6);
  });

  const GE_HOLIDAYS: Record<number, Record<string, string>> = {
    2025: {
      '2025-01-01': 'Nouvel An',
      '2025-04-18': 'Vendredi saint',
      '2025-04-21': 'Lundi de Pâques',
      '2025-05-29': 'Ascension',
      '2025-06-09': 'Lundi de Pentecôte',
      '2025-08-01': 'Fête nationale',
      '2025-09-11': 'Jeûne genevois',
      '2025-12-25': 'Noël',
      '2025-12-31': 'Restauration de la République',
    },
    2026: {
      '2026-01-01': 'Nouvel An',
      '2026-04-03': 'Vendredi saint',
      '2026-04-06': 'Lundi de Pâques',
      '2026-05-14': 'Ascension',
      '2026-05-25': 'Lundi de Pentecôte',
      '2026-08-01': 'Fête nationale',
      '2026-09-10': 'Jeûne genevois',
      '2026-12-25': 'Noël',
      '2026-12-31': 'Restauration de la République',
    },
    2027: {
      '2027-01-01': 'Nouvel An',
      '2027-03-26': 'Vendredi saint',
      '2027-03-29': 'Lundi de Pâques',
      '2027-05-06': 'Ascension',
      '2027-05-17': 'Lundi de Pentecôte',
      '2027-08-01': 'Fête nationale',
      '2027-09-09': 'Jeûne genevois',
      '2027-12-25': 'Noël',
      '2027-12-31': 'Restauration de la République',
    },
  };

  const VD_HOLIDAYS: Record<number, Record<string, string>> = {
    2025: {
      '2025-01-01': 'Nouvel An',
      '2025-01-02': 'Saint-Berchtold',
      '2025-04-18': 'Vendredi saint',
      '2025-04-21': 'Lundi de Pâques',
      '2025-05-29': 'Ascension',
      '2025-06-09': 'Lundi de Pentecôte',
      '2025-08-01': 'Fête nationale',
      '2025-09-22': 'Lundi du Jeûne',
      '2025-12-25': 'Noël',
    },
    2026: {
      '2026-01-01': 'Nouvel An',
      '2026-01-02': 'Saint-Berchtold',
      '2026-04-03': 'Vendredi saint',
      '2026-04-06': 'Lundi de Pâques',
      '2026-05-14': 'Ascension',
      '2026-05-25': 'Lundi de Pentecôte',
      '2026-08-01': 'Fête nationale',
      '2026-09-21': 'Lundi du Jeûne',
      '2026-12-25': 'Noël',
    },
    2027: {
      '2027-01-01': 'Nouvel An',
      '2027-01-02': 'Saint-Berchtold',
      '2027-03-26': 'Vendredi saint',
      '2027-03-29': 'Lundi de Pâques',
      '2027-05-06': 'Ascension',
      '2027-05-17': 'Lundi de Pentecôte',
      '2027-08-01': 'Fête nationale',
      '2027-09-20': 'Lundi du Jeûne',
      '2027-12-25': 'Noël',
    },
  };

  it.each([2025, 2026, 2027])('Geneva holidays of %i', (year) => {
    const holidays = cantonalHolidays(year, ['GE']);

    expect(names(holidays)).toEqual(GE_HOLIDAYS[year]);
    expect([...holidays.values()].every((holiday) => holiday.cantons.join() === 'GE')).toBe(true);
  });

  it.each([2025, 2026, 2027])('Vaud holidays of %i', (year) => {
    const holidays = cantonalHolidays(year, ['VD']);

    expect(names(holidays)).toEqual(VD_HOLIDAYS[year]);
    expect([...holidays.values()].every((holiday) => holiday.cantons.join() === 'VD')).toBe(true);
  });

  it.each([2025, 2026, 2027])('both cantons merge in %i and keep which canton observes each day', (year) => {
    const holidays = cantonalHolidays(year);

    const expected = new Set([...Object.keys(GE_HOLIDAYS[year]), ...Object.keys(VD_HOLIDAYS[year])]);
    expect(Object.keys(names(holidays)).sort()).toEqual([...expected].sort());
    expect(holidays.get(dayNumber(year, 1, 2))?.cantons).toEqual(['VD']);
    expect(holidays.get(dayNumber(year, 12, 31))?.cantons).toEqual(['GE']);
    expect(holidays.get(dayNumber(year, 12, 25))?.cantons).toEqual(['GE', 'VD']);
  });

  it('reads canton codes in any case', () => {
    expect(cantonalHolidays(2026, ['ge'])).toEqual(cantonalHolidays(2026, ['GE']));
    expect(cantonalHolidays(2026, [])).toEqual(new Map());
  });

  it.each([
    // 1 September 2024 is a Sunday: it is the first Sunday itself.
    [2024, dayNumber(2024, 9, 5), dayNumber(2024, 9, 16)],
    // 1 September 2022 is a Thursday: the Jeûne genevois is the next Thursday.
    [2022, dayNumber(2022, 9, 8), dayNumber(2022, 9, 19)],
    // 1 September 2029 is a Saturday.
    [2029, dayNumber(2029, 9, 6), dayNumber(2029, 9, 17)],
  ])('Jeûne dates around the first of September %i', (year, jeuneGenevois, lundiDuJeune) => {
    const holidays = cantonalHolidays(year);

    expect(holidays.get(jeuneGenevois)?.name).toBe('Jeûne genevois');
    expect(weekday(jeuneGenevois)).toBe(3);
    expect(holidays.get(lundiDuJeune)?.name).toBe('Lundi du Jeûne');
    expect(weekday(lundiDuJeune)).toBe(0);
  });
});

// ------------------------------------------------------- work calendar ----

describe('work calendar', () => {
  it('works 41 hours 30 from Monday to Friday in Geneva by default', () => {
    expect(GE.cantons).toEqual(['GE']);
    expect(GE.workdays).toEqual(new Set([0, 1, 2, 3, 4]));
    expect(GE.week).toEqual(DEFAULT_WORK_WEEK);
    expect(GE.periodsOfWeekday(0)).toEqual(FULL_DAY);
    expect(GE.periodsOfWeekday(4)).toEqual(FRIDAY);
    expect(GE.periodsOfWeekday(5)).toEqual([]);
    expect(GE.minutesPerWeek).toBe(2490);
    expect(GE.dayStartMinute).toBe(390);
    expect(GE.dayEndMinute).toBe(990);
  });

  it('knows the worked minutes per day', () => {
    expect([0, 1, 2, 3, 4, 5, 6].map((offset) => GE.workedMinutes(MON + offset))).toEqual([
      540, 540, 540, 540, 330, 0, 0,
    ]);
    expect(GE.workedMinutes(GOOD_FRIDAY)).toBe(0);
    expect(GE.workBetween(dayStart(MON), dayStart(NEXT_MON))).toBe(GE.minutesPerWeek);
  });

  it('has holidays that depend on the canton', () => {
    const saintBerchtold = dayNumber(2026, 1, 2);
    const restauration = dayNumber(2026, 12, 31);

    expect(GE.isWorkday(saintBerchtold)).toBe(true);
    expect(VD.isWorkday(saintBerchtold)).toBe(false);
    expect(GE.isWorkday(restauration)).toBe(false);
    expect(VD.isWorkday(restauration)).toBe(true);
    const holiday = GE.holiday(dayNumber(2026, 9, 10));
    expect(holiday).not.toBeNull();
    expect(holiday?.name).toBe('Jeûne genevois');
    expect(VD.holiday(dayNumber(2026, 9, 10))).toBeNull();
    expect(new WorkCalendar({ cantons: ['vd'] }).cantons).toEqual(['VD']);
  });

  it('lists the holidays between two days across years, in order', () => {
    const found = GE.holidaysBetween(dayNumber(2026, 12, 20), dayNumber(2027, 1, 10));

    expect(found.map((holiday) => [formatDate(holiday.day), holiday.name])).toEqual([
      ['2026-12-25', 'Noël'],
      ['2026-12-31', 'Restauration de la République'],
      ['2027-01-01', 'Nouvel An'],
    ]);
    expect(GE.holidaysBetween(MON, SUN)).toEqual([]);
  });

  it('does not work closures, which are not holidays', () => {
    const closed = new WorkCalendar({ closures: [TUE] });

    expect(closed.isClosed(TUE)).toBe(true);
    expect(closed.isWorkday(TUE)).toBe(false);
    expect(closed.holiday(TUE)).toBeNull();
    expect(closed.dayPeriods(TUE)).toEqual([]);
    expect(closed.minutesPerWeek).toBe(2490);
    expect(closed.workBetween(dayStart(MON), dayStart(NEXT_MON))).toBe(2490 - 540);
    expect(closed.addWork(at(MON, '16:00'), 60)).toBe(at(WED, '07:00'));
  });

  it('does not count weekends, holidays and closures as workdays', () => {
    expect(GE.isWorkday(MON)).toBe(true);
    expect(GE.isWorkday(FRI)).toBe(true);
    expect(GE.isWorkday(SAT)).toBe(false);
    expect(GE.isWorkday(SUN)).toBe(false);
    expect(GE.isWorkday(GOOD_FRIDAY)).toBe(false);
    expect(GE.isClosed(GOOD_FRIDAY)).toBe(false);
  });

  it('takes a custom week', () => {
    const calendar = new WorkCalendar({
      week: { 0: [[8 * 60, 12 * 60]], 2: [[13 * 60, 17 * 60]], 9: [] },
    });

    expect(calendar.workdays).toEqual(new Set([0, 2]));
    expect(calendar.minutesPerWeek).toBe(480);
    expect(calendar.dayStartMinute).toBe(480);
    expect(calendar.dayEndMinute).toBe(1020);
    expect(calendar.addWork(at(MON, '11:00'), 120)).toBe(at(WED, '14:00'));
  });

  it.each<[WorkWeek]>([
    [{}],
    [{ 5: [] }],
    [{ 0: [[600, 500]] }],
    [
      {
        0: [
          [390, 720],
          [700, 990],
        ],
      },
    ],
    [{ 0: [[390, 1500]] }],
    [{ 0: [[-10, 390]] }],
  ])('refuses the invalid week %j', (week) => {
    expect(() => new WorkCalendar({ week })).toThrow(/Horaires de travail/);
  });

  it('sorts periods, which may touch', () => {
    const calendar = new WorkCalendar({
      week: {
        0: [
          [720, 990],
          [390, 720],
        ],
      },
    });

    expect(calendar.periodsOfWeekday(0)).toEqual([
      [390, 720],
      [720, 990],
    ]);
    expect(calendar.addWork(at(MON, '11:30'), 60)).toBe(at(MON, '12:30'));
  });

  it('gives the day periods as instants of that day', () => {
    expect(GE.dayPeriods(MON)).toEqual([
      [at(MON, '06:30'), at(MON, '12:00')],
      [at(MON, '13:00'), at(MON, '16:30')],
    ]);
    expect(GE.dayPeriods(FRI)).toEqual([[at(FRI, '06:30'), at(FRI, '12:00')]]);
    expect(GE.dayPeriods(SAT)).toEqual([]);
    expect(GE.dayPeriods(GOOD_FRIDAY)).toEqual([]);
  });

  it('skips weekends and holidays for the next and previous workdays', () => {
    expect(GE.nextWorkday(FRI)).toBe(NEXT_MON);
    expect(GE.nextWorkday(SAT)).toBe(NEXT_MON);
    expect(GE.nextWorkday(MON)).toBe(TUE);
    expect(GE.previousWorkday(NEXT_MON)).toBe(FRI);
    expect(GE.previousWorkday(SUN)).toBe(FRI);
    expect(GE.nextWorkday(MAUNDY_THU)).toBe(EASTER_TUE);
    expect(GE.previousWorkday(EASTER_TUE)).toBe(MAUNDY_THU);
  });

  it('lists working days only', () => {
    expect(GE.daysBetween(MON, NEXT_MON)).toEqual([MON, TUE, WED, THU, FRI, NEXT_MON]);
    expect(GE.daysBetween(MAUNDY_THU, EASTER_TUE)).toEqual([MAUNDY_THU, EASTER_TUE]);
    expect(GE.daysBetween(TUE, MON)).toEqual([]);
  });

  it.each([
    [MON, '06:29', false],
    [MON, '06:30', true],
    [MON, '11:59', true],
    [MON, '12:00', false],
    [MON, '12:59', false],
    [MON, '13:00', true],
    [MON, '16:29', true],
    [MON, '16:30', false],
    [FRI, '11:59', true],
    [FRI, '12:00', false],
    [FRI, '14:00', false],
    [SAT, '09:00', false],
    [GOOD_FRIDAY, '09:00', false],
  ])('is worked on day %i at %s: %s', (day, text, worked) => {
    expect(GE.isWorked(at(day, text))).toBe(worked);
  });
});

// ------------------------------------------------------- working edges ----

describe('working edges', () => {
  it.each([
    [at(MON, '00:00'), at(MON, '06:30')],
    [at(MON, '05:00'), at(MON, '06:30')],
    [at(MON, '06:30'), at(MON, '06:30')],
    [at(MON, '09:15'), at(MON, '09:15')],
    [at(MON, '11:59'), at(MON, '11:59')],
    [at(MON, '12:00'), at(MON, '13:00')],
    [at(MON, '12:30'), at(MON, '13:00')],
    [at(MON, '13:00'), at(MON, '13:00')],
    [at(MON, '16:30'), at(TUE, '06:30')],
    [at(MON, '20:00'), at(TUE, '06:30')],
    [at(FRI, '11:30'), at(FRI, '11:30')],
    [at(FRI, '12:00'), at(NEXT_MON, '06:30')],
    [at(FRI, '14:00'), at(NEXT_MON, '06:30')],
    [at(SAT, '10:00'), at(NEXT_MON, '06:30')],
    [at(SUN, '23:59'), at(NEXT_MON, '06:30')],
    [at(MAUNDY_THU, '16:30'), at(EASTER_TUE, '06:30')],
    [at(GOOD_FRIDAY, '08:00'), at(EASTER_TUE, '06:30')],
  ])('working start of %i moves out of breaks, nights, weekends and holidays', (value, expected) => {
    expect(show(GE.workingStart(value))).toBe(show(expected));
  });

  it.each([
    [at(MON, '06:00'), at(PREVIOUS_FRI, '12:00')],
    [at(MON, '06:30'), at(PREVIOUS_FRI, '12:00')],
    [at(MON, '07:00'), at(MON, '07:00')],
    [at(MON, '12:00'), at(MON, '12:00')],
    [at(MON, '12:30'), at(MON, '12:00')],
    [at(MON, '13:00'), at(MON, '12:00')],
    [at(MON, '13:30'), at(MON, '13:30')],
    [at(MON, '16:30'), at(MON, '16:30')],
    [at(MON, '20:00'), at(MON, '16:30')],
    [dayStart(TUE), at(MON, '16:30')],
    [at(FRI, '12:00'), at(FRI, '12:00')],
    [at(FRI, '16:30'), at(FRI, '12:00')],
    [at(SAT, '10:00'), at(FRI, '12:00')],
    [at(NEXT_MON, '06:30'), at(FRI, '12:00')],
    [at(EASTER_TUE, '06:30'), at(MAUNDY_THU, '16:30')],
    [at(EASTER_MONDAY, '10:00'), at(MAUNDY_THU, '16:30')],
  ])('working end of %i moves back out of breaks, nights, weekends and holidays', (value, expected) => {
    expect(show(GE.workingEnd(value))).toBe(show(expected));
  });
});

// ---------------------------------------------------------- durations ----

describe('durations', () => {
  it.each([
    [at(MON, '06:30'), at(MON, '16:30'), 540],
    [at(MON, '00:00'), dayStart(TUE), 540],
    [at(MON, '11:00'), at(MON, '14:00'), 120],
    [at(MON, '12:00'), at(MON, '13:00'), 0],
    [at(MON, '12:15'), at(MON, '13:30'), 30],
    [at(FRI, '06:30'), at(NEXT_MON, '06:30'), 330],
    [at(FRI, '11:00'), at(NEXT_MON, '07:00'), 90],
    [at(SAT, '08:00'), at(SUN, '17:00'), 0],
    [at(MAUNDY_THU, '16:00'), at(EASTER_TUE, '07:00'), 60],
    [dayStart(dayNumber(2026, 3, 30)), dayStart(dayNumber(2026, 4, 6)), 2160],
    [at(MON, '14:00'), at(MON, '14:00'), 0],
    [at(MON, '14:00'), at(MON, '10:00'), 0],
  ])('work between %i and %i is %i minutes', (start, end, minutes) => {
    expect(GE.workBetween(start, end)).toBe(minutes);
  });

  it.each([
    // A morning ends at noon, not at the next period's start.
    [at(MON, '06:30'), 330, at(MON, '12:00')],
    [at(MON, '06:30'), 360, at(MON, '13:30')],
    [at(MON, '11:00'), 120, at(MON, '14:00')],
    [at(MON, '06:30'), 540, at(MON, '16:30')],
    [at(MON, '06:30'), 570, at(TUE, '07:00')],
    [at(MON, '12:15'), 30, at(MON, '13:30')],
    [at(MON, '05:00'), 30, at(MON, '07:00')],
    [at(MON, '12:00'), 0, at(MON, '13:00')],
    [at(FRI, '11:00'), 60, at(FRI, '12:00')],
    [at(FRI, '11:00'), 90, at(NEXT_MON, '07:00')],
    [at(FRI, '12:00'), 30, at(NEXT_MON, '07:00')],
    [at(SAT, '08:00'), 30, at(NEXT_MON, '07:00')],
    [at(MON, '06:30'), 2490, at(FRI, '12:00')],
    [at(MON, '06:30'), 2520, at(NEXT_MON, '07:00')],
    [at(MAUNDY_THU, '15:30'), 120, at(EASTER_TUE, '07:30')],
    [at(GOOD_FRIDAY, '08:00'), 60, at(EASTER_TUE, '07:30')],
  ])('add work from %i, %i minutes', (start, minutes, expected) => {
    expect(show(GE.addWork(start, minutes))).toBe(show(expected));
  });

  it.each([
    [at(MON, '12:00'), 330, at(MON, '06:30')],
    [at(MON, '16:30'), 210, at(MON, '13:00')],
    [at(MON, '14:00'), 120, at(MON, '11:00')],
    [at(MON, '13:00'), 30, at(MON, '11:30')],
    [at(MON, '07:00'), 60, at(PREVIOUS_FRI, '11:30')],
    [at(MON, '06:30'), 30, at(PREVIOUS_FRI, '11:30')],
    [at(TUE, '06:30'), 30, at(MON, '16:00')],
    [at(FRI, '16:30'), 2490, at(MON, '06:30')],
    [at(EASTER_TUE, '07:30'), 120, at(MAUNDY_THU, '15:30')],
  ])('subtract work up to %i, %i minutes', (end, minutes, expected) => {
    expect(show(GE.subtractWork(end, minutes))).toBe(show(expected));
  });

  it('goes the other way for negative durations', () => {
    expect(GE.addWork(at(MON, '14:00'), -120)).toBe(at(MON, '11:00'));
    expect(GE.subtractWork(at(MON, '11:00'), -120)).toBe(at(MON, '14:00'));
  });

  function instants(first: number, days: number, step: number): Instant[] {
    const begin = dayStart(first);
    const values: Instant[] = [];
    for (let value = begin; value < begin + days * MINUTES_PER_DAY; value += step) values.push(value);
    return values;
  }

  it.each([1, 30, 60, 210, 330, 540, 570, 1000, 2490, 5000])(
    'adds and subtracts %i minutes of work as inverses',
    (minutes) => {
      const problems: string[] = [];
      for (const start of instants(MAUNDY_THU - 4, 10, 30)) {
        const end = GE.addWork(start, minutes);
        const first = GE.workingStart(start);
        if (GE.workBetween(start, end) !== minutes) problems.push(`work from ${show(start)}`);
        if (GE.subtractWork(end, minutes) !== first) problems.push(`back from ${show(start)}`);
        if (!GE.isWorked(end - 1)) problems.push(`end from ${show(start)}`);
      }
      for (const end of instants(MAUNDY_THU - 4, 10, 30)) {
        const start = GE.subtractWork(end, minutes);
        if (GE.workBetween(start, end) !== minutes) problems.push(`work to ${show(end)}`);
        if (GE.addWork(start, minutes) !== GE.workingEnd(end)) problems.push(`forth to ${show(end)}`);
        if (!GE.isWorked(start)) problems.push(`start to ${show(end)}`);
      }
      expect(problems).toEqual([]);
    },
  );

  it('gives up searching without any workday', () => {
    const closures: number[] = [];
    for (let offset = -3700; offset < 3700; offset += 1) closures.push(MON + offset);
    const everyDayClosed = new WorkCalendar({ closures });

    expect(() => everyDayClosed.workingStart(at(MON, '08:00'))).toThrow('Aucun jour ouvrable');
    expect(() => everyDayClosed.nextWorkday(MON)).toThrow('Aucun jour ouvrable');
  });
});

// ------------------------------------------------------- working days ----

describe('working days', () => {
  it.each([
    [at(MON, '06:30'), 0.5, at(MON, '12:00')],
    [at(MON, '06:30'), 1, at(MON, '16:30')],
    [at(MON, '06:30'), 5, at(FRI, '12:00')],
    // An afternoon start is half of its day.
    [at(MON, '13:00'), 0.5, at(MON, '16:30')],
    [at(MON, '13:00'), 1, at(TUE, '12:00')],
    [at(THU, '13:00'), 2, at(NEXT_MON, '12:00')],
    [at(WED, '13:00'), 3, at(NEXT_MON, '12:00')],
    // Friday, worked until noon, is a whole day: the count jumps past it.
    [at(THU, '06:30'), 2, at(FRI, '12:00')],
    [at(THU, '13:00'), 1, at(FRI, '12:00')],
    [at(FRI, '06:30'), 0.5, at(FRI, '12:00')],
    // A start inside a period counts that period from its beginning.
    [at(MON, '09:00'), 0.5, at(MON, '12:00')],
    [at(MON, '12:00'), 0.5, at(MON, '16:30')],
    [at(MAUNDY_THU, '13:00'), 1, at(EASTER_TUE, '12:00')],
  ])('add days from %i counts halves from the period of the start (%d j)', (start, days, expected) => {
    expect(show(GE.addDays(start, days))).toBe(show(expected));
  });

  it.each([0.5, 1, 1.5, 2, 2.5, 3, 4.5, 5, 7.5])(
    'whole days reads back what add days placed for %d j',
    (days) => {
      // The bar (wholeDays) says what the card (addDays) gave, past a Friday by ½ j.
      const problems: string[] = [];
      for (const day of GE.daysBetween(MAUNDY_THU - 10, MAUNDY_THU + 10)) {
        for (const [low] of GE.dayPeriods(day)) {
          const end = GE.addDays(low, days);
          const read = GE.wholeDays(low, end);
          if (read === null) {
            problems.push(`unread from ${show(low)}`);
            continue;
          }
          if (!(days <= read && read <= days + 0.5)) problems.push(`${read} j from ${show(low)}`);
          // Reading it again places the same bar.
          if (GE.addDays(low, read) !== end) problems.push(`moved from ${show(low)}`);
        }
      }
      expect(problems).toEqual([]);
    },
  );
});

// ---------------------------------------------------------------- snap ----

describe('snap', () => {
  it.each([
    ['06:30', '06:30'],
    ['06:44', '06:30'],
    ['06:45', '07:00'],
    ['06:15', '06:30'],
    ['06:14', '06:00'],
    ['12:10', '12:00'],
    ['16:20', '16:30'],
    ['00:10', '00:00'],
  ])('rounds %s to the half hour, halves up: %s', (text, expected) => {
    expect(snap(at(MON, text))).toBe(at(MON, expected));
  });

  it('reaches next midnight late in the evening', () => {
    expect(snap(at(MON, '23:50'))).toBe(dayStart(TUE));
  });

  it('takes another step', () => {
    expect(SNAP_MINUTES).toBe(30);
    expect(snap(at(MON, '07:29'), 60)).toBe(at(MON, '07:00'));
    expect(snap(at(MON, '07:30'), 60)).toBe(at(MON, '08:00'));
    expect(snap(at(MON, '07:07'), 15)).toBe(at(MON, '07:00'));
  });
});

// ------------------------------------------------- calendar from meta ----

describe('calendar from the shared settings', () => {
  it('uses the default week and canton without settings', () => {
    const calendar = calendarFromMeta(undefined);

    expect(calendar.cantons).toEqual(['GE']);
    expect(calendar.week).toEqual(DEFAULT_WORK_WEEK);
  });

  it('reads JSON week keys, expands closures and takes the resource canton', () => {
    const meta = {
      work_week: { '0': [[420, 720]], '2': [[780, 960]] } as Record<string, [number, number][]>,
      default_canton: 'VD',
      closures: [{ first_day: '2026-10-26', last_day: '2026-10-28' }],
    };

    const vaud = calendarFromMeta(meta);
    const geneva = calendarFromMeta(meta, 'ge');

    expect(vaud.cantons).toEqual(['VD']);
    expect(geneva.cantons).toEqual(['GE']);
    expect(vaud.workdays).toEqual(new Set([0, 2]));
    expect([MON, TUE, WED].map((day) => vaud.isClosed(day))).toEqual([true, true, true]);
    expect(vaud.isClosed(THU)).toBe(false);
    expect(vaud.isWorkday(NEXT_MON)).toBe(true);
    expect(vaud.isWorkday(dayNumber(2026, 1, 2))).toBe(false);
    expect(geneva.isWorkday(dayNumber(2026, 12, 31))).toBe(false);
  });

  it('falls back to the default canton for an unknown one', () => {
    expect(calendarFromMeta({ default_canton: 'ZH' }, 'BE').cantons).toEqual(['GE']);
    expect(calendarFromMeta({ default_canton: 'vd' }, '').cantons).toEqual(['VD']);
  });
});
