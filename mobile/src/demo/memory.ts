// Passerelles en mémoire : mode démo (sans compte Microsoft) et tests automatisés.
import type {
  DriveGateway,
  DriveItem,
  Gateways,
  MailFolder,
  MailGateway,
  PlannerGateway,
  PlannerMember,
  PlannerTask,
  UsedRange,
  Workbook,
  WorkbookGateway,
} from '../core/gateways';
import { cellText } from '../core/text';

interface Node {
  id: string;
  name: string;
  parentId: string | null;
  isFolder: boolean;
  size: number;
  modified: string;
  sheets?: Map<string, unknown[][]>;
  deleted?: boolean;
}

export interface MemoryOptions {
  latencyMs?: number;
}

const DRIVE = 'demo-drive';
let seq = 0;
const newId = () => `n${++seq}`;

export class MemoryStore {
  nodes = new Map<string, Node>();
  sentMail: { to: { name: string; email: string }[]; subject: string; html: string }[] = [];
  mailFolders: (MailFolder & { parentId: string | null })[] = [];
  plans = [{ id: 'plan-atelier', title: 'Projets atelier' }];
  buckets = [
    { id: 'b-todo', name: 'À faire' },
    { id: 'b-etude', name: 'Étude' },
    { id: 'b-atelier', name: 'Atelier' },
  ];
  members: PlannerMember[] = [];
  tasks: PlannerTask[] = [];
  me = { id: 'u-lm', displayName: 'Lionel Martin', email: 'lionel.martin@balzmetal.ch' };
  rootId: string;

  constructor(public latencyMs = 0) {
    this.rootId = this.add(null, 'OneDrive', true).id;
  }

  add(parentId: string | null, name: string, isFolder: boolean, size = 0, modified = new Date().toISOString()): Node {
    const node: Node = { id: newId(), name, parentId, isFolder, size, modified };
    this.nodes.set(node.id, node);
    return node;
  }

  folder(path: string): Node {
    let current = this.nodes.get(this.rootId)!;
    for (const part of path.split('/').filter(Boolean)) {
      current = this.children(current.id).find((c) => c.isFolder && c.name === part) ?? this.add(current.id, part, true);
    }
    return current;
  }

  file(path: string, size = 48_000, modified?: string, sheets?: Record<string, unknown[][]>): Node {
    const parts = path.split('/');
    const name = parts.pop()!;
    const parent = this.folder(parts.join('/'));
    const node = this.add(parent.id, name, false, size, modified);
    if (sheets) node.sheets = new Map(Object.entries(sheets).map(([k, v]) => [k, v.map((r) => [...r])]));
    return node;
  }

  children(id: string): Node[] {
    return [...this.nodes.values()].filter((n) => n.parentId === id && !n.deleted);
  }

  byPath(path: string): Node | null {
    let current: Node | undefined = this.nodes.get(this.rootId);
    for (const part of path.split('/').filter(Boolean)) {
      current = current && this.children(current.id).find((c) => c.name.toLowerCase() === part.toLowerCase());
    }
    return current ?? null;
  }

  item(n: Node): DriveItem {
    return { driveId: DRIVE, id: n.id, name: n.name, isFolder: n.isFolder, size: n.size, lastModified: n.modified, webUrl: `demo:${this.pathOf(n)}` };
  }

  pathOf(n: Node): string {
    const parts: string[] = [];
    let c: Node | undefined = n;
    while (c && c.parentId) {
      parts.unshift(c.name);
      c = this.nodes.get(c.parentId);
    }
    return parts.join('/');
  }

  wait = () => (this.latencyMs ? new Promise((r) => setTimeout(r, this.latencyMs)) : Promise.resolve());
}

function node(store: MemoryStore, item: { id: string }): Node {
  const n = store.nodes.get(item.id);
  if (!n || n.deleted) throw new Error('Élément OneDrive introuvable.');
  return n;
}

function memoryDrive(store: MemoryStore): DriveGateway {
  return {
    async resolve(reference) {
      await store.wait();
      const n = store.byPath(reference.replace(/^demo:/, '').replace(/\\/g, '/'));
      if (!n) throw new Error('Ce chemin n’existe pas dans le OneDrive de démonstration.');
      return store.item(n);
    },
    async listChildren(folder) {
      await store.wait();
      return store.children(node(store, folder).id).map((c) => store.item(c));
    },
    async ensureFolder(parent, name) {
      await store.wait();
      const p = node(store, parent);
      const hit = store.children(p.id).find((c) => c.isFolder && c.name.toLowerCase() === name.toLowerCase());
      return store.item(hit ?? store.add(p.id, name, true));
    },
    async copyFile(source, destination, name) {
      await store.wait();
      const s = node(store, source);
      const d = node(store, destination);
      if (store.children(d.id).some((c) => c.name.toLowerCase() === name.toLowerCase())) throw new Error(`${name} existe déjà.`);
      const copy = store.add(d.id, name, false, s.size);
      if (s.sheets) copy.sheets = new Map([...s.sheets].map(([k, v]) => [k, v.map((r) => [...r])]));
      return store.item(copy);
    },
    async rename(item, name) {
      await store.wait();
      const n = node(store, item);
      n.name = name;
      return store.item(n);
    },
    async remove(item) {
      await store.wait();
      node(store, item).deleted = true;
    },
    async thumbnailUrl() {
      return null;
    },
    async pdf(item) {
      return new Blob([`%PDF-1.4\n% ${item.name}\n`], { type: 'application/pdf' });
    },
  };
}

