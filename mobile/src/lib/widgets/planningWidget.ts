/**
 * The « Planning » widget's content: every person's row of the planning (tasks, absences,
 * holidays), worded for the widget, and who each widget may show (« Afficher » on the iPhone:
 * me, the people checked in Paramètres, a team, a person, everybody). One timeline entry per
 * calendar day, so that the widget turns to the next day at midnight without the app;
 * Saturday and Sunday show Monday, as the planning views do.
 */

import { calendarResolver } from '@/lib/planning/commands';
import { dayInfoFrom, shownDay } from '@/lib/planning/display';
import {
  ABSENCE_LABELS,
  assigneeIds,
  isScheduled,
  taskTypeOf,
  teamsOf,
  type PlanningData,
  type PlanningTask,
  type Resource,
} from '@/lib/planning/models';
import { dateParts, hhmm, parseDate, parseWallTime, todayNumber, weekday } from '@/lib/planning/walltime';
import { AFTERNOON, DAY_END, DAY_START, NOON, weekdaysBetween } from '@/lib/planning/week';
import type { PlanningWidgetProps, WidgetChoice, WidgetChoiceOption, WidgetDay, WidgetTask } from '@/widgets/types';

export const APP_LINK = 'projectflow://';
export const PLANNING_LINK = `${APP_LINK}planning`;
/** Calendar days written ahead: the widget keeps turning for two weeks without the app. */
export const TIMELINE_DAYS = 14;
const MAX_TASKS = 8;

const WEEKDAYS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
const MONTHS = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];

/** « 2026-11-09 » */
export function isoDay(day: number): string {
  const { year, month, date } = dateParts(day);
  return `${year}-${String(month).padStart(2, '0')}-${String(date).padStart(2, '0')}`;
}

/** « Lun 9 nov. » */
export function widgetDayLabel(day: number): string {
  const { month, date } = dateParts(day);
  return `${WEEKDAYS[weekday(day)].slice(0, 3)} ${date} ${MONTHS[month - 1]}`;
}

/** The next working weekday after `day`. */
function nextWorkday(day: number): number {
  let next = day + 1;
  while (weekday(next) > 4) next += 1;
  return next;
}

function kickerFor(day: number, today: number): string {
  if (day === today) return "Aujourd'hui";
  if (day === today + 1) return 'Demain';
  return WEEKDAYS[weekday(day)];
}

/** The planning row of these initials (« lm » finds « LM »). */
export function resourceOf(data: PlanningData, initials: string): Resource | null {
  const wanted = initials.trim().toUpperCase();
  if (!wanted) return null;
  return (
    data.resources.find(
      (resource) => !resource.deleted_at && resource.active !== false && (resource.initials ?? '').trim().toUpperCase() === wanted,
    ) ?? null
  );
}

function partText(part: string | undefined): string {
  if (part === 'morning') return ' (matin)';
  if (part === 'afternoon') return ' (après-midi)';
  return '';
}

/** Why `resource` is away on `day` (« Vacances », « École professionnelle »), else ''. */
function absenceOn(data: PlanningData, resource: Resource, day: number): string {
  for (const absence of data.absences) {
    if (absence.deleted_at || absence.resource_id !== resource.id) continue;
    const first = parseDate(absence.first_day);
    const last = parseDate(absence.last_day);
    if (first === null || last === null || day < first || day > last) continue;
    const label = ABSENCE_LABELS[absence.reason ?? 'absent'] ?? absence.reason ?? 'Absent';
    // The part counts for a single day only, as on the planning.
    return first === last ? `${label}${partText(absence.part)}` : label;
  }
  for (const off of resource.recurring_off ?? []) {
    if (off.weekday !== weekday(day)) continue;
    return `${ABSENCE_LABELS[off.reason ?? 'ecole'] ?? 'Absent'}${partText(off.part)}`;
  }
  return '';
}

