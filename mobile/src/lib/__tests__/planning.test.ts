import { sharingToken } from '@/lib/base64';
import type { PlanningData } from '@/lib/planning/models';
import { collect, parseFileName } from '@/lib/planning/store';
import { dayNumber, isoWeek, mondayOf, parseWallTime, weekday, weekRange } from '@/lib/planning/walltime';
import { barLabel, buildWeek } from '@/lib/planning/week';

const MONDAY = dayNumber(2026, 11, 9);

function data(partial: Partial<PlanningData>): PlanningData {
  return { tasks: [], resources: [], absences: [], meta: null, ...partial };
}

const people = [
  { id: 'kdu', name: 'Karim', initials: 'KDU', team: 'atelier', order: 1 },
  { id: 'lbi', name: 'Luca', initials: 'LBI', team: 'atelier', order: 2 },
  { id: 'mro', name: 'Marc', initials: 'MRO', team: 'montage', order: 1 },
  { id: 'old', name: 'Ancien', initials: 'ANC', team: 'atelier', active: false },
];

describe('wall times', () => {
  it('parses the desktop format without any time zone', () => {
    expect(parseWallTime('2026-11-09T06:30')).toEqual({ day: MONDAY, minutes: 390 });
    expect(parseWallTime('2026-11-09 16:30:00')).toEqual({ day: MONDAY, minutes: 990 });
    expect(parseWallTime('lundi')).toBeNull();
  });

  it('knows weekdays, ISO weeks and labels', () => {
    expect(weekday(MONDAY)).toBe(0);
    expect(mondayOf(MONDAY + 4)).toBe(MONDAY);
    expect(isoWeek(MONDAY)).toBe(46);
    expect(isoWeek(dayNumber(2027, 1, 1))).toBe(53);
    expect(weekRange(MONDAY)).toBe('9 – 13 nov.');
    expect(weekRange(dayNumber(2026, 11, 30))).toBe('30 nov. – 4 déc.');
  });
});

describe('OneDrive planning files', () => {
  it('reads only <kind>__<id>.json names', () => {
    expect(parseFileName('task__abc123.json')).toEqual({ kind: 'task', id: 'abc123' });
    expect(parseFileName('meta__meta.json')).toEqual({ kind: 'meta', id: 'meta' });
    expect(parseFileName('task__abc (1).json')).toBeNull();
    expect(parseFileName('notes.txt')).toBeNull();
  });

  it('drops a stray copy whose own id differs from its file name', () => {
    const result = collect([
      { kind: 'task', id: 'a', data: { id: 'a', title: 'Bon' } },
      { kind: 'task', id: 'b', data: { id: 'a', title: 'Copie' } },
      { kind: 'resource', id: 'r', data: { name: 'Karim' } },
      { kind: 'absence', id: 'x', data: 'pas du JSON objet' },
    ]);
    expect(result.tasks.map((task) => task.title)).toEqual(['Bon']);
    expect(result.resources[0].id).toBe('r');
    expect(result.absences).toHaveLength(0);
  });
});

