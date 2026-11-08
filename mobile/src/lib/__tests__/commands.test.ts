/**
 * Port of the desktop's command tests (`tests/unit/test_planning_commands.py`) for
 * `lib/planning/commands.ts`, limited to the commands the phone has. Documents are the
 * JSON the desktop writes (every field present, wall times as text); commands take
 * instants and return `Change[]`. The desktop's messages have no accents, the phone's do:
 * refusals are matched on the meaningful words of the phone's message.
 */

import { WorkCalendar } from '@/lib/planning/calendar';
import {
  assign,
  backlog,
  calendarResolver,
  createAbsence,
  createTask,
  DEFAULT_TASK_MINUTES,
  deleteAbsence,
  deleteTask,
  moveTask,
  PlanningCommandError,
  resizeTask,
  restoreTask,
  scheduleTask,
  setStatus,
  setTaskDays,
  taskDays,
  unassign,
  UNASSIGNED,
  unscheduleTask,
  updateTask,
  type CommandContext,
  type NewTask,
  type TaskFields,
} from '@/lib/planning/commands';
import type { Change } from '@/lib/planning/documents';
import { formatInstant, instant, parseInstant, type Instant } from '@/lib/planning/instant';
import {
  assigneeIds,
  isScheduled,
  type Absence,
  type Author,
  type PlanningData,
  type PlanningMeta,
  type PlanningTask,
  type Resource,
  type TaskStatus,
} from '@/lib/planning/models';
import type { Json } from '@/lib/planning/store';
import { dayNumber } from '@/lib/planning/walltime';

const AUTHOR: Author = { initials: 'LM', name: 'Lionel Mottiez', poste: 'Bureau 1', poste_id: 'poste-1' };
const EARLIER = '2026-10-01T08:00:00Z';
const NOW = '2026-10-22T10:00:00Z';
const OLD_AUTHOR: Author = { initials: 'RB', name: '', poste: 'Atelier', poste_id: '' };

const MON = dayNumber(2026, 10, 26);
const TUE = dayNumber(2026, 10, 27);
const WED = dayNumber(2026, 10, 28);
const THU = dayNumber(2026, 10, 29);
const FRI = dayNumber(2026, 10, 30);
const SAT = dayNumber(2026, 10, 31);
const NEXT_MON = dayNumber(2026, 11, 2);

function at(day: number, hour: number, minute = 0): Instant {
  return instant(day, hour, minute);
}

/** A span as tasks store it: `[start, end]` wall-clock texts. */
function span(start: Instant, end: Instant): [string, string] {
  return [formatInstant(start), formatInstant(end)];
}

function bar(task: PlanningTask): [string | null, string | null] {
  return [task.start ?? null, task.end ?? null];
}

function stored(value: Instant): string {
  return formatInstant(value);
}

// ----------------------------------------------------------------- documents ----

function makeResource(id: string, name: string, fields: Partial<Resource> = {}): Resource {
  return {
    id,
    schema_version: 1,
    created_at: EARLIER,
    created_by: OLD_AUTHOR,
    updated_at: EARLIER,
    updated_by: OLD_AUTHOR,
    deleted_at: null,
    deleted_by: null,
    kind_of_resource: 'person',
    name,
    initials: '',
    team: 'atelier',
    order: 0,
    active: true,
    apprentice: false,
    canton: '',
    role: '',
    recurring_off: [],
    ...fields,
  };
}

type TaskSpec = Omit<Partial<PlanningTask>, 'start' | 'end'> & {
  start?: Instant | null;
  end?: Instant | null;
  people?: readonly string[];
};

/** `make_task`: a task as the desktop stores it, with `people` as assignees. */
function makeTask(id: string, spec: TaskSpec = {}): PlanningTask {
  const { people: assignees = [], start = null, end = null, ...fields } = spec;
  return {
    id,
    schema_version: 1,
    created_at: EARLIER,
    created_by: OLD_AUTHOR,
    updated_at: EARLIER,
    updated_by: OLD_AUTHOR,
    deleted_at: null,
    deleted_by: null,
    status: 'confirmed',
    task_type: 'soudure',
    title: `Tache ${id}`,
    project_number: '',
    project_cache: { client: '', contact: '', designation: '', managed_by: '', location: '' },
    project_fingerprint: '',
    start: start === null ? null : formatInstant(start),
    end: end === null ? null : formatInstant(end),
    estimate_minutes: 0,
    estimate_days: 0,
    required_people: 1,
    assignments: Object.fromEntries(assignees.map((person) => [person, {}])),
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

function people(): Resource[] {
  return [
    makeResource('anna', 'Anna Favre', { order: 1000 }),
    makeResource('bruno', 'Bruno Rossier', { order: 2000 }),
    makeResource('chloe', 'Chloé Vaudoise', { canton: 'VD', order: 3000 }),
    makeResource('dylan', 'Dylan Archive', { active: false, order: 4000 }),
    makeResource('emma', 'Emma Bureau', { team: 'bureau', order: 1000 }),
  ];
}

function makeData(tasks: PlanningTask[], meta: PlanningMeta | null = null): PlanningData {
  return { tasks, resources: people(), absences: [], meta };
}

function makeCtx(data: PlanningData): CommandContext {
  return { data, author: AUTHOR, now: NOW, calendarFor: calendarResolver(data) };
}

function ctxWith(...tasks: PlanningTask[]): CommandContext {
  return makeCtx(makeData(tasks));
}

// ------------------------------------------------------------------- changes ----

type AnyChange = { kind: Change['kind']; id: string; before: Json | null; after: Json | null };

/** `PlanningSnapshot.apply`: a new state with the changes applied (`after` null removes). */
function apply(data: PlanningData, changes: readonly AnyChange[]): PlanningData {
  const next: PlanningData = {
    tasks: [...data.tasks],
    resources: [...data.resources],
    absences: [...data.absences],
    meta: data.meta,
  };
  for (const change of changes) {
    if (change.kind === 'meta') {
      next.meta = change.after as PlanningMeta | null;
      continue;
    }
    const list = (change.kind === 'task' ? next.tasks : change.kind === 'resource' ? next.resources : next.absences) as unknown as Json[];
    const index = list.findIndex((document) => document.id === change.id);
    if (change.after === null) {
      if (index >= 0) list.splice(index, 1);
    } else if (index >= 0) {
      list[index] = change.after;
    } else {
      list.push(change.after);
    }
  }
  return next;
}

function inverse(change: AnyChange): AnyChange {
  return { ...change, before: change.after, after: change.before };
}

function applied(ctx: CommandContext, changes: readonly AnyChange[]): CommandContext {
  return makeCtx(apply(ctx.data, changes));
}

function only(changes: readonly Change[], id: string): Json {
  const found = changes.filter((change) => change.id === id);
  expect(found).toHaveLength(1);
  return found[0].after;
}

function taskAfter(changes: readonly Change[], id: string): PlanningTask {
  return only(changes, id) as PlanningTask;
}

function firstTask(changes: readonly Change[]): PlanningTask {
  return changes[0].after as PlanningTask;
}

function taskIn(ctx: CommandContext, id: string): PlanningTask {
  const task = ctx.data.tasks.find((candidate) => candidate.id === id);
  if (!task) throw new Error(`no task ${id}`);
  return task;
}

function backlogIds(data: PlanningData): string[] {
  return backlog(data).map((card) => card.id);
}

/** The command refuses with a PlanningCommandError whose message matches. */
function refuses(action: () => unknown, message: RegExp): void {
  let error: unknown = null;
  try {
    action();
  } catch (caught) {
    error = caught;
  }
  expect(error).toBeInstanceOf(PlanningCommandError);
  expect((error as Error).message).toMatch(message);
}

/** Every nested object frozen: a command writing into its input throws. */
function deepFrozen<T>(value: T): T {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value);
    for (const child of Object.values(value)) deepFrozen(child);
  }
  return value;
}

