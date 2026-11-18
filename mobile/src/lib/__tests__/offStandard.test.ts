/**
 * Port of `tests/unit/test_planning_off_standard_core.py`: « Hors horaires standard », a
 * task let out of the working hours inside a day. Limited to what the phone has (no
 * duplication, keyboard steps, conflicts nor Outlook copy).
 */

import { continuousWeek, DEFAULT_WORK_WEEK, WorkCalendar } from '@/lib/planning/calendar';
import {
  calendarResolver,
  createTask,
  moveTask,
  PlanningCommandError,
  resizeTask,
  scheduleTask,
  setOffStandard,
  standardSpan,
  taskHours,
  unscheduleTask,
  type CommandContext,
} from '@/lib/planning/commands';
import type { Change } from '@/lib/planning/documents';
import { formatInstant, instant, type Instant } from '@/lib/planning/instant';
import type { Author, PlanningData, PlanningTask, Resource } from '@/lib/planning/models';
import { dayNumber } from '@/lib/planning/walltime';
import { buildBoard } from '@/lib/planning/week';

const AUTHOR: Author = { initials: 'LM', name: 'Lionel Mottiez', poste: 'iPhone', poste_id: 'phone-1' };
const NOW = '2026-10-22T10:00:00Z';

const MON = dayNumber(2026, 10, 26);
const TUE = dayNumber(2026, 10, 27);
const FRI = dayNumber(2026, 10, 30);
const SAT = dayNumber(2026, 10, 31);
const NEXT_MON = dayNumber(2026, 11, 2);
const CHRISTMAS = dayNumber(2026, 12, 25);

const GE = new WorkCalendar();
const OPEN = GE.withoutBreaks();
const HALVES = [
  [390, 720],
  [720, 990],
];

function at(day: number, hour: number, minute = 0): Instant {
  return instant(day, hour, minute);
}

function person(id: string, name: string): Resource {
  return { id, name, initials: '', team: 'atelier', active: true, canton: '' };
}

type Spec = Omit<Partial<PlanningTask>, 'start' | 'end'> & { start?: Instant | null; end?: Instant | null; people?: readonly string[] };

function makeTask(id: string, spec: Spec = {}): PlanningTask {
  const { people = [], start = null, end = null, ...fields } = spec;
  return {
    id,
    status: 'confirmed',
    task_type: 'soudure',
    title: `Tache ${id}`,
    project_number: '',
    start: start === null ? null : formatInstant(start),
    end: end === null ? null : formatInstant(end),
    estimate_minutes: 0,
    estimate_days: 0,
    assignments: Object.fromEntries(people.map((who) => [who, {}])),
    backlog_rank: '',
    off_hours: false,
    ...fields,
  };
}

/** A task « hors horaires standard » of Anna, two hours on Monday morning by default. */
function free(id = 'free', spec: Spec = {}): PlanningTask {
  return makeTask(id, { start: at(MON, 6, 30), end: at(MON, 8, 30), people: ['anna'], off_standard: true, ...spec });
}

function held(id = 'held', spec: Spec = {}): PlanningTask {
  return free(id, { off_standard: false, ...spec });
}

function ctxWith(...tasks: PlanningTask[]): CommandContext {
  const data: PlanningData = { tasks, resources: [person('anna', 'Anna Favre'), person('bruno', 'Bruno Rossier')], absences: [], meta: null };
  return { data, author: AUTHOR, now: NOW, calendarFor: calendarResolver(data) };
}

function taskAfter(changes: Change[], id: string): PlanningTask {
  const change = changes.find((candidate) => candidate.id === id);
  if (!change?.after) throw new Error(`no change for ${id}`);
  return change.after as unknown as PlanningTask;
}

function bar(task: PlanningTask): [string | null, string | null] {
  return [task.start ?? null, task.end ?? null];
}

function span(start: Instant, end: Instant): [string, string] {
  return [formatInstant(start), formatInstant(end)];
}

// ----------------------------------------------------------------- calendar ----

