// Une seule tâche Planner par numéro de projet (repris de graph/planner.py).
import type { PlannerGateway, PlannerTask } from './gateways';

export interface PlannerRequest {
  planId: string;
  bucketId: string;
  number: string;
  designation: string;
  assigneeIds: string[];
  dueDays: number | null;
}

export interface PlannerResult {
  task: PlannerTask | { id: string };
  created: boolean;
  updated: boolean;
}

export function taskTitle(number: string, designation: string): string {
  const d = designation.split(/\s+/).filter(Boolean).join(' ');
  return d ? `${number} - ${d}` : number;
}

export function taskMatches(title: string, number: string): boolean {
  const t = title.trim();
  return t === number || t.startsWith(number + ' ');
}

export function dueDate(days: number | null, now = new Date()): string {
  if (!days || days <= 0) return '';
  const due = new Date(now.getTime() + days * 86_400_000);
  due.setMilliseconds(0);
  return due.toISOString().replace('.000Z', 'Z');
}

export async function ensureProjectTask(gw: PlannerGateway, req: PlannerRequest, fallbackAssignee: () => Promise<string>): Promise<PlannerResult> {
  if (!req.planId || !req.bucketId) throw new Error('Planner actif mais plan ou colonne non configuré.');
  const assignees = req.assigneeIds.filter((a) => a.trim());
  if (!assignees.length) assignees.push(await fallbackAssignee());
  const title = taskTitle(req.number, req.designation);
  const due = dueDate(req.dueDays);
  const existing = (await gw.listTasks(req.planId)).find((t) => taskMatches(t.title, req.number));
  if (!existing) {
    const task = await gw.createTask({ planId: req.planId, bucketId: req.bucketId, title, assignments: assignees, dueDateTime: due });
    return { task, created: true, updated: false };
  }
  const patch: Parameters<PlannerGateway['patchTask']>[1] = {};
  if (existing.title !== title) patch.title = title;
  if (existing.bucketId !== req.bucketId) patch.bucketId = req.bucketId;
  const missing = assignees.filter((a) => !existing.assignments.includes(a));
  if (missing.length) patch.addAssignments = missing;
  if (due && existing.dueDateTime !== due) patch.dueDateTime = due;
  if (Object.keys(patch).length) {
    await gw.patchTask(existing, patch);
    return { task: existing, created: false, updated: true };
  }
  return { task: existing, created: false, updated: false };
}

export async function deleteProjectTasks(gw: PlannerGateway, planId: string, number: string): Promise<number> {
  const matches = (await gw.listTasks(planId)).filter((t) => taskMatches(t.title, number));
  for (const t of matches) await gw.deleteTask(t);
  return matches.length;
}