// --------------------------------------------------------------- create task ----

describe('createTask', () => {
  it('joins the end of the backlog without start', () => {
    const ctx = ctxWith(
      makeTask('old', { backlog_rank: 'i' }),
      makeTask('planned', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna'] }),
    );
    const changes = createTask(ctx, {
      title: '  Garde-corps  ',
      taskType: 'soudure',
      projectNumber: '2026-5245',
      resourceIds: ['anna', 'bruno', 'anna'],
      estimateMinutes: 600,
      origin: 'quick_entry',
    });
    expect(changes).toHaveLength(1);
    const created = firstTask(changes);
    expect(changes[0].before).toBeNull();
    expect(created.title).toBe('Garde-corps');
    expect(isScheduled(created)).toBe(false);
    expect(assigneeIds(created)).toEqual(['anna', 'bruno']);
    expect((created.backlog_rank ?? '') > 'i').toBe(true);
    expect(created.estimate_minutes).toBe(600);
    expect(created.origin).toBe('quick_entry');
    expect(created.created_by).toEqual(AUTHOR);
    expect(created.updated_at).toBe(NOW);
    expect(backlogIds(apply(ctx.data, changes))).toEqual(['old', created.id]);
  });

  it('lasts its estimate on worked time with a start', () => {
    const ctx = ctxWith();
    const fullDay = firstTask(createTask(ctx, { title: 'A', taskType: 'debit', start: at(MON, 6, 30) }));
    expect(bar(fullDay)).toEqual(span(at(MON, 6, 30), at(MON, 16, 30)));
    const longer = firstTask(createTask(ctx, { title: 'B', taskType: 'debit', start: at(THU, 6, 30), estimateMinutes: 870 }));
    expect(longer.end).toBe(stored(at(FRI, 12)));
    const rounded = firstTask(createTask(ctx, { title: 'C', taskType: 'debit', start: at(MON, 11), estimateMinutes: 45 }));
    expect(rounded.end).toBe(stored(at(MON, 12)));
  });

  it('snaps and moves starts onto worked time', () => {
    const ctx = ctxWith();
    const snapped = firstTask(createTask(ctx, { title: 'A', taskType: 'debit', start: at(MON, 6, 40), end: at(MON, 9, 50) }));
    expect(bar(snapped)).toEqual(span(at(MON, 6, 30), at(MON, 10)));
    const lunch = firstTask(createTask(ctx, { title: 'B', taskType: 'debit', start: at(MON, 12, 10), end: at(MON, 15) }));
    expect(lunch.start).toBe(stored(at(MON, 13)));
    const weekend = firstTask(createTask(ctx, { title: 'C', taskType: 'debit', start: at(SAT, 9) }));
    expect(bar(weekend)).toEqual(span(at(NEXT_MON, 6, 30), at(NEXT_MON, 16, 30)));
  });

  it('uses the canton of the first person', () => {
    const ctx = ctxWith();
    const day = dayNumber(2026, 9, 10);
    const geneva = firstTask(createTask(ctx, { title: 'A', taskType: 'montage', start: at(day, 6, 30), resourceIds: ['anna'] }));
    const vaud = firstTask(createTask(ctx, { title: 'B', taskType: 'montage', start: at(day, 6, 30), resourceIds: ['chloe'] }));
    expect(geneva.start).toBe(stored(at(dayNumber(2026, 9, 11), 6, 30)));
    expect(vaud.start).toBe(stored(at(day, 6, 30)));
  });

  it.each<[string, Partial<NewTask>, RegExp]>([
    ['an end without start', { end: at(MON, 9) }, /besoin d'un début/],
    ['an end before the start', { start: at(MON, 10), end: at(MON, 9) }, /fin doit suivre/],
    ['an end at the start', { start: at(MON, 10), end: at(MON, 10) }, /fin doit suivre/],
    ['an unknown type', { taskType: 'inconnu' }, /Type de tâche inconnu/],
    ['an unknown person', { resourceIds: ['nobody'] }, /introuvable/],
    ['an archived person', { resourceIds: ['dylan'] }, /archivé/],
  ])('refuses %s', (_, fields, message) => {
    refuses(() => createTask(ctxWith(), { title: 'A', taskType: 'debit', ...fields }), message);
  });

  it('keeps one person required at least (the desktop refuses 0)', () => {
    // Desktop: required_people=0 raises « required_people »; the phone clamps to 1.
    const created = firstTask(createTask(ctxWith(), { title: 'A', taskType: 'debit', requiredPeople: 0 }));
    expect(created.required_people).toBe(1);
  });
});

// ------------------------------------------------------------------ schedule ----

describe('scheduleTask', () => {
  it('lasts one worked day without estimate', () => {
    const ctx = ctxWith(makeTask('card', { backlog_rank: 'i', people: ['anna'] }));
    const task = taskAfter(scheduleTask(ctx, 'card', { start: at(FRI, 6, 30), resourceIds: ['bruno'] }), 'card');
    expect(task.start).toBe(stored(at(FRI, 6, 30)));
    expect(task.end).toBe(stored(at(NEXT_MON, 10)));
    expect(assigneeIds(task)).toEqual(['bruno']);
    expect(assigneeIds(taskIn(ctx, 'card'))).toEqual(['anna']);
  });

  it('keeps the people foreseen without resource ids', () => {
    const ctx = ctxWith(makeTask('card', { estimate_minutes: 870, people: ['chloe', 'anna'] }));
    const task = taskAfter(scheduleTask(ctx, 'card', { start: at(THU, 6, 30), resourceIds: [] }), 'card');
    expect(assigneeIds(task)).toEqual(['chloe', 'anna']);
    expect(task.end).toBe(stored(at(FRI, 12)));
    expect(task.estimate_minutes).toBe(870);
  });

  it('goes on the unassigned row and leaves archived people out', () => {
    // Regression: [UNASSIGNED] kept the people foreseen, and an archived person foreseen
    // put the task on a hidden row.
    const ctx = ctxWith(
      makeTask('card', { backlog_rank: 'i', people: ['anna'] }),
      makeTask('mixed', { backlog_rank: 'j', people: ['dylan', 'bruno'] }),
      makeTask('gone', { backlog_rank: 'k', people: ['dylan'] }),
    );
    const free = taskAfter(scheduleTask(ctx, 'card', { start: at(MON, 6, 30), resourceIds: [UNASSIGNED] }), 'card');
    expect(isScheduled(free)).toBe(true);
    expect(free.assignments).toEqual({});
    const mixed = taskAfter(scheduleTask(ctx, 'mixed', { start: at(MON, 6, 30), resourceIds: [] }), 'mixed');
    expect(assigneeIds(mixed)).toEqual(['bruno']);
    const gone = taskAfter(scheduleTask(ctx, 'gone', { start: at(MON, 6, 30), resourceIds: [] }), 'gone');
    expect(isScheduled(gone)).toBe(true);
    expect(gone.assignments).toEqual({});
    expect(bar(gone)).toEqual(span(at(MON, 6, 30), at(MON, 16, 30)));
  });

  it('takes an explicit end', () => {
    const ctx = ctxWith(makeTask('card'));
    const task = taskAfter(
      scheduleTask(ctx, 'card', { start: at(MON, 6, 30), end: at(TUE, 12), resourceIds: ['anna'] }),
      'card',
    );
    expect(bar(task)).toEqual(span(at(MON, 6, 30), at(TUE, 12)));
  });

  it('refuses a deleted card', () => {
    const ctx = ctxWith(makeTask('card', { deleted_at: EARLIER }));
    refuses(() => scheduleTask(ctx, 'card', { start: at(MON, 6, 30), resourceIds: [] }), /supprimée/);
    refuses(() => scheduleTask(ctx, 'missing', { start: at(MON, 6, 30), resourceIds: [] }), /introuvable/);
  });

  it('keeps its worked length when planned again', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(TUE, 12), people: ['anna'] }));
    const task = taskAfter(scheduleTask(ctx, 't', { start: at(THU, 6, 30), resourceIds: [] }), 't');
    expect(bar(task)).toEqual(span(at(THU, 6, 30), at(FRI, 12)));
  });
});

