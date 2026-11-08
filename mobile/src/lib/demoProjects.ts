/**
 * « Mode démo » for Création projet: a OneDrive held in memory with
 * the racine projets (one folder per demo dossier of the répertoire, with fiche, prise de
 * cote, photos and plans), the reference folder, and a Planner plan. Nothing is sent to
 * Microsoft; the session keeps what is created until the app is reloaded.
 */

import { demoUsedRange } from '@/lib/demo';
import { GraphError } from '@/lib/graph';
import { DriveError, type DriveGateway, type DriveItem, type DriveRef } from '@/lib/projects/drive';
import { excelSerial, type CellValue, type FicheCells } from '@/lib/projects/fiche';
import { RACINE_LABEL, REFERENCE_LABEL } from '@/lib/projects/location';
import {
  chosenAssignees,
  plannerDueDate,
  plannerTaskTitle,
  taskMatchesProject,
  taskPatch,
  type ExistingTask,
  type PlannerGateway,
} from '@/lib/projects/planner';
import { cellText } from '@/lib/repertoire/rows';

const DRIVE = 'demo-drive';

type Node = {
  id: string;
  name: string;
  folder: boolean;
  size: number;
  modified: string;
  children: Node[];
  cells?: FicheCells;
};

type DemoState = { nodes: Map<string, Node>; racine: Node; reference: Node; counter: number };

let state: DemoState | null = null;
let plannerTasks: ExistingTask[] = [];
/** Delays that let the busy states show; 0 in the tests. */
let latency = 1;

function wait(ms: number): Promise<void> {
  if (!latency) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms * latency));
}

/** Simulated network delays: 1 by default, 0 for the tests. */
export function setDemoLatency(factor: number): void {
  latency = factor;
}

const EMPTY_CELLS: FicheCells = { C3: '', D3: '', D4: '', D5: '', D6: '', C6: '', C9: '', B9: '', E2: '' };

const LOCATIONS = ['Bulle', 'Romont', 'Fribourg', 'Châtel-St-Denis', 'Vevey'];

function build(): DemoState {
  const demo: DemoState = { nodes: new Map(), racine: null as unknown as Node, reference: null as unknown as Node, counter: 0 };
  const now = new Date().toISOString();
  const add = (parent: Node | null, name: string, folder: boolean, extra: Partial<Node> = {}): Node => {
    demo.counter += 1;
    const node: Node = { id: `demo-${demo.counter}`, name, folder, size: folder ? 0 : 48_000 + demo.counter * 731, modified: now, children: [], ...extra };
    demo.nodes.set(node.id, node);
    parent?.children.push(node);
    return node;
  };
  const folder = (parent: Node, name: string) => add(parent, name, true);
  const file = (parent: Node, name: string, cells?: FicheCells) => add(parent, name, false, cells ? { cells } : {});

  demo.racine = add(null, 'Projets', true);
  demo.reference = add(null, '00 - Dossier de référence', true);

  // The reference folder: a model fiche whose B9 and E2 must not survive in a new dossier.
  file(demo.reference, 'Fiche dossier clients.xlsx', {
    ...EMPTY_CELLS,
    D3: 'Societe :',
    D4: 'Contact :',
    D5: 'Projet :',
    D6: 'Localisation :',
    B9: excelSerial(new Date(2025, 0, 2)),
    E2: "fiche d'atelier le 02.01.2025",
  });
  folder(demo.reference, 'photos');
  folder(folder(demo.reference, 'Plans'), "Plan d'exécution");
  folder(demo.reference, 'Devis');
  folder(demo.reference, 'Commandes');
  folder(demo.reference, 'CAO');

  const thisYear = new Date().getFullYear();
  for (const year of [thisYear, thisYear + 1]) {
    const yearFolder = folder(demo.racine, String(year));
    const sheet = demoUsedRange(year);
    sheet.values.slice(1).forEach((row, index) => {
      const [number, date, client, contact, designation] = row.map(cellText);
      if (!client) return;
      const project = folder(yearFolder, number);
      const [day, month] = date.split('.').map(Number);
      file(project, `${number} - Fiche dossier clients.xlsx`, {
        C3: number,
        D3: `Societe : ${client}`,
        D4: `Contact : ${contact}`,
        D5: `Projet : ${designation}`,
        D6: `Localisation : ${LOCATIONS[index % LOCATIONS.length]}`,
        C6: 'LM',
        C9: 'LM',
        B9: excelSerial(new Date(year, (month || 10) - 1, day || 1)),
        E2: "fiche d'atelier le",
      });
      if (index % 2 === 0) file(project, 'Prise de cote initiale.pdf');
      // « photos » on most dossiers, « Photos » on one: found whatever the case.
      const photos = folder(project, index === 1 ? 'Photos' : 'photos');
      ['IMG_0412.jpg', 'IMG_0413.jpg', 'IMG_0415.jpg'].slice(0, 3 - (index % 2)).forEach((name) => file(photos, name));
      if (index === 0) file(folder(photos, 'Montage'), 'IMG_0420.jpg');
      const execution = folder(folder(project, 'Plans'), "Plan d'exécution");
      file(execution, `${number}-ENS-100.pdf`);
      file(execution, `${number}-PRT-100.pdf`);
      if (index % 2 === 0) file(execution, `${number}-PRT-200.pdf`);
      file(folder(project, 'Devis'), `Offre ${number}.pdf`);
      folder(project, 'CAO');
      if (index === 2) {
        // A variant kept in the numbered subfolder, as the desktop finds it.
        file(folder(project, number), `${number}-2 - Fiche dossier clients.xlsx`, {
          C3: `${number}-2`,
          D3: `Societe : ${client}`,
          D4: `Contact : ${contact}`,
          D5: 'Projet : Variante palier',
          D6: `Localisation : ${LOCATIONS[index]}`,
          C6: 'LM',
          C9: 'LM',
          B9: excelSerial(new Date(year, 9, 20)),
          E2: "fiche d'atelier le",
        });
      }
    });
  }
  return demo;
}

