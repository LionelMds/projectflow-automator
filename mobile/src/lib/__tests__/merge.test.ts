/**
 * Port of the merge tests of the desktop (`tests/unit/test_planning_service.py`:
 * `test_merge_*`) for `lib/planning/merge.ts`. Documents are the JSON the desktop writes
 * (`PlanningTask.to_json()`): every field present, wall times as text.
 */

import { changedFields, deletedMeanwhile, mergeDocuments, type Json } from '@/lib/planning/merge';
import type { Author } from '@/lib/planning/models';

const ME: Author = { initials: 'LM', name: 'Lionel Mottiez', poste: 'BUREAU-1', poste_id: 'poste-1' };
const OTHER: Author = { initials: 'PLA', name: 'Paul', poste: 'ATELIER-1', poste_id: 'poste-2' };
const NOBODY: Author = { initials: '', name: '', poste: '', poste_id: '' };
const MORNING = ['2026-10-26T06:30', '2026-10-26T12:00'];
const AFTERNOON = ['2026-10-26T13:00', '2026-10-26T16:30'];

/** `PlanningTask(title=..., project_number="2026-5245", **fields).to_json()`. */
function newTask(fields: Json = {}): Json {
  return {
    id: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
    schema_version: 1,
    created_at: '',
    created_by: NOBODY,
    updated_at: '',
    updated_by: NOBODY,
    deleted_at: null,
    deleted_by: null,
    status: 'confirmed',
    task_type: 'soudure',
    title: 'Garde-corps',
    project_number: '2026-5245',
    project_cache: { client: '', contact: '', designation: '', managed_by: '', location: '' },
    project_fingerprint: '',
    start: null,
    end: null,
    estimate_minutes: 0,
    estimate_days: 0,
    required_people: 1,
    assignments: {},
    deadline: null,
    urgent: false,
    notes: '',
    backlog_rank: '',
    origin: 'grid',
    planner_task_id: '',
    off_hours: false,
    ...fields,
  };
}

function names(lost: Set<string>): string[] {
  return [...lost].sort();
}

describe('mergeDocuments', () => {
  it('keeps both sides when the fields differ', () => {
    const base = newTask();
    const mine = { ...base, title: 'Garde-corps inox' };
    const theirs = { ...base, notes: 'Nacelle', updated_by: OTHER };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect([merged.title, merged.notes, names(lost)]).toEqual(['Garde-corps inox', 'Nacelle', []]);
  });

  it('keeps the server value of a field changed on both sides', () => {
    const base = newTask();
    const mine = { ...base, title: 'Acier', urgent: true };
    const theirs = { ...base, title: 'Inox' };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect([merged.title, merged.urgent, names(lost)]).toEqual(['Inox', true, ['title']]);
  });

  it('combines assignments per resource', () => {
    const base = newTask({ assignments: { r1: {}, r2: {} } });
    const mine = { ...base, assignments: { r2: {} } };
    const theirs = { ...base, assignments: { r1: {}, r2: {}, r3: {} } };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect([Object.keys(merged.assignments as Json), names(lost)]).toEqual([['r2', 'r3'], []]);
  });

  it('reports an assignment changed on both sides', () => {
    const base = newTask({ assignments: { r1: { hours: 4 } } });
    const mine = { ...base, assignments: { r1: { hours: 2 }, r4: {} } };
    const theirs = { ...base, assignments: { r1: { hours: 8 } } };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect(merged.assignments).toEqual({ r1: { hours: 8 }, r4: {} });
    expect(names(lost)).toEqual(['assignments']);
  });

  it('keeps start and end together', () => {
    const base = newTask({ start: MORNING[0], end: MORNING[1] });
    const mine = { ...base, start: AFTERNOON[0], end: AFTERNOON[1] };
    const theirs = { ...base, end: '2026-10-26T09:00' };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect([merged.start, merged.end]).toEqual([MORNING[0], '2026-10-26T09:00']);
    expect(names(lost)).toEqual(['end', 'start']);
  });

  it('does not report the same deletion twice', () => {
    const base = newTask();
    const mine = { ...base, deleted_at: '2026-10-22T08:00:00Z', deleted_by: ME };
    const theirs = { ...base, deleted_at: '2026-10-22T08:05:00Z', deleted_by: OTHER };

    const { merged, lost } = mergeDocuments(base, mine, theirs);

    expect([merged.deleted_at, names(lost)]).toEqual(['2026-10-22T08:05:00Z', []]);
  });
});

// Not in the desktop's merge tests: the two helpers the merge relies on, checked against
// `DocumentChange.changed_fields` and `_deleted_meanwhile` of `core/planning`.
describe('merge helpers', () => {
  it('changedFields ignores the fields every save rewrites', () => {
    const base = newTask();
    const after = { ...base, title: 'Inox', updated_at: '2026-10-22T08:00:00Z', updated_by: ME, schema_version: 2 };
    expect([...changedFields(base, after).entries()]).toEqual([['title', ['Garde-corps', 'Inox']]]);
    expect(changedFields(base, { ...base }).size).toBe(0);
    expect([...changedFields(null, { id: 'x' }).keys()]).toEqual(['id']);
  });

  it('deletedMeanwhile tells a deletion made by the other poste', () => {
    const base = newTask();
    const deleted = { ...base, deleted_at: '2026-10-22T08:05:00Z', deleted_by: OTHER };
    expect(deletedMeanwhile(base, { ...base, title: 'Inox' }, deleted)).toBe(true);
    expect(deletedMeanwhile(base, { ...base, title: 'Inox' }, base)).toBe(false);
    expect(deletedMeanwhile(base, { ...base, deleted_at: '2026-10-22T08:00:00Z' }, deleted)).toBe(false);
    expect(deletedMeanwhile(deleted, { ...deleted, title: 'Inox' }, deleted)).toBe(false);
    expect(deletedMeanwhile(null, newTask(), deleted)).toBe(true);
  });
});