// ---------------------------------------------------------------------- move ----

describe('moveTask', () => {
  it('keeps the worked minutes over the weekend from a Friday', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna'] }));
    const task = taskAfter(moveTask(ctx, 't', { newStart: at(FRI, 6, 30) }), 't');
    expect(bar(task)).toEqual(span(at(FRI, 6, 30), at(NEXT_MON, 10)));
  });

  it('moves from a Thursday afternoon to a Friday morning', () => {
    const ctx = ctxWith(makeTask('t', { start: at(THU, 13), end: at(FRI, 12), people: ['anna'] }));
    const task = taskAfter(moveTask(ctx, 't', { newStart: at(FRI, 6, 30) }), 't');
    expect(bar(task)).toEqual(span(at(FRI, 6, 30), at(NEXT_MON, 10)));
    const back = taskAfter(moveTask(ctx, 't', { newStart: at(WED, 6, 30) }), 't');
    expect(bar(back)).toEqual(span(at(WED, 6, 30), at(WED, 16, 30)));
  });

  it('skips Christmas', () => {
    const christmasEve = dayNumber(2026, 12, 24);
    const ctx = ctxWith(makeTask('t', { start: at(christmasEve, 6, 30), end: at(christmasEve, 16, 30), people: ['anna'] }));
    const task = taskAfter(moveTask(ctx, 't', { newStart: at(christmasEve, 13) }), 't');
    expect(bar(task)).toEqual(span(at(christmasEve, 13), at(dayNumber(2026, 12, 28), 12)));
  });

  it('uses the calendar of the target person when moved vertically', () => {
    const wednesday = dayNumber(2026, 9, 9);
    const ctx = ctxWith(makeTask('t', { start: at(wednesday, 6, 30), end: at(dayNumber(2026, 9, 11), 12), people: ['anna'] }));
    const task = taskAfter(
      moveTask(ctx, 't', { newStart: at(wednesday, 6, 30), fromResource: 'anna', toResource: 'chloe' }),
      't',
    );
    // 870 worked minutes in Geneva (Jeûne genevois on Thursday); Vaud works that Thursday.
    expect(task.end).toBe(stored(at(dayNumber(2026, 9, 10), 12)));
    expect(assigneeIds(task)).toEqual(['chloe']);
  });

  it('measures with the bar calendar whatever row is grabbed', () => {
    // Regression: the grabbed row's calendar measured the bar, so a shared GE/VD task
    // grew by a day when its VD person was replaced, and drags depended on the row.
    const tuesday = dayNumber(2026, 9, 8);
    const wednesday = dayNumber(2026, 9, 9);
    const friday = dayNumber(2026, 9, 11);
    const ctx = ctxWith(makeTask('t', { start: at(wednesday, 6, 30), end: at(friday, 12), people: ['anna', 'chloe'] }));
    const replaced = taskAfter(
      moveTask(ctx, 't', { newStart: at(wednesday, 6, 30), fromResource: 'chloe', toResource: 'bruno' }),
      't',
    );
    expect(assigneeIds(replaced)).toEqual(['anna', 'bruno']);
    expect(bar(replaced)).toEqual(span(at(wednesday, 6, 30), at(friday, 12)));
    for (const row of ['anna', 'chloe']) {
      const dragged = taskAfter(moveTask(ctx, 't', { newStart: at(tuesday, 6, 30), fromResource: row }), 't');
      expect([row, ...bar(dragged)]).toEqual([row, ...span(at(tuesday, 6, 30), at(wednesday, 12))]);
    }
    const firstReplaced = taskAfter(
      moveTask(ctx, 't', { newStart: at(wednesday, 6, 30), fromResource: 'anna', toResource: 'emma' }),
      't',
    );
    expect(assigneeIds(firstReplaced)).toEqual(['emma', 'chloe']);
    expect(firstReplaced.end).toBe(stored(at(friday, 12)));
  });

  it('replaces only the dragged person when moved vertically', () => {
    const ctx = ctxWith(
      makeTask('t', {
        start: at(MON, 6, 30),
        end: at(MON, 16, 30),
        assignments: { anna: { note: 'chef' }, bruno: {} },
      }),
    );
    const task = taskAfter(
      moveTask(ctx, 't', { newStart: at(TUE, 6, 30), fromResource: 'bruno', toResource: 'chloe' }),
      't',
    );
    expect(task.assignments).toEqual({ anna: { note: 'chef' }, chloe: {} });
    expect(bar(task)).toEqual(span(at(TUE, 6, 30), at(TUE, 16, 30)));
    const replacedFirst = taskAfter(
      moveTask(ctx, 't', { newStart: at(MON, 6, 30), fromResource: 'anna', toResource: 'emma' }),
      't',
    );
    expect(assigneeIds(replacedFirst)).toEqual(['emma', 'bruno']);
    expect(replacedFirst.assignments?.emma).toEqual({ note: 'chef' });
  });

  it('changes nothing when dropped onto someone already assigned', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna', 'bruno'] }));
    expect(moveTask(ctx, 't', { newStart: at(TUE, 6, 30), fromResource: 'anna', toResource: 'bruno' })).toEqual([]);
  });

  it('moves to and from the unassigned row', () => {
    const ctx = ctxWith(
      makeTask('t', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna'] }),
      makeTask('free', { start: at(MON, 6, 30), end: at(MON, 12) }),
    );
    const unassigned = taskAfter(
      moveTask(ctx, 't', { newStart: at(MON, 6, 30), fromResource: 'anna', toResource: UNASSIGNED }),
      't',
    );
    expect(isScheduled(unassigned)).toBe(true);
    expect(unassigned.assignments).toEqual({});
    const taken = taskAfter(
      moveTask(ctx, 'free', { newStart: at(TUE, 6, 30), fromResource: UNASSIGNED, toResource: 'bruno' }),
      'free',
    );
    expect(assigneeIds(taken)).toEqual(['bruno']);
    expect(bar(taken)).toEqual(span(at(TUE, 6, 30), at(TUE, 12)));
    refuses(
      () => moveTask(ctx, 't', { newStart: at(MON, 6, 30), fromResource: UNASSIGNED, toResource: 'bruno' }),
      /Non affecté/,
    );
  });

  it('refuses impossible moves and changes nothing without a move', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna'] }), makeTask('card'));
    expect(moveTask(ctx, 't', { newStart: at(MON, 6, 40) })).toEqual([]);
    refuses(() => moveTask(ctx, 'card', { newStart: at(MON, 6, 30) }), /pas planifiée/);
    refuses(() => moveTask(ctx, 't', { newStart: at(MON, 6, 30), fromResource: 'bruno', toResource: 'chloe' }), /pas affectée/);
    refuses(() => moveTask(ctx, 't', { newStart: at(MON, 6, 30), fromResource: 'anna', toResource: 'dylan' }), /archivé/);
  });

  it('keeps the weekday hours of a task lying on a holiday', () => {
    const christmas = dayNumber(2026, 12, 25);
    const ctx = ctxWith(makeTask('t', { start: at(christmas, 6, 30), end: at(christmas, 12), people: ['anna'] }));
    const task = taskAfter(moveTask(ctx, 't', { newStart: at(dayNumber(2026, 12, 28), 6, 30) }), 't');
    expect(task.end).toBe(stored(at(dayNumber(2026, 12, 28), 12)));
  });
});

