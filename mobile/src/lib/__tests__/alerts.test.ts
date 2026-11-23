import {
  alertBody,
  concerns,
  dateOf,
  instantOf,
  leadText,
  MAX_PLANNED,
  momentText,
  myResourceId,
  orderedReminders,
  plannedNotifications,
  reminderDue,
  reminderKey,
  reminderText,
  toggledReminder,
} from '@/lib/planning/alerts';
import { contextFor, createTask, PlanningCommandError, setReminders } from '@/lib/planning/commands';
import { instant, MINUTES_PER_DAY } from '@/lib/planning/instant';
import type { PlanningData, PlanningTask, Reminder, Resource } from '@/lib/planning/models';
import { dayNumber } from '@/lib/planning/walltime';
import { cleanSettings, DEFAULT_SETTINGS, parseSettings } from '@/lib/settings';

const MON = dayNumber(2026, 10, 26);
const TUE = MON + 1;
const AUTHOR = { initials: 'LM', name: '', poste: 'iPhone', poste_id: 'p' };
const ANNA: Resource = { id: 'anna', name: 'Anna Favre', initials: 'AF' };
const BRUNO: Resource = { id: 'bruno', name: 'Bruno Rossier', initials: 'BR' };
const HALF_HOUR: Reminder = { kind: 'before', minutes: 30, at: null };
const AT_SIX: Reminder = { kind: 'at', minutes: 0, at: '2026-10-26T06:00' };

function visit(id = 'visit', fields: Partial<PlanningTask> = {}): PlanningTask {
  return {
    id,
    title: 'Visite chantier',
    project_number: '2026-118',
    task_type: 'montage',
    start: '2026-10-26T08:00',
    end: '2026-10-26T10:00',
    assignments: { anna: {} },
    reminders: [HALF_HOUR],
    ...fields,
  };
}

function planning(...tasks: PlanningTask[]): PlanningData {
  return { tasks, resources: [ANNA, BRUNO], absences: [], meta: null };
}

describe('alert texts', () => {
  test('read as on the desktop', () => {
    expect(leadText(5)).toBe('5 min');
    expect(leadText(60)).toBe('1 h');
    expect(leadText(90)).toBe('1 h 30');
    expect(leadText(2 * MINUTES_PER_DAY)).toBe('2 j');
    expect(reminderText({ kind: 'before', minutes: 0 })).toBe('Au début');
    expect(reminderText(HALF_HOUR)).toBe('30 min avant');
    expect(reminderText(AT_SIX)).toBe('lun 26.10 à 06:00');
    expect(momentText(instant(MON, 6), MON)).toBe("aujourd'hui à 06:00");
    expect(momentText(instant(TUE, 7, 30), MON)).toBe('demain à 07:30');
  });

  test('an alert has the key of the desktop', () => {
    expect(reminderKey(HALF_HOUR)).toBe('b30');
    expect(reminderKey(AT_SIX)).toBe('a2026-10-26T06:00');
    expect(reminderKey({})).toBe('b0');
  });

  test('the body tells the start and who does the task', () => {
    const data = planning(visit());
    expect(alertBody(data.tasks[0], HALF_HOUR, data, MON)).toBe("Commence dans 30 min · aujourd'hui à 08:00 · AF");
    expect(alertBody(data.tasks[0], AT_SIX, data, MON)).toBe("Rappel · début aujourd'hui à 08:00 · AF");
    expect(alertBody(visit('card', { start: null, end: null, assignments: {} }), AT_SIX, data)).toBe('Rappel · tâche à planifier');
  });
});

describe('the alerts of a task', () => {
  test('are ordered as the desktop stores them, doubles and unreadable ones dropped', () => {
    const dayBefore: Reminder = { kind: 'before', minutes: MINUTES_PER_DAY };
    expect(orderedReminders([AT_SIX, HALF_HOUR, { kind: 'before', minutes: 30 }, dayBefore, { kind: 'at', at: 'tôt' }])).toEqual([
      { kind: 'before', minutes: MINUTES_PER_DAY, at: null },
      HALF_HOUR,
      AT_SIX,
    ]);
  });

  test('toggle on and off', () => {
    expect(toggledReminder([], HALF_HOUR)).toEqual([HALF_HOUR]);
    expect(toggledReminder([HALF_HOUR, AT_SIX], { kind: 'before', minutes: 30 })).toEqual([AT_SIX]);
  });

  test('ring before the start, which they follow, or at their moment', () => {
    expect(reminderDue(visit(), HALF_HOUR)).toBe(instant(MON, 7, 30));
    expect(reminderDue(visit('moved', { start: '2026-10-27T08:00', end: '2026-10-27T10:00' }), HALF_HOUR)).toBe(instant(TUE, 7, 30));
    expect(reminderDue(visit(), AT_SIX)).toBe(instant(MON, 6));
    expect(reminderDue(visit('card', { start: null, end: null }), HALF_HOUR)).toBeNull();
  });

  test('are saved by a command that keeps what the phone does not know', () => {
    const task = { ...visit('visit', { reminders: undefined }), later_field: 'kept' } as PlanningTask;
    const ctx = contextFor(planning(task), AUTHOR);

    expect(setReminders(ctx, 'visit', [])).toEqual([]);
    const [change] = setReminders(ctx, 'visit', [AT_SIX, HALF_HOUR]);
    expect(change.after?.reminders).toEqual([HALF_HOUR, AT_SIX]);
    expect(change.after?.later_field).toBe('kept');
    const tooMany = Array.from({ length: 7 }, (_, minutes) => ({ kind: 'before' as const, minutes }));
    expect(() => setReminders(ctx, 'visit', tooMany)).toThrow(PlanningCommandError);
  });

  test('are given to a new task', () => {
    const ctx = contextFor(planning(), AUTHOR);
    const [change] = createTask(ctx, { title: 'Livraison', taskType: 'livraison', start: instant(MON, 7), reminders: [HALF_HOUR] });
    expect(change.after?.reminders).toEqual([HALF_HOUR]);
  });
});