describe('week board', () => {
  it('places tasks on half-days, per person, grouped by team', () => {
    const week = buildWeek(
      data({
        resources: people,
        tasks: [
          {
            id: 't1',
            project_number: '2026-5247',
            title: 'Soudure',
            task_type: 'soudure',
            start: '2026-11-09T06:30',
            end: '2026-11-10T16:30',
            assignments: { kdu: {}, lbi: {} },
          },
          {
            id: 't2',
            project_number: '2026-5249',
            start: '2026-11-12T13:00',
            end: '2026-11-13T12:00',
            status: 'provisional',
            required_people: 2,
            assignments: { mro: {} },
          },
        ],
      }),
      MONDAY,
    );
    expect(week.groups.map((group) => group.name)).toEqual(['Atelier', 'Montage']);
    const kdu = week.groups[0].rows[0];
    expect(kdu.label).toBe('KDU');
    expect(kdu.segments[0]).toMatchObject({ startSlot: 0, span: 4, badge: '×2', color: '#b7684c' });
    const mro = week.groups[1].rows[0];
    expect(mro.segments[0]).toMatchObject({ startSlot: 7, span: 2, badge: '1/2', provisional: true });
    expect(week.groups[0].rows.map((row) => row.label)).not.toContain('ANC');
  });

  it('puts unassigned tasks on « Non affecté » and undated ones in « À planifier »', () => {
    const week = buildWeek(
      data({
        resources: people,
        tasks: [
          { id: 'u', start: '2026-11-11T06:30', end: '2026-11-11T12:00', assignments: {} },
          { id: 'b', title: 'Fabrication – Main courante', backlog_rank: 'a' },
          { id: 'd', title: 'Terminée', status: 'done' },
          { id: 'z', title: 'Supprimée', deleted_at: '2026-11-01T10:00:00Z' },
        ],
      }),
      MONDAY,
    );
    const last = week.groups[week.groups.length - 1];
    expect(last.name).toBe('Non affecté');
    expect(last.rows[0].segments[0]).toMatchObject({ startSlot: 4, span: 1 });
    expect(week.backlog.map((task) => task.id)).toEqual(['b']);
  });

  it('shows absences and weekly days off, and clips tasks to the week', () => {
    const week = buildWeek(
      data({
        resources: [{ ...people[1], recurring_off: [{ weekday: 2, part: 'day', reason: 'ecole' }] }],
        absences: [{ id: 'v', resource_id: 'lbi', reason: 'vacances', first_day: '2026-11-12', last_day: '2026-11-20' }],
        tasks: [
          { id: 'long', start: '2026-11-02T06:30', end: '2026-11-10T12:00', assignments: { lbi: {} } },
        ],
      }),
      MONDAY,
    );
    const segments = week.groups[0].rows[0].segments;
    expect(segments.find((segment) => segment.label === 'Vacances')).toMatchObject({ startSlot: 6, span: 4 });
    expect(segments.find((segment) => segment.label === 'École professionnelle')).toMatchObject({ startSlot: 4, span: 2 });
    expect(segments.find((segment) => segment.kind === 'task')).toMatchObject({ startSlot: 0, span: 3 });
  });

  it('labels a bar with the number without its year, then the title', () => {
    expect(barLabel({ id: 'a', project_number: '2026-4998', title: 'Montage' })).toBe('4998 Montage');
    expect(barLabel({ id: 'b', project_number: '2026-4998-2', project_cache: { designation: 'Palier' } })).toBe(
      '4998-2 Palier',
    );
    expect(barLabel({ id: 'c', title: 'Rangement' })).toBe('Rangement');
  });

  it('stacks overlapping bars on separate lanes', () => {
    const week = buildWeek(
      data({
        resources: [people[0]],
        tasks: [
          { id: 'a', start: '2026-11-09T06:30', end: '2026-11-11T16:30', assignments: { kdu: {} } },
          { id: 'b', start: '2026-11-10T06:30', end: '2026-11-10T12:00', assignments: { kdu: {} } },
          { id: 'c', start: '2026-11-12T06:30', end: '2026-11-12T12:00', assignments: { kdu: {} } },
        ],
      }),
      MONDAY,
    );
    const row = week.groups[0].rows[0];
    expect(row.lanes).toBe(2);
    expect(row.segments.map((segment) => [segment.id, segment.lane])).toEqual([
      ['task:a', 0],
      ['task:b', 1],
      ['task:c', 0],
    ]);
  });
});

describe('sharing links', () => {
  it('encodes a link as the desktop does (u! + base64url without padding)', () => {
    expect(sharingToken('https://onedrive.live.com/redir?resid=1231244193912!12&authKey=1201919!12921!1')).toBe(
      'u!aHR0cHM6Ly9vbmVkcml2ZS5saXZlLmNvbS9yZWRpcj9yZXNpZD0xMjMxMjQ0MTkzOTEyITEyJmF1dGhLZXk9MTIwMTkxOSExMjkyMSEx',
    );
  });
});
