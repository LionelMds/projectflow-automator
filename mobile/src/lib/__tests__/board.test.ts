import { calendarFromMeta } from '@/lib/planning/calendar';
import { dayInfoFrom, periodText, shiftView, viewDays } from '@/lib/planning/display';
import type { PlanningData } from '@/lib/planning/models';
import { dayNumber } from '@/lib/planning/walltime';
import { buildBoard } from '@/lib/planning/week';

const people = [{ id: 'kdu', name: 'Kevin Dupont', initials: 'KDU', team: 'atelier' }];

function data(tasks: PlanningData['tasks'], absences: PlanningData['absences'] = []): PlanningData {
  return { tasks, resources: people, absences, meta: null };
}

describe('views of the board', () => {
  it('names the period as the desktop toolbar does', () => {
    expect(periodText('week', dayNumber(2026, 11, 12))).toBe('Semaine 46 · 9 – 13 nov.');
    expect(periodText('week', dayNumber(2026, 10, 28))).toBe('Semaine 44 · 26 – 30 oct.');
    expect(periodText('day', dayNumber(2026, 11, 12))).toBe('Jeu 12 nov. 2026');
    expect(periodText('month', dayNumber(2026, 11, 12))).toBe('Novembre 2026');
  });

  it('moves by shown days, weeks and months', () => {
    expect(shiftView('day', dayNumber(2026, 11, 13), 1)).toBe(dayNumber(2026, 11, 16));
    expect(shiftView('day', dayNumber(2026, 11, 16), -1)).toBe(dayNumber(2026, 11, 13));
    expect(shiftView('week', dayNumber(2026, 11, 12), 1)).toBe(dayNumber(2026, 11, 19));
    expect(shiftView('month', dayNumber(2026, 12, 15), 1)).toBe(dayNumber(2027, 1, 1));
    expect(shiftView('month', dayNumber(2026, 1, 15), -1)).toBe(dayNumber(2025, 12, 1));
  });

  it('shows the weekdays of the month only', () => {
    const days = viewDays('month', dayNumber(2026, 11, 12));
    expect(days).toHaveLength(21);
    expect(days[0]).toBe(dayNumber(2026, 11, 2));
    expect(days[days.length - 1]).toBe(dayNumber(2026, 11, 30));
  });

  it('lays a task over a weekend continuously in the month view', () => {
    const board = buildBoard(
      data([{ id: 'a', start: '2026-11-12T13:00', end: '2026-11-16T12:00', assignments: { kdu: {} } }]),
      { kind: 'slots', days: viewDays('month', dayNumber(2026, 11, 1)) },
      { compact: true },
    );
    // Thursday 12 is the 9th weekday (index 8): its afternoon is unit 17; Monday 16 morning is unit 20.
    expect(board.units).toBe(42);
    expect(board.groups[0].rows[0].segments[0]).toMatchObject({ startSlot: 17, span: 4 });
  });

  it('lays bars on the minutes of the day view', () => {
    const board = buildBoard(
      data(
        [
          { id: 'a', start: '2026-11-12T09:00', end: '2026-11-12T14:00', assignments: { kdu: {} } },
          { id: 'b', start: '2026-11-11T06:30', end: '2026-11-13T12:00', assignments: { kdu: {} } },
        ],
        [{ id: 'v', resource_id: 'kdu', reason: 'vacances', first_day: '2026-11-12', last_day: '2026-11-12', part: 'afternoon' }],
      ),
      { kind: 'day', day: dayNumber(2026, 11, 12) },
    );
    const segments = board.groups[0].rows[0].segments;
    expect(board.units).toBe(600);
    expect(segments.find((segment) => segment.id === 'task:a')).toMatchObject({ startSlot: 150, span: 300 });
    expect(segments.find((segment) => segment.id === 'task:b')).toMatchObject({ startSlot: 0, span: 600 });
    expect(segments.find((segment) => segment.kind === 'absence')).toMatchObject({ startSlot: 390, span: 210 });
  });

  it('flags holidays, closures and Friday afternoon on the columns', () => {
    const meta = { id: 'meta', closures: [{ first_day: '2026-12-28', last_day: '2026-12-30', label: 'Fermeture annuelle' }] };
    const calendar = calendarFromMeta(meta);
    const board = buildBoard(
      { tasks: [], resources: people, absences: [], meta },
      { kind: 'slots', days: viewDays('week', dayNumber(2026, 12, 21)) },
      { dayInfo: dayInfoFrom(calendar, meta) },
    );
    const christmas = board.columns.find((column) => column.day === dayNumber(2026, 12, 25));
    expect(christmas).toMatchObject({ label: 'Noël', closed: true, worked: [false, false] });
    const thursday = board.columns.find((column) => column.day === dayNumber(2026, 12, 24));
    expect(thursday).toMatchObject({ label: '', closed: false, worked: [true, true] });

    const next = buildBoard(
      { tasks: [], resources: people, absences: [], meta },
      { kind: 'slots', days: viewDays('week', dayNumber(2026, 12, 28)) },
      { dayInfo: dayInfoFrom(calendar, meta) },
    );
    expect(next.columns[0]).toMatchObject({ label: 'Fermeture annuelle', closed: true });
    // Restauration de la République (GE), then an ordinary Friday morning.
    expect(next.columns[3]).toMatchObject({ label: 'Restauration de la République', closed: true });
    const friday = buildBoard(data([]), { kind: 'slots', days: viewDays('week', dayNumber(2026, 11, 9)) }, { dayInfo: dayInfoFrom(calendar, meta) });
    expect(friday.columns[4].worked).toEqual([true, false]);
  });
});
