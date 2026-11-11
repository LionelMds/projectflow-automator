/**
 * Planning documents as the desktop stores them, one JSON file each
 * (`core/planning/models.py`). Only the fields the mobile screens read are typed;
 * unknown fields are kept untouched by never rewriting a document here.
 */

export type DocumentKind = 'task' | 'resource' | 'absence' | 'meta';
export const DOCUMENT_KINDS: readonly DocumentKind[] = ['task', 'resource', 'absence', 'meta'];

export type Author = { initials?: string; name?: string; poste?: string; poste_id?: string };

type Envelope = {
  id: string;
  schema_version?: number;
  created_at?: string;
  created_by?: Author;
  updated_at?: string;
  updated_by?: Author;
  deleted_at?: string | null;
  deleted_by?: Author | null;
};

export type TaskStatus = 'provisional' | 'confirmed' | 'done';
export type DayPart = 'day' | 'morning' | 'afternoon';

export type PlanningTask = Envelope & {
  status?: TaskStatus;
  task_type?: string;
  title?: string;
  project_number?: string;
  project_cache?: {
    client?: string;
    contact?: string;
    designation?: string;
    managed_by?: string;
    location?: string;
  };
  /** Wall-clock text without zone, « 2026-10-26T06:30 ». */
  start?: string | null;
  end?: string | null;
  estimate_minutes?: number;
  /** Estimate typed in working days (« 3 j »), to the half day. */
  estimate_days?: number;
  required_people?: number;
  assignments?: Record<string, Record<string, unknown>>;
  /** « 2026-11-20 » */
  deadline?: string | null;
  urgent?: boolean;
  notes?: string;
  backlog_rank?: string;
  origin?: string;
  planner_task_id?: string;
  project_fingerprint?: string;
  off_hours?: boolean;
};

export type RecurringOff = { weekday: number; part?: DayPart; reason?: string };

export type Resource = Envelope & {
  kind_of_resource?: 'person' | 'subcontractor';
  name: string;
  initials?: string;
  team?: string;
  order?: number;
  active?: boolean;
  apprentice?: boolean;
  canton?: string;
  role?: string;
  recurring_off?: RecurringOff[];
};

export type Absence = Envelope & {
  resource_id: string;
  reason?: string;
  first_day: string;
  last_day: string;
  part?: DayPart;
  note?: string;
};

export type Team = { key: string; name: string; alias?: string; order?: number };
export type TaskType = { key: string; label: string; color: string; color_dark?: string; keywords?: string[]; team?: string };

export type PlanningMeta = Envelope & {
  teams?: Team[];
  task_types?: TaskType[];
  closures?: { first_day: string; last_day: string; label?: string }[];
  /** Worked periods per weekday (« 0 » = Monday), minutes since midnight. */
  work_week?: Record<string, [number, number][]>;
  default_canton?: string;
  auto_card_on_sortie?: boolean;
};

export const ABSENCE_LABELS: Record<string, string> = {
  vacances: 'Vacances',
  absent: 'Absent',
  ecole: 'École professionnelle',
  cie: 'Cours CIE',
  militaire: 'Militaire / PC',
  formation: 'Formation',
  recuperation: 'Récupération',
  conge: 'Congé',
};

export const STATUS_LABELS: Record<TaskStatus, string> = {
  provisional: 'Provisoire',
  confirmed: 'Confirmé',
  done: 'Terminé',
};

export const DEFAULT_TEAMS: Team[] = [
  { key: 'bureau', name: 'Bureau technique', order: 0 },
  { key: 'atelier', name: 'Atelier', order: 1 },
  { key: 'montage', name: 'Montage', order: 2 },
  { key: 'soustraitants', name: 'Sous-traitants', order: 3 },
];

export const DEFAULT_TASK_TYPES: TaskType[] = [
  { key: 'releve', label: 'Relevé de cotes', color: '#009494', team: 'bureau' },
  { key: 'dessin', label: 'Dessin / plans', color: '#8572bb', team: 'bureau' },
  { key: 'debit', label: 'Débit', color: '#908126', team: 'atelier' },
  { key: 'soudure', label: 'Soudure / assemblage', color: '#b7684c', team: 'atelier' },
  { key: 'finition', label: 'Finition / contrôle', color: '#aa732b', team: 'atelier' },
  { key: 'montage', label: 'Montage chantier', color: '#4a925c', team: 'montage' },
  { key: 'livraison', label: 'Livraison', color: '#b16389', team: 'montage' },
  { key: 'sav', label: 'SAV / réparation', color: '#4086bd', team: 'montage' },
  { key: 'soustraitance', label: 'Sous-traitance', color: '#787a7e', team: 'soustraitants' },
  { key: 'interne', label: 'Interne', color: '#96989c' },
];
const DEFAULT_TASK_TYPE = 'soudure';

export type PlanningData = {
  tasks: PlanningTask[];
  resources: Resource[];
  absences: Absence[];
  meta: PlanningMeta | null;
};

export function teamsOf(meta: PlanningMeta | null): Team[] {
  return meta?.teams?.length ? meta.teams : DEFAULT_TEAMS;
}

export function taskTypesOf(meta: PlanningMeta | null): TaskType[] {
  return meta?.task_types?.length ? meta.task_types : DEFAULT_TASK_TYPES;
}

/** The configured type, else the default type of the shared list (`PlanningMeta.task_type`). */
export function taskTypeOf(meta: PlanningMeta | null, key: string | undefined): TaskType {
  const kinds = taskTypesOf(meta);
  const fallback = kinds.some((kind) => kind.key === DEFAULT_TASK_TYPE) ? DEFAULT_TASK_TYPE : kinds[0].key;
  return kinds.find((kind) => kind.key === key) ?? kinds.find((kind) => kind.key === fallback) ?? kinds[0];
}

/** « 2026-5271 - Local grillagé », the name of a task everywhere (`task_label`). */
export function taskLabel(task: PlanningTask): string {
  const number = (task.project_number ?? '').trim();
  const designation = ((task.title ?? '').trim() || (task.project_cache?.designation ?? '').trim());
  if (number && designation) return `${number} - ${designation}`;
  return number || designation;
}

export function isScheduled(task: PlanningTask): boolean {
  return Boolean(task.start && task.end);
}

export function assigneeIds(task: PlanningTask): string[] {
  return Object.keys(task.assignments ?? {});
}