describe('the calendar without breaks', () => {
  it('leaves the usual hours as they are: lunch and Friday afternoon blocked', () => {
    expect(DEFAULT_WORK_WEEK[0]).toEqual([
      [390, 720],
      [780, 990],
    ]);
    expect(DEFAULT_WORK_WEEK[4]).toEqual([[390, 720]]);
    expect(GE.isWorked(at(MON, 12, 30))).toBe(false);
    expect(GE.isWorked(at(FRI, 14))).toBe(false);
    expect(GE.workingStart(at(MON, 12, 30))).toBe(at(MON, 13));
    expect(GE.workingStart(at(FRI, 13))).toBe(at(NEXT_MON, 6, 30));
    expect(GE.workingEnd(at(FRI, 15))).toBe(at(FRI, 12));
    expect(GE.minutesPerWeek).toBe(2490);
  });

  it('removes the break and the short day', () => {
    const week = continuousWeek(DEFAULT_WORK_WEEK);

    expect(week).toEqual({ 0: HALVES, 1: HALVES, 2: HALVES, 3: HALVES, 4: HALVES });
    expect(continuousWeek(week)).toEqual(week);
    expect(continuousWeek({})).toEqual({});
    // A weekday without hours stays not worked; the others take the widest day of the week.
    expect(continuousWeek({ 0: [[420, 720]], 2: [[780, 960]] })).toEqual({
      0: [
        [420, 720],
        [720, 960],
      ],
      2: [
        [420, 720],
        [720, 960],
      ],
    });
    // Noon outside the day: one period.
    expect(
      continuousWeek({
        1: [
          [780, 900],
          [930, 1020],
        ],
      }),
    ).toEqual({ 1: [[780, 1020]] });
  });

  it('opens the day and nothing else', () => {
    expect(GE.withoutBreaks()).toBe(OPEN);
    expect(OPEN.withoutBreaks()).toBe(OPEN);
    expect(OPEN.week).toEqual({ 0: HALVES, 1: HALVES, 2: HALVES, 3: HALVES, 4: HALVES });
    expect(OPEN.cantons).toEqual(GE.cantons);
    expect(OPEN.isWorked(at(MON, 12, 30))).toBe(true);
    expect(OPEN.isWorked(at(FRI, 14))).toBe(true);
    expect(OPEN.workingStart(at(MON, 12, 30))).toBe(at(MON, 12, 30));
    expect(OPEN.workingEnd(at(FRI, 15))).toBe(at(FRI, 15));
    expect(OPEN.workBetween(at(MON, 6, 30), at(MON, 16, 30))).toBe(600);
    expect(OPEN.wholeDays(at(MON, 6, 30), at(MON, 16, 30))).toBe(1);
    expect(OPEN.wholeDays(at(MON, 12), at(MON, 16, 30))).toBe(0.5);
    // The weekend and the holidays stay not worked, before 06:30 and after 16:30 too.
    expect(OPEN.isWorked(at(SAT, 9))).toBe(false);
    expect(OPEN.isWorkday(CHRISTMAS)).toBe(false);
    expect(OPEN.isWorked(at(MON, 5))).toBe(false);
    expect(OPEN.isWorked(at(MON, 17))).toBe(false);
    expect(OPEN.workingStart(at(FRI, 17))).toBe(at(NEXT_MON, 6, 30));
  });

  it('keeps the closures', () => {
    const closed = new WorkCalendar({ closures: [TUE] });

    expect(closed.withoutBreaks().isClosed(TUE)).toBe(true);
    expect(closed.withoutBreaks().isWorkday(TUE)).toBe(false);
  });

  it('follows the switch of the task', () => {
    expect(GE.hoursOf(false)).toBe(GE);
    expect(GE.hoursOf(undefined)).toBe(GE);
    expect(GE.hoursOf(true)).toBe(OPEN);
    const ctx = ctxWith(free(), held());
    const [out, kept] = ctx.data.tasks;

    expect(taskHours(kept, ctx.calendarFor)).toBe(ctx.calendarFor('anna'));
    expect(taskHours(out, ctx.calendarFor)).toBe(ctx.calendarFor('anna').withoutBreaks());
  });
});

// ----------------------------------------------------------------- commands ----