// -------------------------------------------------------------------- resize ----

describe('resizeTask', () => {
  it('snaps the edges onto worked time', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(TUE, 16, 30), people: ['anna'] }));
    expect(taskAfter(resizeTask(ctx, 't', { end: at(TUE, 12, 40) }), 't').end).toBe(stored(at(TUE, 12)));
    expect(taskAfter(resizeTask(ctx, 't', { start: at(MON, 12, 20) }), 't').start).toBe(stored(at(MON, 13)));
    expect(taskAfter(resizeTask(ctx, 't', { end: at(SAT, 10) }), 't').end).toBe(stored(at(FRI, 12)));
    expect(resizeTask(ctx, 't', {})).toEqual([]);
  });

  it('keeps at least thirty minutes', () => {
    // Regression: an edge dragged onto or past the other one raised instead of stopping.
    const ctx = ctxWith(makeTask('t', { start: at(MON, 13), end: at(MON, 16, 30), people: ['anna'] }));
    expect(taskAfter(resizeTask(ctx, 't', { end: at(MON, 12, 30) }), 't').end).toBe(stored(at(MON, 13, 30)));
    expect(taskAfter(resizeTask(ctx, 't', { end: at(MON, 6) }), 't').end).toBe(stored(at(MON, 13, 30)));
    expect(taskAfter(resizeTask(ctx, 't', { start: at(MON, 16, 30) }), 't').start).toBe(stored(at(MON, 16)));
    expect(taskAfter(resizeTask(ctx, 't', { start: at(TUE, 9) }), 't').start).toBe(stored(at(MON, 16)));
    expect(taskAfter(resizeTask(ctx, 't', { end: at(MON, 13, 30) }), 't').end).toBe(stored(at(MON, 13, 30)));
    const morning = ctxWith(makeTask('m', { start: at(MON, 6, 30), end: at(MON, 8), people: ['anna'] }));
    expect(taskAfter(resizeTask(morning, 'm', { end: at(MON, 6, 30) }), 'm').end).toBe(stored(at(MON, 7)));
    const afternoon = ctxWith(makeTask('a', { start: at(MON, 13), end: at(MON, 14), people: ['anna'] }));
    expect(taskAfter(resizeTask(afternoon, 'a', { start: at(MON, 14) }), 'a').start).toBe(stored(at(MON, 13, 30)));
    refuses(() => resizeTask(ctx, 't', { start: at(MON, 15), end: at(MON, 14) }), /fin doit suivre/);
    const both = taskAfter(resizeTask(ctx, 't', { start: at(MON, 11, 30), end: at(MON, 13, 10) }), 't');
    expect(bar(both)).toEqual(span(at(MON, 11, 30), at(MON, 12)));
  });
});

// ---------------------------------------------------------- assign / unassign ----

describe('assign and unassign', () => {
  it('adds and removes a second person', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(MON, 16, 30), people: ['anna'] }));
    expect(assigneeIds(taskAfter(assign(ctx, 't', 'bruno'), 't'))).toEqual(['anna', 'bruno']);
    expect(assign(ctx, 't', 'anna')).toEqual([]);
    refuses(() => assign(ctx, 't', 'dylan'), /archivé/);
    const two = applied(ctx, assign(ctx, 't', 'bruno'));
    const kept = taskAfter(unassign(two, 't', 'anna'), 't');
    expect(isScheduled(kept)).toBe(true);
    expect(assigneeIds(kept)).toEqual(['bruno']);
    expect(unassign(ctx, 't', 'chloe')).toEqual([]);
  });

  it('sends the task back to the backlog without its last person', () => {
    const ctx = ctxWith(
      makeTask('card', { backlog_rank: 'm' }),
      makeTask('t', {
        start: at(THU, 13),
        end: at(FRI, 12),
        people: ['anna'],
        origin: 'sortie_dossier',
        estimate_minutes: 60,
      }),
    );
    const changes = unassign(ctx, 't', 'anna');
    const task = taskAfter(changes, 't');
    expect(isScheduled(task)).toBe(false);
    expect(task.assignments).toEqual({});
    expect(task.estimate_minutes).toBe(540);
    expect(task.origin).toBe('sortie_dossier');
    expect((task.backlog_rank ?? '') > 'm').toBe(true);
    expect(backlogIds(apply(ctx.data, changes))).toEqual(['card', 't']);
  });

  it('keeps the card in the backlog when a person foreseen is removed', () => {
    const ctx = ctxWith(makeTask('card', { people: ['anna'], backlog_rank: 'i' }));
    const task = taskAfter(unassign(ctx, 'card', 'anna'), 'card');
    expect(task.assignments).toEqual({});
    expect(task.backlog_rank).toBe('i');
  });

  it('unscheduleTask keeps the people as foreseen', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(TUE, 12), people: ['anna', 'bruno'] }));
    const task = taskAfter(unscheduleTask(ctx, 't'), 't');
    expect(isScheduled(task)).toBe(false);
    expect(assigneeIds(task)).toEqual(['anna', 'bruno']);
    expect(task.estimate_minutes).toBe(540 + 330);
    expect(unscheduleTask(applied(ctx, unscheduleTask(ctx, 't')), 't')).toEqual([]);
  });
});

