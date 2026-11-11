/**
 * Microsoft Planner, ported from `graph/planner.py`: one task per dossier, titled
 * « 2026-4995 - Désignation », created in the plan of the settings or brought up to date.
 */

import { GraphError, graphRequest, type TokenSource } from '@/lib/graph';

export class PlannerError extends Error {}

export type PlannerBucket = { id: string; name: string };
export type PlannerMember = { id: string; name: string; email: string };

export type PlannerTaskRequest = {
  planId: string;
  bucketId: string;
  /** Chosen in the form; else the settings' default members; else the connected account. */
  assigneeIds: string[];
  defaultAssigneeIds: string[];
  /** Days until the due date; null without a due date. */
  dueDays: number | null;
  number: string;
  designation: string;
};

export type PlannerTaskResult = { taskId: string; created: boolean; updated: boolean };

export interface PlannerGateway {
  buckets(planId: string): Promise<PlannerBucket[]>;
  members(planId: string): Promise<PlannerMember[]>;
  ensureProjectTask(request: PlannerTaskRequest): Promise<PlannerTaskResult>;
}

const PLAN_ID_QUERY_RE = /(?:^|[?&#/])planId=([A-Za-z0-9_-]+)/i;
const PLAN_ID_PATH_RE = /\/(?:plan|PlanViews)\/([A-Za-z0-9_-]+)/i;
const PLAN_ID_JSON_RE = /"planId"\s*:\s*"([A-Za-z0-9_-]+)"/i;

function decode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** The plan id of a Planner/Teams link, or the text itself when it is already an id. */
export function plannerPlanIdFromText(text: string): string {
  const value = text.trim();
  if (!/^https?:\/\//i.test(value)) return value;
  // Teams links wrap the Planner context in an encoded JSON query parameter.
  const decoded = decode(decode(value));
  for (const pattern of [PLAN_ID_QUERY_RE, PLAN_ID_JSON_RE, PLAN_ID_PATH_RE]) {
    const match = pattern.exec(decoded);
    if (match) return match[1];
  }
  throw new PlannerError(
    "Lien Planner non reconnu. Ouvrez le plan dans Planner (navigateur) et copiez l'adresse de la page.",
  );
}

export function plannerTaskTitle(number: string, designation: string): string {
  const text = designation.split(/\s+/).filter(Boolean).join(' ');
  return text ? `${number} - ${text}` : number;
}

/** « 2026-4995 - … » matches 2026-4995, « 2026-4995-2 - … » does not. */
export function taskMatchesProject(title: string, number: string): boolean {
  const normalized = title.trim();
  return normalized === number || normalized.startsWith(`${number} `);
}

export function assignmentsPayload(userIds: readonly string[]): Record<string, { '@odata.type': string; orderHint: string }> {
  return Object.fromEntries(
    userIds.map((id) => [id, { '@odata.type': '#microsoft.graph.plannerAssignment', orderHint: ' !' }]),
  );
}

/** `_due_date`: now plus the days, in UTC, to the second (« 2026-11-14T09:30:00Z »). */
export function plannerDueDate(dueDays: number | null, now: Date = new Date()): string {
  if (dueDays === null || dueDays <= 0) return '';
  const due = new Date(now.getTime() + dueDays * 86_400_000);
  due.setUTCMilliseconds(0);
  return due.toISOString().replace('.000Z', 'Z');
}

export type ExistingTask = { id: string; title: string; bucketId: string; etag: string; assignments: string[]; dueDateTime: string };

/** What `ensure_project_task` changes on an existing task; null when nothing. */
export function taskPatch(
  existing: ExistingTask,
  wanted: { title: string; bucketId: string; assigneeIds: string[]; dueDate: string },
): Record<string, unknown> | null {
  const patch: Record<string, unknown> = {};
  if (existing.title !== wanted.title) patch.title = wanted.title;
  if (existing.bucketId !== wanted.bucketId) patch.bucketId = wanted.bucketId;
  const missing = wanted.assigneeIds.filter((id) => !existing.assignments.includes(id));
  if (missing.length) patch.assignments = assignmentsPayload(missing);
  if (wanted.dueDate && existing.dueDateTime !== wanted.dueDate) patch.dueDateTime = wanted.dueDate;
  return Object.keys(patch).length ? patch : null;
}

/** Who gets the task: the form's members, else the settings' members, else nobody chosen. */
export function chosenAssignees(request: Pick<PlannerTaskRequest, 'assigneeIds' | 'defaultAssigneeIds'>): string[] {
  const chosen = request.assigneeIds.filter((id) => id.trim());
  if (chosen.length) return chosen;
  return request.defaultAssigneeIds.filter((id) => id.trim());
}

/** Group of a plan (`_planner_plan_group_id`). */
export function planGroupId(plan: { container?: { type?: string; containerId?: string }; owner?: string }): string {
  const type = (plan.container?.type ?? '').toLowerCase();
  const id = plan.container?.containerId ?? '';
  if (id && (!type || type === 'group')) return id;
  return plan.owner ?? '';
}

// ------------------------------------------------------------------ Graph ----

type Page<T> = { value?: T[]; '@odata.nextLink'?: string };

async function collect<T>(token: TokenSource, path: string): Promise<T[]> {
  const items: T[] = [];
  let next: string | undefined = path;
  while (next) {
    const page: Page<T> = await graphRequest<Page<T>>(token, next);
    items.push(...(page.value ?? []));
    next = page['@odata.nextLink'];
  }
  return items;
}

function requirePlan(planId: string): string {
  const id = planId.trim();
  if (!id) throw new PlannerError('Plan Planner non configuré. Collez le lien du plan dans les paramètres.');
  return id;
}

export function graphPlanner(token: TokenSource): PlannerGateway {
  let me = '';
  const currentUser = async () => {
    if (me) return me;
    const payload = await graphRequest<{ id?: string }>(token, '/me?$select=id');
    if (!payload.id) throw new PlannerError("Impossible d'identifier l'utilisateur Microsoft connecté.");
    me = payload.id;
    return me;
  };

  return {
    async buckets(planId) {
      const id = requirePlan(planId);
      const raw = await collect<{ id?: string; name?: string }>(token, `/planner/plans/${encodeURIComponent(id)}/buckets`);
      return raw.filter((bucket) => bucket.id && bucket.name).map((bucket) => ({ id: bucket.id!, name: bucket.name! }));
    },

    async members(planId) {
      const id = requirePlan(planId);
      const plan = await graphRequest<{ container?: { type?: string; containerId?: string }; owner?: string }>(
        token,
        `/planner/plans/${encodeURIComponent(id)}?$select=id,title,container,owner`,
      );
      const group = planGroupId(plan);
      if (!group) throw new PlannerError("Impossible d'identifier le groupe Microsoft 365 du plan Planner.");
      const raw = await collect<{ id?: string; displayName?: string; mail?: string; userPrincipalName?: string }>(
        token,
        `/groups/${encodeURIComponent(group)}/members/microsoft.graph.user?$select=id,displayName,mail,userPrincipalName`,
      );
      const members = raw
        .filter((member) => member.id)
        .map((member) => ({
          id: member.id!,
          name: member.displayName ?? '',
          email: member.mail ?? member.userPrincipalName ?? '',
        }));
      if (members.length && members.every((member) => !member.name && !member.email)) {
        throw new PlannerError(
          "Microsoft Graph ne renvoie que les identifiants des membres Planner. Ajoutez l'autorisation déléguée " +
            'User.ReadBasic.All à ProjectFlow, accordez le consentement administrateur, puis reconnectez le compte Microsoft.',
        );
      }
      return members;
    },

    async ensureProjectTask(request) {
      const planId = requirePlan(request.planId);
      const bucketId = request.bucketId.trim();
      if (!bucketId) throw new PlannerError('Planner actif mais colonne (bucket) non configurée.');
      let assignees = chosenAssignees(request);
      if (!assignees.length) assignees = [await currentUser()];
      const dueDate = plannerDueDate(request.dueDays);
      const title = plannerTaskTitle(request.number, request.designation);

      const raw = await collect<{
        id?: string;
        title?: string;
        bucketId?: string;
        '@odata.etag'?: string;
        assignments?: Record<string, unknown>;
        dueDateTime?: string;
      }>(token, `/planner/plans/${encodeURIComponent(planId)}/tasks`);
      const existing = raw
        .filter((task) => task.id && task.title && task['@odata.etag'])
        .map<ExistingTask>((task) => ({
          id: task.id!,
          title: task.title!,
          bucketId: task.bucketId ?? '',
          etag: task['@odata.etag']!,
          assignments: Object.entries(task.assignments ?? {})
            .filter(([, value]) => Boolean(value))
            .map(([key]) => key),
          dueDateTime: task.dueDateTime ?? '',
        }))
        .find((task) => taskMatchesProject(task.title, request.number));

      if (!existing) {
        const body: Record<string, unknown> = { planId, bucketId, title, assignments: assignmentsPayload(assignees) };
        if (dueDate) body.dueDateTime = dueDate;
        const created = await graphRequest<{ id?: string }>(token, '/planner/tasks', { method: 'POST', body });
        if (!created?.id) throw new PlannerError("Planner n'a pas retourné l'identifiant de la tâche créée.");
        return { taskId: created.id, created: true, updated: false };
      }
      const patch = taskPatch(existing, { title, bucketId, assigneeIds: assignees, dueDate });
      if (!patch) return { taskId: existing.id, created: false, updated: false };
      try {
        await graphRequest(token, `/planner/tasks/${encodeURIComponent(existing.id)}`, {
          method: 'PATCH',
          body: patch,
          headers: { 'If-Match': existing.etag },
        });
      } catch (exc) {
        if (exc instanceof GraphError && exc.status === 412) {
          throw new PlannerError('La tâche Planner a été modifiée entre-temps. Recommencez.');
        }
        throw exc;
      }
      return { taskId: existing.id, created: false, updated: true };
    },
  };
}