describe('whom a phone rings for', () => {
  test('the tasks of its person, or of who created a task nobody does', () => {
    const data = planning(visit());
    expect(myResourceId(data, 'af')).toBe('anna');
    expect(myResourceId(data, 'ZZ')).toBe('');
    expect(concerns(data.tasks[0], 'anna', 'AF')).toBe(true);
    expect(concerns(data.tasks[0], 'bruno', 'BR')).toBe(false);
    expect(concerns(data.tasks[0], '', '')).toBe(true);
    const nobody = visit('free', { assignments: {}, created_by: { initials: 'LM' } });
    expect(concerns(nobody, '', 'lm')).toBe(true);
    expect(concerns(nobody, 'anna', 'AF')).toBe(false);
  });
});

describe('the alerts handed to the phone', () => {
  const now = instant(MON, 6, 30);

  test('are those still to come, the nearest first, written for the day they ring', () => {
    const data = planning(
      visit('later', { start: '2026-10-27T08:00', end: '2026-10-27T10:00' }),
      visit('soon', { reminders: [HALF_HOUR, AT_SIX] }),
    );

    const planned = plannedNotifications(data, { now, initials: 'AF' });

    expect(planned.map((alert) => [alert.taskId, alert.due])).toEqual([
      ['soon', instant(MON, 7, 30)],
      ['later', instant(TUE, 7, 30)],
    ]);
    expect(planned[0].id).toBe(`soon|b30|${instant(MON, 7, 30)}`);
    expect(planned[0].title).toBe('2026-118 - Visite chantier');
    expect(planned[1].body).toBe("Commence dans 30 min · aujourd'hui à 08:00 · AF");
  });

  test('leave out the others, the finished, the deleted and the far away', () => {
    const data = planning(
      visit('theirs', { assignments: { bruno: {} } }),
      visit('done', { status: 'done' }),
      visit('gone', { deleted_at: '2026-10-20T08:00:00Z' }),
      visit('far', { start: '2027-06-01T08:00', end: '2027-06-01T10:00' }),
    );

    expect(plannedNotifications(data, { now, initials: 'AF' })).toEqual([]);
    expect(plannedNotifications(data, { now, initials: 'AF', audience: 'all' }).map((alert) => alert.taskId)).toEqual(['theirs']);
    expect(plannedNotifications(data, { now, initials: '' }).map((alert) => alert.taskId)).toEqual(['theirs']);
  });

  test('stay under what iOS keeps for one app', () => {
    const tasks = Array.from({ length: 80 }, (_, index) => {
      const hour = String(Math.floor(index / 2) % 24).padStart(2, '0');
      const at = `2026-10-${index < 48 ? 27 : 28}T${hour}:${index % 2 ? '30' : '00'}`;
      return visit(`t${index}`, { reminders: [{ kind: 'at', at }] });
    });
    expect(plannedNotifications(planning(...tasks), { now, initials: 'AF' })).toHaveLength(MAX_PLANNED);
  });

  test('ring at the wall clock of the planning', () => {
    const date = dateOf(instant(MON, 7, 30));
    expect([date.getFullYear(), date.getMonth() + 1, date.getDate(), date.getHours(), date.getMinutes()]).toEqual([2026, 10, 26, 7, 30]);
    expect(instantOf(date)).toBe(instant(MON, 7, 30));
  });
});

describe('the alert settings of the phone', () => {
  test('are on for a phone set up before the alerts existed', () => {
    expect(DEFAULT_SETTINGS.alerts).toBe(true);
    const old = parseSettings(JSON.stringify({ initials: 'LM' }));
    expect(old.alerts).toBe(true);
    expect(old.alertsAudience).toBe('mine');
    const off = parseSettings(JSON.stringify({ alerts: false, alertsAudience: 'all' }));
    expect(off.alerts).toBe(false);
    expect(cleanSettings(off).alertsAudience).toBe('all');
  });
});