// ------------------------------------------------------------------- details ----

describe('details', () => {
  it('setStatus and updateTask', () => {
    const ctx = ctxWith(
      makeTask('t', {
        project_number: '2026-0001',
        project_cache: { client: 'Client SA', contact: '', designation: '', managed_by: '', location: '' },
        project_fingerprint: 'abc',
      }),
    );
    expect(taskAfter(setStatus(ctx, 't', 'provisional'), 't').status).toBe('provisional');
    expect(setStatus(ctx, 't', 'confirmed')).toEqual([]);
    refuses(() => setStatus(ctx, 't', 'lost' as TaskStatus), /Statut/);
    const updated = taskAfter(updateTask(ctx, 't', { title: '  Nouveau  ', urgent: true, deadline: '2026-10-30', notes: 'x' }), 't');
    expect(updated.title).toBe('Nouveau');
    expect(updated.urgent).toBe(true);
    expect(updated.deadline).toBe('2026-10-30');
    expect(updated.project_cache?.client).toBe('Client SA');
    const renumbered = taskAfter(updateTask(ctx, 't', { project_number: '2026-0002' }), 't');
    // Desktop: ProjectCache() (every field empty); the phone writes {}, read alike.
    expect(Object.values(renumbered.project_cache ?? {}).every((value) => !value)).toBe(true);
    expect(renumbered.project_fingerprint).toBe('');
    expect(updateTask(ctx, 't', { title: 'Tache t' })).toEqual([]);
  });

  it('a new project drops the old fingerprint', () => {
    // Regression: a new number given with its cache kept the previous dossier's fingerprint.
    // `project_cache` and `project_fingerprint` are not in TaskFields: given as JSON.
    const cache = (client: string) => ({ client, contact: '', designation: '', managed_by: '', location: '' });
    const ctx = ctxWith(makeTask('t', { project_number: '2026-0001', project_cache: cache('A'), project_fingerprint: 'fp-of-0001' }));
    const withCache = taskAfter(
      updateTask(ctx, 't', { project_number: '2026-0002', project_cache: cache('B') } as TaskFields),
      't',
    );
    expect(withCache.project_cache?.client).toBe('B');
    expect(withCache.project_fingerprint).toBe('');
    const complete = taskAfter(
      updateTask(ctx, 't', {
        project_number: '2026-0002',
        project_cache: cache('B'),
        project_fingerprint: 'fp-of-0002',
      } as TaskFields),
      't',
    );
    expect(complete.project_fingerprint).toBe('fp-of-0002');
    const refreshed = taskAfter(updateTask(ctx, 't', { project_fingerprint: 'fp-new' } as TaskFields), 't');
    expect(refreshed.project_fingerprint).toBe('fp-new');
    expect(refreshed.project_cache?.client).toBe('A');
    const same = taskAfter(updateTask(ctx, 't', { project_number: ' 2026-0001 ', notes: 'x' }), 't');
    expect(same.project_fingerprint).toBe('fp-of-0001');
  });

  it.each<[string, TaskFields, RegExp]>([
    ['an unknown type', { task_type: 'inconnu' }, /Type de tâche inconnu/],
    ['negative minutes', { estimate_minutes: -30 }, /estimate_minutes/],
  ])('updateTask refuses %s', (_, fields, message) => {
    refuses(() => updateTask(ctxWith(makeTask('t')), 't', fields), message);
  });

  it('updateTask keeps one person required at least (the desktop refuses 0)', () => {
    // Desktop: required_people=0 raises « required_people »; the phone clamps to 1.
    expect(updateTask(ctxWith(makeTask('t')), 't', { required_people: 0 })).toEqual([]);
  });

  it('updateTask refuses an unreadable deadline', () => {
    refuses(() => updateTask(ctxWith(makeTask('t')), 't', { deadline: 'demain' }), /deadline/);
  });

  it('deletes and restores a task', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(MON, 12), people: ['anna'] }));
    const deleted = taskAfter(deleteTask(ctx, 't'), 't');
    expect(deleted.deleted_at).toBe(NOW);
    expect(deleted.deleted_by).toEqual(AUTHOR);
    expect(isScheduled(deleted)).toBe(true);
    const afterDelete = applied(ctx, deleteTask(ctx, 't'));
    expect(deleteTask(afterDelete, 't')).toEqual([]);
    refuses(() => moveTask(afterDelete, 't', { newStart: at(TUE, 6, 30) }), /supprimée/);
    const restored = taskAfter(restoreTask(afterDelete, 't'), 't');
    expect(restored.deleted_at).toBeNull();
    expect(restored.deleted_by).toBeNull();
    expect(restoreTask(ctx, 't')).toEqual([]);
  });
});

// ------------------------------------------------------------------ absences ----

describe('absences', () => {
  it('creates and deletes an absence', () => {
    const ctx = ctxWith();
    const changes = createAbsence(ctx, { resourceId: 'anna', firstDay: TUE, reason: 'vacances' });
    const absence = changes[0].after as Absence;
    expect([absence.first_day, absence.last_day]).toEqual(['2026-10-27', '2026-10-27']);
    expect(absence.created_by).toEqual(AUTHOR);
    const withAbsence = applied(ctx, changes);
    const deleted = only(deleteAbsence(withAbsence, absence.id), absence.id) as Absence;
    expect(deleted.deleted_at).toBe(NOW);
    const gone = applied(withAbsence, deleteAbsence(withAbsence, absence.id));
    expect(deleteAbsence(gone, absence.id)).toEqual([]);
  });

  it('refuses impossible absences', () => {
    const ctx = ctxWith();
    refuses(() => createAbsence(ctx, { resourceId: 'anna', firstDay: FRI, lastDay: MON }), /précède/);
    refuses(() => createAbsence(ctx, { resourceId: 'nobody', firstDay: MON }), /introuvable/);
    refuses(() => createAbsence(ctx, { resourceId: 'anna', firstDay: MON, reason: 'plage' }), /reason/);
  });

  it('refuses to delete a missing absence', () => {
    refuses(() => deleteAbsence(ctxWith(), 'missing'), /Absence introuvable/);
  });
});

