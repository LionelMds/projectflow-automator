import { WorkCalendar } from '@/lib/planning/calendar';
import { describeInstant, wheelDayLabel } from '@/lib/planning/display';
import { dayStart, type Instant } from '@/lib/planning/instant';
import type { ParsedEntry } from '@/lib/planning/quickEntry';
import { dayNumber } from '@/lib/planning/walltime';
import { entryWithWhen, firstWhen, fromWheel, movedStart, wheelDays, wheelParts, whenNotes, whenRefused } from '@/lib/planning/when';

const calendar = new WorkCalendar();
const monday = dayNumber(2026, 11, 9);
const friday = dayNumber(2026, 11, 13);
const at = (day: number, hour: number, minute = 0): Instant => dayStart(day) + hour * 60 + minute;

const entry: ParsedEntry = {
  projectNumber: '2026-5000',
  project: null,
  title: 'Pose',
  taskType: 'montage',
  resourceIds: ['lbi'],
  start: null,
  end: null,
  estimateMinutes: 0,
  provisional: false,
  urgent: false,
  deadline: null,
  notes: '',
  absence: null,
  destination: 'backlog',
  tokens: [],
  warnings: [],
  interpretation: '',
  estimateDays: 2,
  ignoredDuration: false,
  offHours: false,
};

describe('début et fin sur les roues', () => {
  it('reads an instant as a day, an hour and a half hour, and back', () => {
    expect(wheelParts(at(monday, 7, 30))).toEqual({ day: monday, hour: 7, minute: 30 });
    expect(wheelParts(at(monday, 16, 45))).toEqual({ day: monday, hour: 16, minute: 30 });
    expect(fromWheel(monday, 6, 30)).toBe(at(monday, 6, 30));
  });

  it('names the days as the iPhone does', () => {
    expect(wheelDayLabel(monday, monday)).toBe('Aujourd’hui');
    expect(wheelDayLabel(monday + 1, monday)).toBe('Demain');
    expect(wheelDayLabel(friday, monday)).toBe('Ven 13 nov.');
  });

  it('turns the day wheel from a month back to a year ahead, the days shown included', () => {
    expect(wheelDays(monday)).toEqual({ first: monday - 31, count: 31 + 366 + 1 });
    const far = wheelDays(monday, monday + 500);
    expect(far.first).toBe(monday - 31);
    expect(far.first + far.count - 1).toBe(monday + 500 + 366);
  });

  it('keeps the worked time when the start moves, over the weekend', () => {
    // One day of work (9 h) moved to Friday, worked only in the morning: it ends on Monday.
    const moved = movedStart(calendar, { start: at(monday, 6, 30), end: at(monday, 16, 30) }, at(friday, 6, 30));
    expect(moved.start).toBe(at(friday, 6, 30));
    expect(describeInstant(moved.end)).toBe('lun 16.11 10:00');
  });

  it('offers the next working morning, as long as the estimate', () => {
    expect(firstWhen(calendar, friday)).toEqual({ start: at(friday + 3, 6, 30), end: at(friday + 3, 16, 30) });
    expect(firstWhen(calendar, monday, { minutes: 120 })).toEqual({ start: at(monday + 1, 6, 30), end: at(monday + 1, 8, 30) });
    expect(firstWhen(calendar, monday, { days: 2 }).end).toBe(at(monday + 2, 16, 30));
  });

  it('offers the planning cell touched: its half day, else a day from that minute', () => {
    const afternoon = { start: at(friday - 1, 13), end: at(friday - 1, 16, 30) };
    expect(firstWhen(calendar, monday, {}, afternoon)).toEqual(afternoon);
    expect(firstWhen(calendar, monday, { minutes: 90 }, { start: at(monday, 9), end: null })).toEqual({
      start: at(monday, 9),
      end: at(monday, 10, 30),
    });
  });

  it('says what the planning will change, and refuses an end before the start', () => {
    expect(whenNotes(calendar, { start: at(monday, 6, 30), end: at(monday, 16, 30) })).toEqual([]);
    expect(whenNotes(calendar, { start: at(monday, 18), end: at(monday + 1, 12) })).toEqual([
      'Hors des heures de travail : la tâche commencera mar 10.11 06:30.',
    ]);
    expect(whenNotes(calendar, { start: at(monday, 10), end: at(monday, 9) })).toEqual(['La fin doit suivre le début.']);
    expect(whenRefused(calendar, { start: at(monday, 10), end: at(monday, 9) })).toBe(true);
    expect(whenRefused(calendar, { start: at(monday, 12), end: at(monday, 12, 30) })).toBe(true);
    expect(whenRefused(calendar, { start: at(monday, 7), end: at(monday, 9) })).toBe(false);
  });

  it('dates the quick entry with the wheels, or sends it to « À planifier »', () => {
    const dated = entryWithWhen(entry, calendar, { start: at(monday, 6, 30), end: at(monday + 1, 16, 30) });
    expect(dated).toMatchObject({ start: at(monday, 6, 30), end: at(monday + 1, 16, 30), destination: 'grid', estimateDays: 2 });
    expect(dated.estimateMinutes).toBe(18 * 60);
    expect(entryWithWhen({ ...entry, resourceIds: [] }, calendar, { start: at(monday, 7), end: at(monday, 9) })).toMatchObject({
      destination: 'unassigned',
      estimateMinutes: 120,
      estimateDays: 0,
    });
    const later = entryWithWhen({ ...dated }, calendar, null);
    expect(later).toMatchObject({ start: null, end: null, destination: 'backlog' });
    // An absence keeps its days.
    expect(entryWithWhen({ ...entry, destination: 'absence' }, calendar, null).destination).toBe('absence');
  });
});
