/** The planning's search: words found anywhere in a task, narrowed by filters. */

import type { PlanningData, PlanningTask } from '@/lib/planning/models';
import { EMPTY_SEARCH, filterCount, folded, searchTasks, taskPeriod, type TaskSearch } from '@/lib/planning/search';
import { dayNumber } from '@/lib/planning/walltime';

const TODAY = dayNumber(2026, 11, 18); // a Wednesday

function task(id: string, fields: Partial<PlanningTask>): PlanningTask {
  return { id, ...fields };
}

const DATA: PlanningData = {
  meta: null,
  absences: [],
  resources: [
    { id: 'kdu', name: 'Kevin Dupuis', initials: 'KDU', team: 'atelier' },
    { id: 'mro', name: 'Marc Rochat', initials: 'MRO', team: 'montage' },
  ],
  tasks: [
    task('past', {
      project_number: '2026-4990',
      title: 'Garde-corps',
      task_type: 'montage',
      start: '2026-11-02T06:30',
      end: '2026-11-03T16:30',
      assignments: { mro: {} },
      status: 'done',
    }),
    task('running', {
      project_number: '2026-4996',
      title: 'Escalier',
      task_type: 'soudure',
      project_cache: { client: 'Régie Égalité SA', designation: 'Escalier hélicoïdal' },
      start: '2026-11-16T06:30',
      end: '2026-11-18T12:00',
      assignments: { kdu: {} },
    }),
    task('later', {
      project_number: '2026-5000',
      title: 'Pose portail',
      task_type: 'montage',
      start: '2026-11-25T21:00',
      end: '2026-11-26T05:00',
      assignments: { mro: {}, kdu: {} },
      off_hours: true,
      urgent: true,
      status: 'provisional',
    }),
    task('waiting', { title: 'Rendez-vous architecte', task_type: 'releve', notes: 'Apporter les échantillons' }),
    task('gone', { title: 'Escalier supprimé', task_type: 'soudure', deleted_at: '2026-11-01T08:00:00Z' }),
  ],
};

function ids(search: Partial<TaskSearch>): string[] {
  return searchTasks(DATA, { ...EMPTY_SEARCH, ...search }, TODAY).map((found) => found.id);
}

describe('searchTasks', () => {
  it('lists every live task, what is running or coming first, the past last', () => {
    expect(ids({})).toEqual(['running', 'later', 'waiting', 'past']);
  });

  it('finds words wherever they are, whatever their accents and case', () => {
    expect(ids({ text: 'egalite' })).toEqual(['running']);
    expect(ids({ text: 'HÉLICO' })).toEqual(['running']);
    expect(ids({ text: '5000' })).toEqual(['later']);
    expect(ids({ text: 'echantillons' })).toEqual(['waiting']);
  });

  it('asks for every word', () => {
    expect(ids({ text: 'montage mro' })).toEqual(['later', 'past']);
    expect(ids({ text: 'montage kdu' })).toEqual(['later']);
    expect(ids({ text: 'escalier mro' })).toEqual([]);
  });

  it('finds people, dates, weeks, nights and statuses by their words', () => {
    expect(ids({ text: 'kevin' })).toEqual(['running', 'later']);
    expect(ids({ text: '25.11' })).toEqual(['later']);
    expect(ids({ text: 's47' })).toEqual(['running']);
    expect(ids({ text: 'nuit' })).toEqual(['later']);
    expect(ids({ text: 'provisoire' })).toEqual(['later']);
  });

  it('narrows by period', () => {
    expect(ids({ period: 'upcoming' })).toEqual(['running', 'later']);
    expect(ids({ period: 'backlog' })).toEqual(['waiting']);
    expect(ids({ period: 'past' })).toEqual(['past']);
  });

  it('narrows by type, status, people, urgency and night, all at once', () => {
    expect(ids({ types: ['montage'] })).toEqual(['later', 'past']);
    expect(ids({ types: ['montage', 'releve'] })).toEqual(['later', 'waiting', 'past']);
    expect(ids({ statuses: ['done'] })).toEqual(['past']);
    expect(ids({ people: ['kdu'] })).toEqual(['running', 'later']);
    expect(ids({ people: ['kdu'], types: ['montage'] })).toEqual(['later']);
    expect(ids({ urgent: true })).toEqual(['later']);
    expect(ids({ night: true })).toEqual(['later']);
    expect(ids({ night: true, text: 'escalier' })).toEqual([]);
  });

  it('never returns a deleted task', () => {
    expect(ids({ text: 'supprime' })).toEqual([]);
  });
});

describe('helpers', () => {
  it('counts the filters in use, the words apart', () => {
    expect(filterCount(EMPTY_SEARCH)).toBe(0);
    expect(filterCount({ ...EMPTY_SEARCH, text: 'escalier' })).toBe(0);
    expect(filterCount({ ...EMPTY_SEARCH, period: 'past', types: ['a', 'b'], people: ['kdu'], urgent: true })).toBe(5);
  });

  it('folds accents and case', () => {
    expect(folded('Égalité À VENIR')).toBe('egalite a venir');
  });

  it('tells where a task stands today', () => {
    const byId = (id: string) => DATA.tasks.find((found) => found.id === id) as PlanningTask;
    expect(taskPeriod(byId('running'), TODAY)).toBe('upcoming');
    expect(taskPeriod(byId('past'), TODAY)).toBe('past');
    expect(taskPeriod(byId('waiting'), TODAY)).toBe('backlog');
  });
});