/** Minutes of `day` the task holds, from 06:30 to 16:30, or null when it is not on that day. */
function spanOn(task: PlanningTask, day: number): { from: number; to: number; night: boolean } | null {
  const start = parseWallTime(task.start);
  const end = parseWallTime(task.end);
  if (!start || !end) return null;
  if (task.off_hours) {
    // A night belongs to its evening: before 06:00 it is the previous day's night.
    const evening = start.minutes < 6 * 60 ? start.day - 1 : start.day;
    return evening === day ? { from: start.minutes, to: end.minutes, night: true } : null;
  }
  const base = day * 1440;
  const from = Math.max(start.day * 1440 + start.minutes, base + DAY_START) - base;
  const to = Math.min(end.day * 1440 + end.minutes, base + DAY_END) - base;
  return to > from ? { from, to, night: false } : null;
}

const BOUNDS = new Set([DAY_START, NOON, AFTERNOON, DAY_END]);

/** « Matin », « Après-midi », « Journée », « Nuit » or the hours, then « · 2/3 » over several days. */
function whenText(task: PlanningTask, day: number, span: { from: number; to: number; night: boolean }): string {
  let text: string;
  if (span.night) text = 'Nuit';
  else if (!BOUNDS.has(span.from) || !BOUNDS.has(span.to)) text = `${hhmm(span.from)}–${hhmm(span.to)}`;
  else if (span.from < NOON && span.to > AFTERNOON) text = 'Journée';
  else if (span.from < NOON) text = 'Matin';
  else text = 'Après-midi';
  const start = parseWallTime(task.start);
  const end = parseWallTime(task.end);
  if (!span.night && start && end) {
    // The last day holding a worked minute: an end at 06:30 closes the day before.
    const lastDay = end.minutes <= DAY_START ? end.day - 1 : end.day;
    const total = weekdaysBetween(start.day, lastDay).length;
    if (total > 1) text += ` · ${weekdaysBetween(start.day, day).length}/${total}`;
  }
  return text;
}

/** « 4998 » for « 2026-4998 », as on the planning bars. */
function shortNumber(task: PlanningTask): string {
  return (task.project_number ?? '').trim().replace(/^\d{4}-/, '');
}

/** `tasks` of one person on `day`, in the order of the day. */
function dayTasks(meta: PlanningData['meta'], tasks: PlanningTask[], day: number): WidgetTask[] {
  const placed: { task: WidgetTask; from: number }[] = [];
  for (const task of tasks) {
    const span = spanOn(task, day);
    if (!span) continue;
    const kind = taskTypeOf(meta, task.task_type);
    placed.push({
      from: span.night ? 24 * 60 : span.from,
      task: {
        id: task.id,
        number: shortNumber(task),
        // The desktop's rule: the typed title, else the dossier's designation, else the type.
        title: (task.title ?? '').trim() || (task.project_cache?.designation ?? '').trim() || kind.label,
        when: whenText(task, day, span),
        color: kind.color,
        provisional: task.status === 'provisional',
        done: task.status === 'done',
        urgent: Boolean(task.urgent),
        url: `${APP_LINK}tache?id=${encodeURIComponent(task.id)}`,
      },
    });
  }
  placed.sort((a, b) => a.from - b.from || a.task.number.localeCompare(b.task.number));
  return placed.slice(0, MAX_TASKS).map(({ task }) => task);
}

/** The tasks of `resource` on `day`, in the order of the day. */
export function tasksOn(data: PlanningData, resource: Resource, day: number): WidgetTask[] {
  const mine = data.tasks.filter((task) => !task.deleted_at && isScheduled(task) && assigneeIds(task).includes(resource.id));
  return dayTasks(data.meta, mine, day);
}

/** « LM », as the planning labels a row; the name when there are no initials. */
function whoOf(resource: Resource): string {
  return (resource.initials ?? '').trim().toUpperCase() || resource.name;
}

export type PlanningTeam = { key: string; name: string; members: Resource[] };

