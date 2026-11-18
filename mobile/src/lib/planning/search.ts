import { instantDay, parseInstant } from '@/lib/planning/instant';
import { STATUS_LABELS, taskTypeOf, type PlanningData, type PlanningTask, type TaskStatus } from '@/lib/planning/models';
import { dateParts, isoWeek, parseDate } from '@/lib/planning/walltime';

/**
 * Looking for a task: words typed as they come (a dossier number, a client, a person's
 * initials, a date) narrowed by filters. Every word must be found somewhere in the task;
 * every filter in use must agree.
 */
export type SearchPeriod = 'all' | 'upcoming' | 'past' | 'backlog';

export type TaskSearch = {
  text: string;
  period: SearchPeriod;
  /** Task type keys; none: every type. */
  types: string[];
  /** None: every status. */
  statuses: TaskStatus[];
  /** Resource ids; none: anyone. A task is kept when one of them is on it. */
  people: string[];
  urgent: boolean;
  night: boolean;
};

export const EMPTY_SEARCH: TaskSearch = { text: '', period: 'all', types: [], statuses: [], people: [], urgent: false, night: false };

/** How many filters are in use, the words apart. */
export function filterCount(search: TaskSearch): number {
  return (
    (search.period === 'all' ? 0 : 1) +
    search.types.length +
    search.statuses.length +
    search.people.length +
    (search.urgent ? 1 : 0) +
    (search.night ? 1 : 0)
  );
}

/** Lower case without accents: « Égalité » is found by « egalite ». */
export function folded(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** A day as it may be typed: « 16.11 », « 16.11.2026 », « s47 ». */
function dayWords(day: number): string[] {
  const { year, month, date } = dateParts(day);
  return [`${pad(date)}.${pad(month)}`, `${date}.${month}`, `${pad(date)}.${pad(month)}.${year}`, `s${isoWeek(day)}`];
}

/** Everything a task can be found by, in one folded text. */
export function searchText(data: PlanningData, task: PlanningTask): string {
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  const deadline = task.deadline ? parseDate(task.deadline) : null;
  const people = Object.keys(task.assignments ?? {}).flatMap((id) => {
    const person = data.resources.find((resource) => resource.id === id);
    return person ? [person.initials ?? '', person.name] : [];
  });
  return folded(
    [
      task.project_number,
      task.title,
      task.project_cache?.designation,
      task.project_cache?.client,
      task.project_cache?.contact,
      task.project_cache?.location,
      task.project_cache?.managed_by,
      task.notes,
      taskTypeOf(data.meta, task.task_type).label,
      STATUS_LABELS[task.status ?? 'confirmed'],
      ...people,
      ...(start !== null ? dayWords(instantDay(start)) : ['a planifier']),
      ...(end !== null ? dayWords(instantDay(end)) : []),
      ...(deadline !== null ? dayWords(deadline) : []),
      task.urgent ? 'urgent' : '',
      task.off_hours ? 'nuit' : '',
    ]
      .filter(Boolean)
      .join(' \n '),
  );
}

function matches(data: PlanningData, task: PlanningTask, search: TaskSearch, words: string[], today: number): boolean {
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  const scheduled = start !== null && end !== null;
  if (search.period === 'backlog' && scheduled) return false;
  if (search.period === 'upcoming' && !(scheduled && instantDay(end) >= today)) return false;
  if (search.period === 'past' && !(scheduled && instantDay(end) < today)) return false;
  if (search.types.length && !search.types.includes(taskTypeOf(data.meta, task.task_type).key)) return false;
  if (search.statuses.length && !search.statuses.includes(task.status ?? 'confirmed')) return false;
  if (search.people.length && !search.people.some((id) => id in (task.assignments ?? {}))) return false;
  if (search.urgent && !task.urgent) return false;
  if (search.night && !task.off_hours) return false;
  if (!words.length) return true;
  const text = searchText(data, task);
  return words.every((word) => text.includes(word));
}

/** Where a task stands today: running or coming, waiting to be planned, or over. */
export function taskPeriod(task: PlanningTask, today: number): Exclude<SearchPeriod, 'all'> {
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  if (start === null || end === null) return 'backlog';
  return instantDay(end) >= today ? 'upcoming' : 'past';
}

/**
 * The tasks found, in the order they are of use: what is running or coming first, soonest
 * first; then what waits to be planned; then the past, most recent first.
 */
export function searchTasks(data: PlanningData, search: TaskSearch, today: number): PlanningTask[] {
  const words = folded(search.text).split(/\s+/).filter(Boolean);
  const rank = (task: PlanningTask): [number, number] => {
    const start = parseInstant(task.start ?? '');
    const end = parseInstant(task.end ?? '');
    if (start === null || end === null) return [1, 0];
    return instantDay(end) >= today ? [0, start] : [2, -start];
  };
  return data.tasks
    .filter((task) => !task.deleted_at && matches(data, task, search, words, today))
    .map((task) => ({ task, rank: rank(task) }))
    .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1])
    .map((item) => item.task);
}
