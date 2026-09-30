// Microsoft Planner via Graph (repris de graph/planner.py).
import type { PlannerGateway, PlannerTask } from '../core/gateways';
import { graph, graphAll, seg } from './client';

const P = { scopes: ['base', 'planner'] as const };
const opts = (extra: object = {}) => ({ scopes: [...P.scopes], ...extra });

interface RawTask {
  id?: string;
  title?: string;
  bucketId?: string;
  '@odata.etag'?: string;
  assignments?: Record<string, unknown>;
  dueDateTime?: string;
}

const toTask = (r: RawTask): PlannerTask => ({
  id: r.id ?? '',
  title: r.title ?? '',
  bucketId: r.bucketId ?? '',
  etag: r['@odata.etag'] ?? '*',
  assignments: Object.entries(r.assignments ?? {}).filter(([, v]) => v).map(([k]) => k),
  dueDateTime: r.dueDateTime ?? '',
});

const assignmentsPayload = (ids: string[]) =>
  Object.fromEntries(ids.map((id) => [id, { '@odata.type': '#microsoft.graph.plannerAssignment', orderHint: ' !' }]));

export const graphPlanner: PlannerGateway = {
  async listPlans() {
    const raw = await graphAll<{ id?: string; title?: string }>('/me/planner/plans', opts());
    return raw.filter((p) => p.id && p.title).map((p) => ({ id: p.id!, title: p.title! }));
  },

  async listBuckets(planId) {
    const raw = await graphAll<{ id?: string; name?: string; orderHint?: string }>(`/planner/plans/${seg(planId)}/buckets`, opts());
    return raw
      .filter((b) => b.id && b.name)
      .sort((a, b) => (a.orderHint ?? '').localeCompare(b.orderHint ?? ''))
      .map((b) => ({ id: b.id!, name: b.name! }));
  },

  async listMembers(planId) {
    const plan = await graph<{ owner?: string; container?: { containerId?: string; type?: string } }>('GET', `/planner/plans/${seg(planId)}?$select=id,title,container,owner`, opts());
    const c = plan.container;
    const groupId = c?.containerId && (!c.type || c.type.toLowerCase() === 'group') ? c.containerId : plan.owner;
    if (!groupId) throw new Error('Impossible d’identifier le groupe Microsoft 365 du plan Planner.');
    const raw = await graphAll<{ id?: string; displayName?: string; mail?: string; userPrincipalName?: string }>(
      `/groups/${seg(groupId)}/members/microsoft.graph.user?$select=id,displayName,mail,userPrincipalName`,
      opts(),
    );
    const members = raw.filter((m) => m.id).map((m) => ({ id: m.id!, displayName: m.displayName ?? '', email: m.mail || m.userPrincipalName || '' }));
    if (members.length && members.every((m) => !m.displayName && !m.email)) {
      throw new Error('Microsoft Graph ne renvoie que les identifiants des membres Planner. Ajoutez l’autorisation User.ReadBasic.All à ProjectFlow, faites accorder le consentement administrateur, puis reconnectez-vous.');
    }
    return members.sort((a, b) => a.displayName.localeCompare(b.displayName));
  },

  async listTasks(planId) {
    return (await graphAll<RawTask>(`/planner/plans/${seg(planId)}/tasks`, opts())).map(toTask).filter((t) => t.id && t.title);
  },

  async createTask(t) {
    const body: Record<string, unknown> = { planId: t.planId, bucketId: t.bucketId, title: t.title, assignments: assignmentsPayload(t.assignments) };
    if (t.dueDateTime) body.dueDateTime = t.dueDateTime;
    return toTask(await graph<RawTask>('POST', '/planner/tasks', opts({ body })));
  },

  async patchTask(task, patch) {
    const body: Record<string, unknown> = {};
    if (patch.title) body.title = patch.title;
    if (patch.bucketId) body.bucketId = patch.bucketId;
    if (patch.addAssignments?.length) body.assignments = assignmentsPayload(patch.addAssignments);
    if (patch.dueDateTime) body.dueDateTime = patch.dueDateTime;
    await graph('PATCH', `/planner/tasks/${seg(task.id)}`, opts({ body, headers: { 'If-Match': task.etag } }));
  },

  async deleteTask(task) {
    await graph('DELETE', `/planner/tasks/${seg(task.id)}`, opts({ headers: { 'If-Match': task.etag } }));
  },

  taskUrl(task, planId) {
    return `https://planner.cloud.microsoft/webui/plan/${encodeURIComponent(planId)}/view/board/task/${encodeURIComponent(task.id)}`;
  },
};
