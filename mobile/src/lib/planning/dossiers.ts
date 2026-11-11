/**
 * A task's dossier as the desktop planning reads it from the répertoire (`ui/planning_controller.py`):
 * the details copied into a task when its dossier is set (`_project_cache`), the details
 * shown for a task that has none of its own (`_fill_task_projects`), the dossier of a short
 * number (`ui/planning/detail.py` `_resolve_project`).
 */

import { utcNowText } from '@/lib/planning/documents';
import type { PlanningData, PlanningTask } from '@/lib/planning/models';
import type { ProjectInfo } from '@/lib/planning/quickEntry';

export type DossierCache = NonNullable<PlanningTask['project_cache']> & { read_at?: string };

/** The répertoire details copied once into a task: the planning's shared display copy. */
export function dossierCache(project: ProjectInfo, now: Date = new Date()): DossierCache {
  return {
    client: project.client ?? '',
    contact: project.contact ?? '',
    designation: project.designation ?? '',
    managed_by: project.managedBy ?? '',
    read_at: utcNowText(now),
  };
}

/**
 * The tasks as shown: a task whose copy lacks the designation or the client takes them from
 * the répertoire. Only the display changes; the shared planning keeps the tasks as they are.
 */
export function withDossiers(data: PlanningData, projects: ReadonlyMap<string, ProjectInfo>): PlanningData {
  let changed = false;
  const tasks = data.tasks.map((task) => {
    const project = task.project_number ? projects.get(task.project_number.trim()) : undefined;
    const cache = task.project_cache ?? {};
    if (!project || (cache.designation && cache.client)) return task;
    changed = true;
    return {
      ...task,
      project_cache: {
        ...cache,
        designation: cache.designation || project.designation || '',
        client: cache.client || project.client || '',
        contact: cache.contact || project.contact || '',
      },
    };
  });
  return changed ? { ...data, tasks } : data;
}

/** The dossier typed: « 5231 » is the dossier « …-5231 » when the répertoire has exactly one. */
export function resolveDossierNumber(typed: string, projects: ReadonlyMap<string, ProjectInfo>): string {
  const value = typed.trim();
  if (!/^\d+$/.test(value)) return value.toUpperCase();
  const matches = [...projects.keys()].filter((number) => number.endsWith(`-${value}`));
  return matches.length === 1 ? matches[0] : value;
}

/** The designation of a task's dossier: the répertoire's, else the copy in the task. */
export function dossierDesignation(task: PlanningTask, projects: ReadonlyMap<string, ProjectInfo>): string {
  const project = task.project_number ? projects.get(task.project_number.trim()) : undefined;
  return (project?.designation || task.project_cache?.designation || '').trim();
}