/** The planning's people, team by team, in the order of the planning's rows. */
export function planningTeams(data: PlanningData): PlanningTeam[] {
  const resources = data.resources.filter((resource) => !resource.deleted_at && resource.active !== false);
  const teams = [...teamsOf(data.meta)].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const keys = teams.map((team) => team.key);
  for (const resource of resources) {
    const team = resource.team || 'atelier';
    if (!keys.includes(team)) keys.push(team);
  }
  return keys
    .map((key) => ({
      key,
      name: teams.find((team) => team.key === key)?.name ?? key,
      members: resources
        .filter((resource) => (resource.team || 'atelier') === key)
        .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase())),
    }))
    .filter((team) => team.members.length > 0);
}

/** What « Moi » and « Ma sélection » mean on this phone (Paramètres). */
export type WidgetPeopleSettings = { initials: string; selection: string[] };

/** Every « Afficher » choice of the widget, by key. */
export function widgetChoices(data: PlanningData, settings: WidgetPeopleSettings): Record<string, WidgetChoice> {
  const teams = planningTeams(data);
  const people = teams.flatMap((team) => team.members);
  const initials = settings.initials.trim().toUpperCase();
  const me = resourceOf(data, initials);
  const choices: Record<string, WidgetChoice> = {
    moi: {
      title: me ? whoOf(me) : initials,
      ids: me ? [me.id] : [],
      empty: initials ? `« ${initials} » n'est pas dans le planning.` : 'Renseignez vos initiales dans Paramètres.',
    },
    selection: {
      title: 'Ma sélection',
      ids: people.filter((person) => settings.selection.includes(person.id)).map((person) => person.id),
      empty: 'Cochez des personnes dans Paramètres, « Widget Planning ».',
    },
    tous: { title: 'Tout le monde', ids: people.map((person) => person.id), empty: 'Personne dans le planning.' },
  };
  for (const team of teams) {
    choices[`equipe:${team.key}`] = { title: team.name, ids: team.members.map((member) => member.id), empty: '' };
  }
  for (const person of people) {
    choices[`personne:${person.id}`] = { title: whoOf(person), ids: [person.id], empty: '' };
  }
  return choices;
}

/** The « Afficher » list of the widget, as the iPhone shows it. */
export function widgetChoiceOptions(data: PlanningData, settings: WidgetPeopleSettings): WidgetChoiceOption[] {
  const teams = planningTeams(data);
  const people = teams.flatMap((team) => team.members);
  const choices = widgetChoices(data, settings);
  const names = (ids: string[]) =>
    ids.map((id) => people.find((person) => person.id === id)).flatMap((person) => (person ? [whoOf(person)] : [])).join(', ');
  const option = (name: string, value: string, subtitle: string): WidgetChoiceOption =>
    subtitle ? { name, value, subtitle } : { name, value };
  return [
    option('Moi', 'moi', choices.moi.ids.length ? choices.moi.title : 'Initiales à renseigner dans Paramètres'),
    option('Ma sélection', 'selection', names(choices.selection.ids) || 'Personnes à cocher dans Paramètres'),
    option('Tout le monde', 'tous', ''),
    ...teams.map((team) => option(team.name, `equipe:${team.key}`, names(team.members.map((member) => member.id)))),
    ...people.map((person) => option(person.name, `personne:${person.id}`, whoOf(person) === person.name ? '' : whoOf(person))),
  ];
}

/** What the timeline needs, read once for all its days. */
type Reading = {
  data: PlanningData;
  people: Resource[];
  tasks: Map<string, PlanningTask[]>;
  /** A holiday or closure that stops a person's whole day; a half-day off stays a normal day. */
  holiday: Map<string, (day: number) => string>;
  choices: Record<string, WidgetChoice>;
};

