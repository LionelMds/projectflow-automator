/**
 * Port of `tests/unit/test_planning_night.py`: the night rules and the quick entry.
 *
 * The model, command and rest/load cases of the Python file exercise `models.py`,
 * `commands.py` and `conflicts.py`, which are not ported: they are listed as skipped below.
 */

import { WorkCalendar } from '@/lib/planning/calendar';
import { formatInstant, type Instant, instant } from '@/lib/planning/instant';
import {
  defaultNight,
  MIN_REST_MINUTES,
  NIGHT_HOURS_MESSAGE,
  NIGHT_WEEKEND_MESSAGE,
  nightDay,
  nightError,
  nightLabel,
  nightOf,
  restSpans,
  restText,
  shiftNights,
} from '@/lib/planning/night';
import {
  DEFAULT_TASK_TYPES,
  DEFAULT_TEAMS,
  type EntryContext,
  type EntryResource,
  type ParsedEntry,
  parseEntry,
  type ProjectInfo,
} from '@/lib/planning/quickEntry';
import { dayNumber } from '@/lib/planning/walltime';

const MON = dayNumber(2026, 10, 26);
const TUE = dayNumber(2026, 10, 27);
const WED = dayNumber(2026, 10, 28);
const THU = dayNumber(2026, 10, 29);
const FRI = dayNumber(2026, 10, 30);
const SAT = dayNumber(2026, 10, 31);
const SUN = dayNumber(2026, 11, 1);

function at(day: number, hour: number, minute = 0): Instant {
  return instant(day, hour, minute);
}

// The quick entry context of `test_planning_quick_entry.py`.
const RESOURCES: EntryResource[] = [
  { id: 'lbi', name: 'Luca Bianchi', initials: 'LBI', team: 'montage' },
  { id: 'ych', name: 'Yann Chappuis', initials: 'YCH', team: 'montage' },
  { id: 'jmf', name: 'Jean-Marc Fasel', initials: 'JMF', team: 'montage' },
  { id: 'mdu2', name: 'Michel Duc', initials: 'MDU', team: 'montage' },
  { id: 'oti', name: 'Olivier Tissot', initials: 'OTI', team: 'montage', active: false },
  { id: 'kdu', name: 'Kevin Dupuis', initials: 'KDU', team: 'atelier' },
  { id: 'tfa', name: 'Thomas Favre', initials: 'TFA', team: 'atelier' },
  { id: 'jro', name: 'Jean Rochat', initials: 'JRO', team: 'atelier' },
  { id: 'mdu1', name: 'Marc Dupraz', initials: 'MDU', team: 'atelier' },
  { id: 'de', name: 'David Emery', initials: 'DE', team: 'atelier' },
  { id: 'le', name: 'Laurent Egger', initials: 'LE', team: 'atelier' },
  { id: 'nre', name: 'Noah Rey', initials: 'NRE', team: 'atelier', apprentice: true },
  { id: 'sbe', name: 'Sébastien Berset', initials: 'SBE', team: 'bureau' },
  { id: 'pm', name: 'Pierre Monnet', initials: 'PM', team: 'bureau' },
  { id: 'thl', name: 'Thermo-Lac SA', initials: 'THL', team: 'soustraitants', kind_of_resource: 'subcontractor' },
];
const PROJECTS = new Map<string, ProjectInfo>([
  ['2026-121', { number: '2026-121', client: 'Commune de Marsens', designation: 'Marquise entrée école' }],
]);
const CONTEXT: EntryContext = {
  today: dayNumber(2026, 10, 22),
  weekStart: dayNumber(2026, 10, 19),
  calendar: new WorkCalendar({ cantons: ['GE'] }),
  resources: RESOURCES,
  teams: DEFAULT_TEAMS,
  taskTypes: DEFAULT_TASK_TYPES,
  findProject: (number) => PROJECTS.get(number) ?? null,
  defaultYear: 2026,
};

function parse(text: string): ParsedEntry {
  return parseEntry(text, CONTEXT);
}

// ----------------------------------------------------------------- rules ----