function demoState(): DemoState {
  if (!state) state = build();
  return state;
}

function toItem(node: Node): DriveItem {
  return {
    driveId: DRIVE,
    id: node.id,
    name: node.name,
    folder: node.folder,
    size: node.size,
    modified: node.modified,
    webUrl: '',
    count: node.folder ? node.children.length : undefined,
  };
}

function nodeOf(ref: DriveRef): Node {
  const node = demoState().nodes.get(ref.id);
  if (!node) throw new GraphError('Élément introuvable sur OneDrive / SharePoint (404).', 404, 'itemNotFound');
  return node;
}

function assertFree(parent: Node, name: string) {
  if (parent.children.some((child) => child.name.toLowerCase() === name.toLowerCase())) {
    throw new GraphError('Conflit : cet élément existe déjà ou a changé (409).', 409, 'nameAlreadyExists');
  }
}

function clone(node: Node, name: string): Node {
  const demo = demoState();
  demo.counter += 1;
  const copy: Node = {
    ...node,
    id: `demo-${demo.counter}`,
    name,
    modified: new Date().toISOString(),
    cells: node.cells ? { ...node.cells } : undefined,
    children: [],
  };
  demo.nodes.set(copy.id, copy);
  copy.children = node.children.map((child) => clone(child, child.name));
  return copy;
}