describe('the switch on a task', () => {
  it('still moves a task on the usual hours out of the blocked ones', () => {
    const ctx = ctxWith(held());

    expect(bar(taskAfter(moveTask(ctx, 'held', { newStart: at(FRI, 13), fromResource: 'anna' }), 'held'))).toEqual(span(at(NEXT_MON, 6, 30), at(NEXT_MON, 8, 30)));
    expect(bar(taskAfter(moveTask(ctx, 'held', { newStart: at(MON, 12), fromResource: 'anna' }), 'held'))).toEqual(span(at(MON, 13), at(MON, 15)));
    // An end stretched into the lunch break comes back to noon: nothing changes.
    expect(resizeTask(ctxWith(held('held', { start: at(MON, 11), end: at(MON, 12) })), 'held', { end: at(MON, 12, 30) })).toEqual([]);
  });

  it('lets a task out without moving it', () => {
    const changed = taskAfter(setOffStandard(ctxWith(held()), 'held', true), 'held');

    expect(changed.off_standard).toBe(true);
    expect(bar(changed)).toEqual(span(at(MON, 6, 30), at(MON, 8, 30)));
    expect(setOffStandard(ctxWith(free()), 'free', true)).toEqual([]);
  });

  it('lets a task go on Friday afternoon and over lunch', () => {
    const ctx = ctxWith(free());

    expect(bar(taskAfter(moveTask(ctx, 'free', { newStart: at(FRI, 13), fromResource: 'anna' }), 'free'))).toEqual(span(at(FRI, 13), at(FRI, 15)));
    expect(bar(taskAfter(moveTask(ctx, 'free', { newStart: at(MON, 11, 30), fromResource: 'anna' }), 'free'))).toEqual(span(at(MON, 11, 30), at(MON, 13, 30)));
    // Its length counts the lunch hour: three hours from 11:00 end at 14:00.
    const short = ctxWith(free('free', { start: at(MON, 11), end: at(MON, 12) }));
    expect(bar(taskAfter(resizeTask(short, 'free', { end: at(MON, 14) }), 'free'))).toEqual(span(at(MON, 11), at(MON, 14)));
    expect(taskAfter(resizeTask(short, 'free', { end: at(MON, 12, 30) }), 'free').end).toBe(formatInstant(at(MON, 12, 30)));
  });

  it('still skips the weekend and the evening', () => {
    const ctx = ctxWith(free());

    expect(taskAfter(moveTask(ctx, 'free', { newStart: at(SAT, 9), fromResource: 'anna' }), 'free').start).toBe(formatInstant(at(NEXT_MON, 6, 30)));
    expect(bar(taskAfter(moveTask(ctx, 'free', { newStart: at(FRI, 16), fromResource: 'anna' }), 'free'))).toEqual(span(at(FRI, 16), at(NEXT_MON, 8)));
  });

  it('makes a whole day last ten hours', () => {
    const ctx = ctxWith(free('free', { start: at(MON, 6, 30), end: at(MON, 16, 30) }));

    expect(bar(taskAfter(moveTask(ctx, 'free', { newStart: at(FRI, 6, 30), fromResource: 'anna' }), 'free'))).toEqual(span(at(FRI, 6, 30), at(FRI, 16, 30)));
  });

  it('puts a task back on the usual hours with the time it lasted', () => {
    const ctx = ctxWith(
      free('friday', { start: at(FRI, 13), end: at(FRI, 15) }),
      free('lunch', { start: at(MON, 12), end: at(MON, 13) }),
      free('across', { start: at(MON, 11), end: at(MON, 14) }),
    );

    const friday = taskAfter(setOffStandard(ctx, 'friday', false), 'friday');
    expect(friday.off_standard).toBe(false);
    expect(bar(friday)).toEqual(span(at(NEXT_MON, 6, 30), at(NEXT_MON, 8, 30)));
    expect(bar(taskAfter(setOffStandard(ctx, 'lunch', false), 'lunch'))).toEqual(span(at(MON, 13), at(MON, 14)));
    // Three hours from 11:00 on the usual hours: one before lunch, two after.
    expect(bar(taskAfter(setOffStandard(ctx, 'across', false), 'across'))).toEqual(span(at(MON, 11), at(MON, 15)));
    // The same rule, for a span not saved yet (the creation screens).
    expect(standardSpan(GE, OPEN, at(FRI, 13), at(FRI, 15))).toEqual([at(NEXT_MON, 6, 30), at(NEXT_MON, 8, 30)]);
  });

  it('only sets the switch of a card', () => {
    const card = taskAfter(setOffStandard(ctxWith(makeTask('card', { estimate_days: 1.5 })), 'card', true), 'card');

    expect(card.off_standard).toBe(true);
    expect([card.start, card.end, card.estimate_days]).toEqual([null, null, 1.5]);
  });

  it('is refused on night work', () => {
    const night = makeTask('night', { start: at(MON, 21), end: at(TUE, 5), people: ['anna'], off_hours: true });

    expect(() => setOffStandard(ctxWith(night), 'night', true)).toThrow(PlanningCommandError);
    expect(() => setOffStandard(ctxWith(night), 'night', true)).toThrow(/nuit/);
  });

  it('is kept when the phone writes the task again', () => {
    const ctx = ctxWith(free('free', { start: at(FRI, 13), end: at(FRI, 15) }));

    // Any other change of the task leaves the switch as it was.
    const moved = taskAfter(moveTask(ctx, 'free', { newStart: at(FRI, 14), fromResource: 'anna', toResource: 'bruno' }), 'free');
    expect(moved.off_standard).toBe(true);
    expect(bar(moved)).toEqual(span(at(FRI, 14), at(FRI, 16)));
    const placed = taskAfter(scheduleTask(ctx, 'free', { start: at(MON, 12) }), 'free');
    expect(placed.off_standard).toBe(true);
    expect(bar(placed)).toEqual(span(at(MON, 12), at(MON, 14)));
  });
});