describe('night rules', () => {
  it('gives a night to its evening', () => {
    expect(nightOf(at(MON, 21))).toBe(MON);
    expect(nightOf(at(TUE, 1))).toBe(MON);
    expect(nightOf(at(TUE, 10))).toBeNull();
    expect(nightDay(at(TUE, 10))).toBe(TUE);
  });

  it.each([
    [at(MON, 21), at(TUE, 5), null],
    [at(THU, 22), at(FRI, 6, 30), null],
    [at(TUE, 18), at(TUE, 23), null],
    [at(TUE, 1), at(TUE, 5), null],
    [at(FRI, 21), at(SAT, 5), NIGHT_WEEKEND_MESSAGE],
    [at(SAT, 21), at(SUN, 5), NIGHT_WEEKEND_MESSAGE],
    [at(SUN, 22), at(MON, 4), NIGHT_WEEKEND_MESSAGE],
    [at(SAT, 2), at(SAT, 5), NIGHT_WEEKEND_MESSAGE],
    [at(MON, 21), at(TUE, 7), NIGHT_HOURS_MESSAGE],
    [at(MON, 17), at(MON, 23), NIGHT_HOURS_MESSAGE],
    [at(MON, 6), at(MON, 9), NIGHT_HOURS_MESSAGE],
  ])('checks the night window %i to %i', (start, end, error) => {
    expect(nightError(start, end)).toBe(error);
  });

  it('skips Friday to Sunday', () => {
    expect(shiftNights(THU, 1)).toBe(dayNumber(2026, 11, 2));
    expect(shiftNights(dayNumber(2026, 11, 2), -1)).toBe(THU);
    expect(shiftNights(MON, 2)).toBe(WED);
  });

  it('keeps rest spans and writes them', () => {
    const [start, end] = defaultNight(MON);
    expect(restSpans(start, end)).toEqual([
      [at(MON, 13), start],
      [end, at(TUE, 13)],
    ]);
    expect(MIN_REST_MINUTES).toBe(480);
    expect(restText(start, end)).toBe("Repos : dès lun 26.10 13:00 avant · jusqu'à mar 27.10 13:00 après");
    expect(nightLabel(at(THU, 22), at(FRI, 5))).toBe('\u{1F319} nuit jeu 29.10 22:00 → ven 30.10 05:00 · 7 h');
  });
});

// ----------------------------------------------------------- quick entry ----

describe('night quick entry', () => {
  it('reads a range crossing midnight', () => {
    const entry = parse('2026-121 jeu 22h-5h LBI');
    expect(entry.offHours).toBe(true);
    expect(entry.interpretation.endsWith('\u{1F319} nuit jeu 22.10 22:00 → ven 23.10 05:00 · 7 h')).toBe(true);
    expect(entry.estimateMinutes).toBe(420);
    expect(entry.warnings).toEqual([]);
  });

  it('reads a clock range, and a start with a duration', () => {
    const entry = parse('mer 21:00-05:00 LBI');
    expect(entry.offHours).toBe(true);
    expect([formatInstant(entry.start ?? 0), formatInstant(entry.end ?? 0)]).toEqual([
      '2026-10-28T21:00',
      '2026-10-29T05:00',
    ]);
    const short = parse('jeu à 20h 3h LBI');
    expect(short.offHours).toBe(true);
    expect((short.end ?? 0) - (short.start ?? 0)).toBe(180);
  });

  it('defaults the word nuit to nine pm to five am', () => {
    const entry = parse('pose nuit mar LBI');
    expect(entry.offHours).toBe(true);
    expect([entry.start, entry.end]).toEqual([instant(dayNumber(2026, 10, 27), 21), instant(dayNumber(2026, 10, 28), 5)]);
  });

  it('ends a night by half past six', () => {
    const entry = parse('lun nuit 23h-8h LBI');
    expect(entry.end).toBe(instant(dayNumber(2026, 10, 27), 6, 30));
    expect(entry.warnings).toContain('Une nuit finit à 06:30 au plus tard : fin à 06:30');
  });

  it.each(['ven nuit LBI', 'sam 22h-4h LBI', 'dim nuit LBI'])('refuses the weekend night %s', (text) => {
    const entry = parse(text);
    expect(entry.offHours).toBe(false);
    expect(entry.warnings).toContain(NIGHT_WEEKEND_MESSAGE);
  });

  it('keeps daytime hours as worked time', () => {
    expect(parse('lun 14h-16h LBI').offHours).toBe(false);
  });
});

// ------------------------------------------------- not ported (other modules) ----

// These cases need `models.py` (document round trip), `commands.py` (create, move,
// resize, shift, duplicate, unschedule/schedule, set_night) or `conflicts.py`
// (findings_by_bar, resource_load), none of which is part of this port.
describe.skip('night tasks in the model, commands, rest and load (not ported)', () => {
  it.each([
    'test_off_hours_round_trips_and_defaults_to_false',
    'test_create_keeps_exact_times_and_real_length',
    'test_create_without_end_lasts_eight_hours_from_nine_pm',
    'test_create_on_a_friday_evening_is_refused',
    'test_move_keeps_the_clock_times_and_the_duration',
    'test_move_onto_a_friday_evening_is_refused',
    'test_move_past_half_past_six_is_refused',
    'test_resize_snaps_to_the_half_hour_only',
    'test_shift_by_days_jumps_from_thursday_to_monday',
    'test_duplicate_goes_to_the_next_night',
    'test_unschedule_and_schedule_keep_the_real_length',
    'test_set_night_on_and_off',
    'test_set_night_on_a_friday_is_refused',
    'test_a_morning_task_after_a_night_breaks_the_rest',
    'test_exactly_eight_hours_of_rest_is_enough',
    'test_rest_is_per_person',
    'test_a_night_raises_no_non_worked_finding',
    'test_night_minutes_count_on_top_of_the_day',
  ])('%s', () => undefined);
});