/** The demo OneDrive of Création projet, kept for the session. */
export function demoDrive(): DriveGateway {
  return {
    async resolve(_location, label) {
      await wait(120);
      const demo = demoState();
      if (label === REFERENCE_LABEL) return toItem(demo.reference);
      if (label === RACINE_LABEL) return toItem(demo.racine);
      throw new GraphError('Élément introuvable sur OneDrive / SharePoint (404).', 404);
    },
    async children(folder) {
      await wait(60);
      return nodeOf(folder).children.map(toItem);
    },
    async downloadUrl() {
      throw new DriveError('Mode démo : les fichiers d’exemple n’ont pas de contenu à ouvrir.');
    },
    async createFolder(parent, name) {
      await wait(120);
      const node = nodeOf(parent);
      assertFree(node, name);
      const demo = demoState();
      demo.counter += 1;
      const created: Node = { id: `demo-${demo.counter}`, name, folder: true, size: 0, modified: new Date().toISOString(), children: [] };
      demo.nodes.set(created.id, created);
      node.children.push(created);
      return toItem(created);
    },
    async copy(item, parent, name) {
      const source = nodeOf(item);
      const target = nodeOf(parent);
      assertFree(target, name);
      await wait(source.folder ? 900 : 250);
      const copy = clone(source, name);
      target.children.push(copy);
      return toItem(copy);
    },
    async rename(item, name) {
      await wait(100);
      const node = nodeOf(item);
      const parent = [...demoState().nodes.values()].find((candidate) => candidate.children.includes(node));
      if (parent && node.name.toLowerCase() !== name.toLowerCase()) assertFree(parent, name);
      node.name = name;
      return toItem(node);
    },
    async upload(parent, name, source) {
      await wait(300);
      const node = nodeOf(parent);
      let finalName = name;
      let suffix = 2;
      while (node.children.some((child) => child.name.toLowerCase() === finalName.toLowerCase())) {
        const dot = name.lastIndexOf('.');
        finalName = dot > 0 ? `${name.slice(0, dot)} ${suffix}${name.slice(dot)}` : `${name} ${suffix}`;
        suffix += 1;
      }
      const demo = demoState();
      demo.counter += 1;
      const created: Node = {
        id: `demo-${demo.counter}`,
        name: finalName,
        folder: false,
        size: source.size ?? 0,
        modified: new Date().toISOString(),
        children: [],
      };
      demo.nodes.set(created.id, created);
      node.children.push(created);
      return toItem(created);
    },
    async readFiche(item) {
      await wait(150);
      return { ...EMPTY_CELLS, ...nodeOf(item).cells };
    },
    async writeCells(item, writes) {
      await wait(200);
      const node = nodeOf(item);
      const cells: FicheCells = { ...EMPTY_CELLS, ...node.cells };
      for (const write of writes) cells[write.address] = write.value as CellValue;
      node.cells = cells;
    },
  };
}

/** Cells of a demo workbook, for the tests. */
export function demoFicheCells(ref: DriveRef): FicheCells | undefined {
  return demoState().nodes.get(ref.id)?.cells;
}

/** Start the demo drive and plan again (tests). */
export function resetDemoProjects(): void {
  state = null;
  plannerTasks = [];
}

// --------------------------------------------------------------- Planner ----

export const DEMO_PLAN_ID = 'demo-plan';
export const DEMO_BUCKETS = [
  { id: 'demo-bucket-todo', name: 'À faire' },
  { id: 'demo-bucket-progress', name: 'En cours' },
  { id: 'demo-bucket-workshop', name: 'Atelier' },
];
export const DEMO_MEMBERS = [
  { id: 'demo-member-lm', name: 'Lionel M.', email: '' },
  { id: 'demo-member-jr', name: 'Julien R.', email: '' },
  { id: 'demo-member-cb', name: 'Claire B.', email: '' },
];

export function demoPlanner(): PlannerGateway {
  return {
    async buckets() {
      await wait(150);
      return DEMO_BUCKETS;
    },
    async members() {
      await wait(150);
      return DEMO_MEMBERS;
    },
    async ensureProjectTask(request) {
      await wait(250);
      const bucketId = request.bucketId.trim() || DEMO_BUCKETS[0].id;
      const assignees = chosenAssignees(request);
      const assigneeIds = assignees.length ? assignees : [DEMO_MEMBERS[0].id];
      const title = plannerTaskTitle(request.number, request.designation);
      const dueDate = plannerDueDate(request.dueDays);
      const existing = plannerTasks.find((task) => taskMatchesProject(task.title, request.number));
      if (!existing) {
        const task: ExistingTask = {
          id: `demo-task-${plannerTasks.length + 1}`,
          title,
          bucketId,
          etag: 'W/"demo"',
          assignments: assigneeIds,
          dueDateTime: dueDate,
        };
        plannerTasks.push(task);
        return { taskId: task.id, created: true, updated: false };
      }
      const patch = taskPatch(existing, { title, bucketId, assigneeIds, dueDate });
      if (!patch) return { taskId: existing.id, created: false, updated: false };
      existing.title = title;
      existing.bucketId = bucketId;
      existing.assignments = [...new Set([...existing.assignments, ...assigneeIds])];
      if (dueDate) existing.dueDateTime = dueDate;
      return { taskId: existing.id, created: false, updated: true };
    },
  };
}