// ---------------------------------------------------------- calendar resolver ----

describe('calendarResolver', () => {
  it('uses the cantons and the closures', () => {
    const meta: PlanningMeta = { id: 'meta', closures: [{ first_day: '2026-12-28', last_day: '2026-12-30' }] };
    const calendarFor = calendarResolver(makeData([], meta));
    expect(calendarFor(null).cantons).toEqual(['GE']);
    expect(calendarFor('chloe').cantons).toEqual(['VD']);
    expect(calendarFor('nobody').cantons).toEqual(['GE']);
    expect(calendarFor('anna')).toBe(calendarFor(null));
    expect(calendarFor('anna').isWorkday(dayNumber(2026, 12, 29))).toBe(false);
    expect(calendarFor('chloe').isWorkday(dayNumber(2026, 9, 10))).toBe(true);
    expect(calendarFor('anna').isWorkday(dayNumber(2026, 9, 10))).toBe(false);
    expect(calendarFor('anna')).toBeInstanceOf(WorkCalendar);
  });
});

// -------------------------------------------------------------------- purity ----

describe('purity', () => {
  it('commands never modify their snapshot and undo restores it', () => {
    const data = deepFrozen(
      makeData([
        makeTask('t', { start: at(MON, 6, 30), end: at(TUE, 12), people: ['anna', 'bruno'] }),
        makeTask('card', { backlog_rank: 'i', people: ['chloe'] }),
        makeTask('other', { backlog_rank: 'k' }),
      ]),
    );
    const ctx = makeCtx(data);
    const before = JSON.stringify(data);
    // Desktop batches not ported: duplicate_task, detach_assignment, move_backlog,
    // shift_tasks, reorder_resource, update_meta.
    const batches = [
      moveTask(ctx, 't', { newStart: at(WED, 6, 30), fromResource: 'anna', toResource: 'chloe' }),
      resizeTask(ctx, 't', { end: at(WED, 12) }),
      assign(ctx, 't', 'emma'),
      unassign(ctx, 't', 'anna'),
      unscheduleTask(ctx, 't'),
      scheduleTask(ctx, 'card', { start: at(MON, 6, 30), resourceIds: [] }),
      updateTask(ctx, 't', { title: 'Nouveau' }),
      deleteTask(ctx, 't'),
    ];
    expect(JSON.stringify(data)).toBe(before);
    for (const changes of batches) {
      expect(changes.length).toBeGreaterThan(0);
      expect(changes.every((change) => change.after !== null && change.after.updated_at === NOW)).toBe(true);
      const undone = apply(apply(data, changes), [...changes].reverse().map(inverse));
      expect(JSON.stringify(undone)).toBe(before);
    }
  });

  it('the default duration is one full worked day', () => {
    expect(new WorkCalendar().workedMinutes(MON)).toBe(DEFAULT_TASK_MINUTES);
  });
});

// -------------------------------------------------------------- working days ----

const SEPT_WED = dayNumber(2026, 9, 9); // the Jeûne genevois is on Thursday 10 September 2026
const SEPT_THU = dayNumber(2026, 9, 10);
const SEPT_FRI = dayNumber(2026, 9, 11);
const SEPT_MON = dayNumber(2026, 9, 21); // Lundi du Jeûne, Vaud only
const SEPT_TUE = dayNumber(2026, 9, 22);