describe('a task created out of the usual hours', () => {
  it('keeps the hours given, where a usual task is moved to worked time', () => {
    const ctx = ctxWith();
    const base = { title: 'Livraison', taskType: 'montage', resourceIds: ['anna'] };

    const task = createTask(ctx, { ...base, start: at(FRI, 13), end: at(FRI, 15), offStandard: true })[0].after as unknown as PlanningTask;
    const usual = createTask(ctx, { ...base, start: at(FRI, 13) })[0].after as unknown as PlanningTask;
    const night = createTask(ctx, { ...base, title: 'Pose', start: at(MON, 21), offHours: true, offStandard: true })[0].after as unknown as PlanningTask;

    expect(task.off_standard).toBe(true);
    expect(bar(task)).toEqual(span(at(FRI, 13), at(FRI, 15)));
    expect(usual.off_standard).toBe(false);
    expect(usual.start).toBe(formatInstant(at(NEXT_MON, 6, 30)));
    expect([night.off_hours, night.off_standard]).toEqual([true, false]);
  });

  it('is measured without breaks when it goes back to « À planifier »', () => {
    const ctx = ctxWith(free('free', { start: at(MON, 6, 30), end: at(MON, 16, 30) }));

    const card = taskAfter(unscheduleTask(ctx, 'free'), 'free');

    expect(card.off_standard).toBe(true);
    expect(card.estimate_minutes).toBe(600);
    expect(card.estimate_days).toBe(1);
  });
});

// ------------------------------------------------------------------ display ----

describe('the board', () => {
  const week = [MON, MON + 1, MON + 2, MON + 3, FRI];
  const slots = (task: PlanningTask) => {
    const ctx = ctxWith(task);
    const board = buildBoard(ctx.data, { kind: 'slots', days: week });
    const segment = board.groups.flatMap((group) => group.rows).flatMap((row) => row.segments)[0];
    return [segment.startSlot, segment.span];
  };

  it('draws a task let out on Friday afternoon and over the lunch hour', () => {
    // Friday is the fifth day: its afternoon is the tenth half day.
    expect(slots(free('free', { start: at(FRI, 13), end: at(FRI, 15) }))).toEqual([9, 1]);
    expect(slots(free('free', { start: at(MON, 12), end: at(MON, 13) }))).toEqual([1, 1]);
    expect(slots(free('free', { start: at(MON, 6, 30), end: at(MON, 16, 30) }))).toEqual([0, 2]);
  });

  it('draws it between 12:00 and 13:00 in the day view', () => {
    const ctx = ctxWith(free('free', { start: at(MON, 12), end: at(MON, 13) }));
    const board = buildBoard(ctx.data, { kind: 'day', day: MON });
    const segment = board.groups.flatMap((group) => group.rows).flatMap((row) => row.segments)[0];

    // Minutes from 06:30: 12:00 is 330 minutes in, for an hour.
    expect([segment.startSlot, segment.span]).toEqual([330, 60]);
  });
});