function cellAddress(a: string): { col: number; row: number } {
  const m = /^([A-Z]+)(\d+)$/.exec(a.toUpperCase());
  if (!m) throw new Error(`Adresse invalide : ${a}`);
  const col = [...m[1]].reduce((acc, ch) => acc * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  return { col, row: Number(m[2]) - 1 };
}

function rangeBounds(address: string) {
  const [a, b] = address.split(':');
  const start = cellAddress(a);
  const end = cellAddress(b ?? a);
  return { start, end };
}

const display = (v: unknown) => (typeof v === 'number' && v > 40000 && v < 60000 ? serialToText(v) : cellText(v));
function serialToText(serial: number): string {
  const d = new Date(Date.UTC(1899, 11, 30) + serial * 86_400_000);
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')}.${d.getUTCFullYear()}`;
}

class MemoryWorkbook implements Workbook {
  constructor(private store: MemoryStore, private n: Node) {}
  private grid(sheet: string): unknown[][] {
    const g = this.n.sheets?.get(sheet);
    if (!g) throw new Error(`Feuille introuvable : ${sheet}`);
    return g;
  }
  async session<T>(operation: () => Promise<T>) {
    return operation();
  }
  async worksheetNames() {
    await this.store.wait();
    return [...(this.n.sheets?.keys() ?? [])];
  }
  async usedRange(sheet: string): Promise<UsedRange> {
    await this.store.wait();
    const g = this.grid(sheet);
    let first = g.findIndex((r) => r && r.some((v) => cellText(v) !== ''));
    if (first < 0) first = 0;
    let last = g.length - 1;
    while (last > first && !(g[last] ?? []).some((v) => cellText(v) !== '')) last--;
    const values = g.slice(first, last + 1).map((r) => [...(r ?? [])]);
    return { startRow: first, values, text: values.map((r) => r.map(display)) };
  }
  async readRange(sheet: string, address: string) {
    const g = this.grid(sheet);
    const { start, end } = rangeBounds(address);
    const values: unknown[][] = [];
    for (let r = start.row; r <= end.row; r++) {
      const row: unknown[] = [];
      for (let c = start.col; c <= end.col; c++) row.push(g[r]?.[c] ?? '');
      values.push(row);
    }
    return { values, text: values.map((r) => r.map(display)) };
  }
  async updateRange(sheet: string, address: string, values: unknown[][]) {
    await this.store.wait();
    const g = this.grid(sheet);
    const { start } = rangeBounds(address);
    values.forEach((row, i) => {
      g[start.row + i] ??= [];
      row.forEach((v, j) => (g[start.row + i][start.col + j] = v));
    });
    this.n.modified = new Date().toISOString();
  }
  async insertBlankRow(sheet: string, rowIndex: number) {
    await this.store.wait();
    this.grid(sheet).splice(rowIndex, 0, []);
  }
}

function memoryWorkbooks(store: MemoryStore): WorkbookGateway {
  return { open: (item) => new MemoryWorkbook(store, node(store, item)) };
}

function memoryPlanner(store: MemoryStore): PlannerGateway {
  return {
    async listPlans() {
      await store.wait();
      return store.plans;
    },
    async listBuckets() {
      await store.wait();
      return store.buckets;
    },
    async listMembers() {
      await store.wait();
      return store.members;
    },
    async listTasks() {
      await store.wait();
      return store.tasks.map((t) => ({ ...t, assignments: [...t.assignments] }));
    },
    async createTask(t) {
      await store.wait();
      const task: PlannerTask = { id: newId(), title: t.title, bucketId: t.bucketId, etag: 'W/"1"', assignments: [...t.assignments], dueDateTime: t.dueDateTime };
      store.tasks.push(task);
      return task;
    },
    async patchTask(task, patch) {
      await store.wait();
      const t = store.tasks.find((x) => x.id === task.id);
      if (!t) throw new Error('Tâche Planner introuvable.');
      if (patch.title) t.title = patch.title;
      if (patch.bucketId) t.bucketId = patch.bucketId;
      if (patch.addAssignments) t.assignments.push(...patch.addAssignments);
      if (patch.dueDateTime) t.dueDateTime = patch.dueDateTime;
    },
    async deleteTask(task) {
      await store.wait();
      store.tasks = store.tasks.filter((t) => t.id !== task.id);
    },
    taskUrl: (task) => `demo:planner/${task.id}`,
  };
}

function memoryMail(store: MemoryStore): MailGateway {
  return {
    async send(message) {
      await store.wait();
      store.sentMail.push(message);
    },
    async childFolders(parentId) {
      await store.wait();
      return store.mailFolders.filter((f) => f.parentId === parentId);
    },
    async createFolder(parentId, name) {
      await store.wait();
      const f = { id: newId(), displayName: name, parentId };
      store.mailFolders.push(f);
      return f;
    },
    async deleteFolder(id) {
      await store.wait();
      const drop = new Set([id]);
      let grew = true;
      while (grew) {
        grew = false;
        for (const f of store.mailFolders) if (f.parentId && drop.has(f.parentId) && !drop.has(f.id)) (drop.add(f.id), (grew = true));
      }
      store.mailFolders = store.mailFolders.filter((f) => !drop.has(f.id));
    },
  };
}

export function memoryGateways(store: MemoryStore): Gateways {
  return {
    kind: 'demo',
    me: async () => store.me,
    drive: memoryDrive(store),
    workbooks: memoryWorkbooks(store),
    planner: memoryPlanner(store),
    mail: memoryMail(store),
  };
}