describe('working days', () => {
  it('createTask stores days and places them as working days', () => {
    const ctx = ctxWith();
    const card = firstTask(createTask(ctx, { title: 'A', taskType: 'debit', estimateMinutes: 1080, estimateDays: 2 }));
    expect([isScheduled(card), card.estimate_days, card.estimate_minutes]).toEqual([false, 2, 1080]);
    const thursday = firstTask(createTask(ctx, { title: 'B', taskType: 'debit', start: at(THU, 6, 30), estimateDays: 2 }));
    expect(bar(thursday)).toEqual(span(at(THU, 6, 30), at(FRI, 12)));
    const week = firstTask(createTask(ctx, { title: 'C', taskType: 'debit', start: at(MON, 6, 30), estimateDays: 5 }));
    expect(week.end).toBe(stored(at(FRI, 12)));
    const afternoon = firstTask(createTask(ctx, { title: 'D', taskType: 'debit', start: at(MON, 13), estimateDays: 0.5 }));
    expect(bar(afternoon)).toEqual(span(at(MON, 13), at(MON, 16, 30)));
  });

  it('createTask: days win over minutes and an end wins over both', () => {
    const ctx = ctxWith();
    const days = firstTask(
      createTask(ctx, { title: 'A', taskType: 'debit', start: at(THU, 6, 30), estimateMinutes: 60, estimateDays: 1 }),
    );
    expect(days.end).toBe(stored(at(THU, 16, 30)));
    const ended = firstTask(
      createTask(ctx, { title: 'B', taskType: 'debit', start: at(MON, 6, 30), end: at(MON, 12), estimateDays: 3 }),
    );
    expect([ended.end, ended.estimate_days]).toEqual([stored(at(MON, 12)), 3]);
    refuses(() => createTask(ctx, { title: 'C', taskType: 'debit', estimateDays: -1 }), /estimate_days/);
  });

  it('createTask: days skip the holidays of the first person', () => {
    const ctx = ctxWith();
    const geneva = firstTask(
      createTask(ctx, { title: 'A', taskType: 'montage', start: at(SEPT_WED, 6, 30), estimateDays: 2, resourceIds: ['anna'] }),
    );
    const vaud = firstTask(
      createTask(ctx, { title: 'B', taskType: 'montage', start: at(SEPT_WED, 6, 30), estimateDays: 2, resourceIds: ['chloe'] }),
    );
    expect(geneva.end).toBe(stored(at(SEPT_FRI, 12)));
    expect(vaud.end).toBe(stored(at(SEPT_THU, 16, 30)));
  });

  it('scheduleTask places a card by its working days', () => {
    const ctx = ctxWith(makeTask('card', { estimate_minutes: 1080, estimate_days: 2, people: ['anna'] }));
    const thursday = taskAfter(scheduleTask(ctx, 'card', { start: at(THU, 6, 30), resourceIds: [] }), 'card');
    expect(bar(thursday)).toEqual(span(at(THU, 6, 30), at(FRI, 12)));
    expect([thursday.estimate_days, thursday.estimate_minutes]).toEqual([2, 1080]);
    const ended = taskAfter(scheduleTask(ctx, 'card', { start: at(MON, 6, 30), end: at(MON, 12), resourceIds: [] }), 'card');
    expect(ended.end).toBe(stored(at(MON, 12)));
  });

  it('scheduleTask: days follow the canton of the first assignee', () => {
    const ctx = ctxWith(makeTask('card', { estimate_days: 2 }));
    const friday = dayNumber(2026, 9, 18);
    const vaud = taskAfter(scheduleTask(ctx, 'card', { start: at(friday, 6, 30), resourceIds: ['chloe', 'anna'] }), 'card');
    const geneva = taskAfter(scheduleTask(ctx, 'card', { start: at(friday, 6, 30), resourceIds: ['anna', 'chloe'] }), 'card');
    expect(vaud.end).toBe(stored(at(SEPT_TUE, 16, 30)));
    expect(geneva.end).toBe(stored(at(SEPT_MON, 16, 30)));
  });

  it('scheduleTask again ignores the days of its estimate', () => {
    const ctx = ctxWith(makeTask('t', { start: at(MON, 6, 30), end: at(TUE, 12), estimate_days: 5, people: ['anna'] }));
    const task = taskAfter(scheduleTask(ctx, 't', { start: at(THU, 6, 30), resourceIds: [] }), 't');
    expect(bar(task)).toEqual(span(at(THU, 6, 30), at(FRI, 12)));
  });

  it.each<[Instant, Instant, string[], number, number]>([
    [at(MON, 6, 30), at(TUE, 12), ['anna'], 1.5, 870],
    [at(THU, 6, 30), at(FRI, 12), ['anna'], 2, 870],
    [at(MON, 13), at(MON, 16, 30), ['anna'], 0.5, 210],
    [at(MON, 6, 30), at(MON, 10), ['anna'], 0, 210],
    [at(MON, 10), at(TUE, 16, 30), ['anna'], 0, 870],
    [at(SEPT_WED, 6, 30), at(SEPT_THU, 16, 30), ['chloe'], 2, 1080],
    [at(SEPT_WED, 6, 30), at(SEPT_FRI, 12), ['anna'], 2, 870],
  ])('unscheduleTask keeps whole days as estimate (%#)', (start, end, assignees, days, minutes) => {
    const ctx = ctxWith(makeTask('t', { start, end, estimate_days: 4, people: assignees }));
    const task = taskAfter(unscheduleTask(ctx, 't'), 't');
    expect(isScheduled(task)).toBe(false);
    expect([task.estimate_days, task.estimate_minutes]).toEqual([days, minutes]);
  });

  it('unassigning the last person keeps whole days on their calendar', () => {
    const ctx = ctxWith(makeTask('t', { start: at(SEPT_WED, 6, 30), end: at(SEPT_THU, 16, 30), people: ['chloe'] }));
    const task = taskAfter(unassign(ctx, 't', 'chloe'), 't');
    expect([task.estimate_days, task.estimate_minutes]).toEqual([2, 1080]);
    const half = ctxWith(makeTask('h', { start: at(THU, 13), end: at(FRI, 12), people: ['anna'] }));
    expect(taskAfter(unassign(half, 'h', 'anna'), 'h').estimate_days).toBe(1.5);
  });

  it('a card goes back and forth with its days', () => {
    const ctx = ctxWith(makeTask('card', { estimate_days: 2, people: ['anna'] }));
    const placed = applied(ctx, scheduleTask(ctx, 'card', { start: at(THU, 6, 30), resourceIds: [] }));
    const back = applied(placed, unscheduleTask(placed, 'card'));
    expect(taskIn(back, 'card').estimate_days).toBe(2);
    const again = taskAfter(scheduleTask(back, 'card', { start: at(MON, 6, 30), resourceIds: [] }), 'card');
    expect(bar(again)).toEqual(span(at(MON, 6, 30), at(TUE, 16, 30)));
  });

  it.each<[number, Instant, Instant]>([
    // wholeDays reads these bars as 1 j, 2 j, nothing, nothing: the card keeps its own.
    [0.5, at(FRI, 6, 30), at(FRI, 12)],
    [1.5, at(THU, 6, 30), at(FRI, 12)],
    [2, at(THU, 11), at(FRI, 12)],
    [1, at(MON, 15), at(TUE, 12)],
    // An afternoon start is half of its day: 1 j from Monday 13:00 ends Tuesday noon.
    [1, at(MON, 13), at(TUE, 12)],
    [2, at(THU, 6, 30), at(FRI, 12)],
    [2, at(THU, 13), at(NEXT_MON, 12)],
    [0.5, at(MON, 13), at(MON, 16, 30)],
  ])('a card of %d j keeps its days through place and send back (%#)', (days, start, end) => {
    const ctx = ctxWith(makeTask('card', { estimate_days: days, people: ['anna'] }));
    const placed = applied(ctx, scheduleTask(ctx, 'card', { start, resourceIds: [] }));
    expect(bar(taskIn(placed, 'card'))).toEqual(span(start, end));
    const unscheduled = taskAfter(unscheduleTask(placed, 'card'), 'card');
    const unassigned = taskAfter(unassign(placed, 'card', 'anna'), 'card');
    expect([unscheduled.estimate_days, unassigned.estimate_days]).toEqual([days, days]);
    const back = applied(placed, unscheduleTask(placed, 'card'));
    const again = taskAfter(scheduleTask(back, 'card', { start, resourceIds: [] }), 'card');
    expect(bar(again)).toEqual(span(start, end));
  });

  it.each<[TaskSpec, number]>([
    // A Friday morning is the whole Friday: 1 j, whatever estimate placed it.
    [{ start: at(FRI, 6, 30), end: at(FRI, 12), estimate_days: 0.5 }, 1],
    [{ start: at(FRI, 6, 30), end: at(FRI, 12) }, 1],
    [{ start: at(THU, 6, 30), end: at(FRI, 12), estimate_days: 1.5 }, 2],
    [{ start: at(THU, 6, 30), end: at(FRI, 12), estimate_days: 3 }, 2],
    // Not whole half days: the bar reads in hours.
    [{ start: at(THU, 11), end: at(FRI, 12), estimate_days: 2 }, 0],
    [{ start: at(THU, 11), end: at(FRI, 12) }, 0],
    [{ start: at(THU, 13), end: at(NEXT_MON, 12), estimate_days: 2 }, 2],
    [{ start: at(MON, 6, 30), end: at(MON, 16, 30), estimate_days: 1.25 }, 1],
    [{ start: at(MON, 6, 30), end: at(TUE, 12), estimate_days: 1.25 }, 1.5],
    [{ estimate_days: 2 }, 0],
  ])('taskDays reads a bar as the bar shows it (%#)', (spec, days) => {
    // Desktop: task_days(WorkCalendar(cantons=("GE",)), task); the phone takes the context,
    // whose calendar for a task without assignee is the Geneva one.
    const calendar = new WorkCalendar({ cantons: ['GE'] });
    const ctx = ctxWith();
    const task = makeTask('t', spec);
    expect(taskDays(ctx, task)).toBe(days);
    const start = parseInstant(task.start ?? '');
    const end = parseInstant(task.end ?? '');
    if (start !== null && end !== null) {
      // The bar, the drag label and the Détail read wholeDays: the hover says the same.
      expect(taskDays(ctx, task)).toBe(calendar.wholeDays(start, end) ?? 0);
    }
  });

  // Desktop `card_days` is private here (`cardDays`): read through the estimate_days that
  // unscheduleTask writes. The desktop's last case (no dates: 0) has no bar to send back.
  it.each<[TaskSpec, number]>([
    [{ start: at(FRI, 6, 30), end: at(FRI, 12), estimate_days: 0.5 }, 0.5],
    [{ start: at(FRI, 6, 30), end: at(FRI, 12) }, 1],
    [{ start: at(THU, 6, 30), end: at(FRI, 12), estimate_days: 1.5 }, 1.5],
    [{ start: at(THU, 6, 30), end: at(FRI, 12), estimate_days: 3 }, 2],
    [{ start: at(THU, 11), end: at(FRI, 12), estimate_days: 2 }, 2],
    [{ start: at(THU, 13), end: at(NEXT_MON, 12), estimate_days: 2 }, 2],
  ])('card days keep the days that placed the bar (%#)', (spec, days) => {
    const ctx = ctxWith(makeTask('t', spec));
    expect(taskAfter(unscheduleTask(ctx, 't'), 't').estimate_days).toBe(days);
  });

  it('a card dropped on an afternoon reads its days everywhere', () => {
    // ~2 j dropped on Thursday 13:00: Monday noon, and 2 j on the bar and the hover.
    const calendar = new WorkCalendar({ cantons: ['GE'] });
    const ctx = ctxWith(makeTask('card', { estimate_days: 2, people: ['anna'] }));
    const placed = taskAfter(scheduleTask(ctx, 'card', { start: at(THU, 13), resourceIds: [] }), 'card');
    expect(bar(placed)).toEqual(span(at(THU, 13), at(NEXT_MON, 12)));
    expect(calendar.wholeDays(at(THU, 13), at(NEXT_MON, 12))).toBe(2);
    expect(taskDays(ctx, placed)).toBe(2);
    const three = ctxWith(makeTask('card', { estimate_days: 3, people: ['anna'] }));
    const wide = taskAfter(scheduleTask(three, 'card', { start: at(WED, 13), resourceIds: [] }), 'card');
    expect(wide.end).toBe(stored(at(NEXT_MON, 12)));
    expect(taskDays(three, wide)).toBe(3);
  });

  it('a bar changed since placed goes back with the days it covers', () => {
    const ctx = ctxWith(makeTask('card', { estimate_days: 2, people: ['anna'] }));
    const placed = applied(ctx, scheduleTask(ctx, 'card', { start: at(THU, 6, 30), resourceIds: [] }));
    const moved = applied(placed, moveTask(placed, 'card', { newStart: at(MON, 6, 30) }));
    expect(taskIn(moved, 'card').end).toBe(stored(at(TUE, 12)));
    expect(taskAfter(unscheduleTask(moved, 'card'), 'card').estimate_days).toBe(1.5);
    const resized = applied(placed, resizeTask(placed, 'card', { end: at(FRI, 10) }));
    expect(taskAfter(unscheduleTask(resized, 'card'), 'card').estimate_days).toBe(0);
  });

  it.each<[number, number, Instant]>([
    [1.25, 1.5, at(TUE, 12)],
    [2.25, 2.5, at(WED, 12)],
    [0.25, 0.5, at(MON, 12)],
    [0.2, 0.5, at(MON, 12)],
    [3.75, 4, at(THU, 16, 30)],
    [2, 2, at(TUE, 16, 30)],
    [0, 0, at(MON, 16, 30)],
  ])('days typed %d are stored rounded (%d) as they are shown', (typed, rounded, end) => {
    const ctx = ctxWith(makeTask('card', { estimate_days: 3 }));
    const updated = taskAfter(updateTask(ctx, 'card', { estimate_days: typed }), 'card');
    const created = firstTask(createTask(ctx, { title: 'A', taskType: 'debit', estimateDays: typed }));
    expect([updated.estimate_days, created.estimate_days]).toEqual([rounded, rounded]);
    const placed = firstTask(
      createTask(ctx, { title: 'B', taskType: 'debit', start: at(MON, 6, 30), estimateDays: typed, resourceIds: ['anna'] }),
    );
    expect([placed.estimate_days, placed.end]).toEqual([rounded, stored(end)]);
  });

  it('days stored unrounded elsewhere are placed as shown', () => {
    const ctx = ctxWith(makeTask('card', { estimate_days: 1.25, people: ['anna'] }));
    const task = taskAfter(scheduleTask(ctx, 'card', { start: at(MON, 6, 30), resourceIds: [] }), 'card');
    expect(task.end).toBe(stored(at(TUE, 12)));
  });

  it.each([Infinity, NaN, -0.5, 251])('refuses %d days', (days) => {
    const ctx = ctxWith(makeTask('card'));
    refuses(() => updateTask(ctx, 'card', { estimate_days: days }), /estimate_days/);
    refuses(() => createTask(ctx, { title: 'A', taskType: 'debit', estimateDays: days }), /estimate_days/);
  });

  it('a card with unreadable days cannot be placed', () => {
    const card = makeTask('card', { people: ['anna'], estimate_days: Infinity });
    refuses(() => scheduleTask(ctxWith(card), 'card', { start: at(MON, 6, 30), resourceIds: [] }), /estimate_days/);
  });

  it('updateTask changes the days of the estimate', () => {
    const ctx = ctxWith(makeTask('card'));
    expect(taskAfter(updateTask(ctx, 'card', { estimate_days: 1.5 }), 'card').estimate_days).toBe(1.5);
    refuses(() => updateTask(ctx, 'card', { estimate_days: -1 }), /estimate_days/);
  });

  // Not in the desktop's tests: the « Durée » of the phone's detail.
  it('setTaskDays keeps the start and stores the days', () => {
    const ctx = ctxWith(
      makeTask('t', { start: at(THU, 6, 30), end: at(THU, 16, 30), people: ['anna'] }),
      makeTask('card', { people: ['anna'] }),
    );
    const task = taskAfter(setTaskDays(ctx, 't', 2), 't');
    expect(bar(task)).toEqual(span(at(THU, 6, 30), at(FRI, 12)));
    expect(task.estimate_days).toBe(2);
    expect(taskDays(ctx, task)).toBe(2);
    expect(taskAfter(setTaskDays(ctx, 't', 1.25), 't').end).toBe(stored(at(FRI, 12)));
    refuses(() => setTaskDays(ctx, 't', 0), /estimate_days/);
    refuses(() => setTaskDays(ctx, 'card', 1), /pas planifiée/);
  });
});