function reading(data: PlanningData, settings: WidgetPeopleSettings, first: number, last: number): Reading {
  const people = planningTeams(data).flatMap((team) => team.members);
  const tasks = new Map<string, PlanningTask[]>();
  for (const task of data.tasks) {
    if (task.deleted_at || !isScheduled(task)) continue;
    const start = parseWallTime(task.start);
    const end = parseWallTime(task.end);
    // Nights belong to their evening: one day of margin on each side.
    if (!start || !end || end.day < first - 1 || start.day > last + 1) continue;
    for (const id of assigneeIds(task)) {
      const list = tasks.get(id);
      if (list) list.push(task);
      else tasks.set(id, [task]);
    }
  }
  const calendars = calendarResolver(data);
  const holiday = new Map(
    people.map((person) => {
      const info = dayInfoFrom(calendars(person.id), data.meta);
      return [
        person.id,
        (day: number) => {
          const { label, closed } = info(day);
          return label && closed ? label : '';
        },
      ] as const;
    }),
  );
  return { data, people, tasks, holiday, choices: widgetChoices(data, settings) };
}

function widgetDay(read: Reading, day: number, today: number): WidgetDay {
  return {
    kicker: kickerFor(day, today),
    label: widgetDayLabel(day),
    people: read.people.map((person) => ({
      id: person.id,
      who: whoOf(person),
      note: absenceOn(read.data, person, day) || (read.holiday.get(person.id)?.(day) ?? ''),
      tasks: dayTasks(read.data.meta, read.tasks.get(person.id) ?? [], day),
    })),
    url: `${PLANNING_LINK}?jour=${isoDay(day)}`,
  };
}

/** « 14:32 » the same day, « 8 nov. 14:32 » after. */
export function updatedText(loadedAt: Date, today: number): string {
  const day = todayNumber(loadedAt);
  const time = hhmm(loadedAt.getHours() * 60 + loadedAt.getMinutes());
  if (day === today) return time;
  const { month, date } = dateParts(day);
  return `${date} ${MONTHS[month - 1]} ${time}`;
}

/** The widget showing only a message (signed out, planning not read…). */
export function planningMessage(message: string): PlanningWidgetProps {
  return { message, updated: '', url: PLANNING_LINK, choices: {} };
}

function propsFor(read: Reading, today: number, loadedAt: Date): PlanningWidgetProps {
  const shown = shownDay(today);
  return {
    message: '',
    day: widgetDay(read, shown, today),
    next: widgetDay(read, nextWorkday(shown), today),
    updated: updatedText(loadedAt, today),
    url: PLANNING_LINK,
    choices: read.choices,
  };
}

/** The widget's content for the calendar day `today` (the day of the timeline entry). */
export function planningWidgetProps(
  data: PlanningData,
  settings: WidgetPeopleSettings,
  today: number,
  loadedAt: Date,
): PlanningWidgetProps {
  return propsFor(reading(data, settings, today, today + 4), today, loadedAt);
}

/** Local midnight of a day number (the phone's zone: the widget turns at the phone's midnight). */
function midnight(day: number): Date {
  const { year, month, date } = dateParts(day);
  return new Date(year, month - 1, date);
}

/** One entry per calendar day from `now`, each showing its own day. */
export function planningTimeline(
  data: PlanningData,
  settings: WidgetPeopleSettings,
  loadedAt: Date,
  now: Date = new Date(),
): { date: Date; props: PlanningWidgetProps }[] {
  const today = todayNumber(now);
  // The last entry may show the Monday after a weekend, and its next working day.
  const read = reading(data, settings, today, today + TIMELINE_DAYS + 4);
  return Array.from({ length: TIMELINE_DAYS }, (_, index) => ({
    date: index === 0 ? now : midnight(today + index),
    props: propsFor(read, today + index, loadedAt),
  }));
}

/** The dossier numbers of the tasks the timeline may show, for their designations. */
export function timelineNumbers(data: PlanningData, now: Date = new Date()): string[] {
  const today = todayNumber(now);
  const numbers = new Set<string>();
  for (const task of data.tasks) {
    const number = (task.project_number ?? '').trim();
    if (!number || task.deleted_at || !isScheduled(task)) continue;
    const start = parseWallTime(task.start);
    const end = parseWallTime(task.end);
    if (!start || !end || end.day < today - 1 || start.day > today + TIMELINE_DAYS + 5) continue;
    numbers.add(number);
  }
  return [...numbers].sort();
}
