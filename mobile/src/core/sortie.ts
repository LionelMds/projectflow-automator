// Sortie dossier : copie horodatée fiche / prise de cote / photos / plans (repris de core/sortie_service.py).
// Les fichiers sources ne sont jamais modifiés.
import { findChild, freeName, walkFiles } from './drive';
import { ensureAtelierDate, ficheSearchDirs, listFicheCandidates } from './fiche';
import type { DriveItem, Gateways } from './gateways';
import { formatNumber, type ProjectNumber } from './numero';
import type { Step } from './steps';
import { pad2 } from './text';

const IMAGE = ['.bmp', '.gif', '.jpeg', '.jpg', '.png', '.tif', '.tiff', '.webp', '.heic'];
const ext = (name: string) => name.slice(name.lastIndexOf('.')).toLowerCase();

export interface Inventory {
  projectDir: DriveItem;
  fiches: DriveItem[];
  mesures: DriveItem[];
  photos: DriveItem[];
  plans: DriveItem[];
}

const byName = (a: DriveItem, b: DriveItem) => a.name.toLowerCase().localeCompare(b.name.toLowerCase());
const unique = (items: DriveItem[]) => [...new Map(items.map((i) => [i.id, i])).values()];

export async function discover(gw: Gateways, projectDir: DriveItem, number: ProjectNumber): Promise<Inventory> {
  const dirs = await ficheSearchDirs(gw.drive, projectDir, number);
  const fiches = await listFicheCandidates(gw.drive, projectDir, number);

  const plans: DriveItem[] = [];
  for (const dir of dirs) {
    const plansDir = await findChild(gw.drive, dir, 'plans');
    if (!plansDir) continue;
    const exec = (await findChild(gw.drive, plansDir, "plan d'execution")) ?? plansDir;
    plans.push(...(await walkFiles(gw.drive, exec)).files.map((f) => f.item).filter((i) => ext(i.name) === '.pdf'));
  }
  const planIds = new Set(plans.map((p) => p.id));

  const mesures: DriveItem[] = [];
  const photos: DriveItem[] = [];
  for (const dir of dirs) {
    mesures.push(...(await gw.drive.listChildren(dir)).filter((c) => !c.isFolder && ext(c.name) === '.pdf' && !planIds.has(c.id)));
    const photoDir = await findChild(gw.drive, dir, 'photos');
    if (photoDir) photos.push(...(await walkFiles(gw.drive, photoDir)).files.map((f) => f.item).filter((i) => IMAGE.includes(ext(i.name))));
  }
  return {
    projectDir,
    fiches: unique(fiches),
    mesures: unique(mesures).sort(byName),
    photos: unique(photos).sort(byName),
    plans: unique(plans).sort(byName),
  };
}

export function outputFolderName(number: ProjectNumber, now: Date): string {
  const stamp = `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}-${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`;
  return `${formatNumber(number)} - Sortie dossier - ${stamp}`;
}

export interface SortieSelection {
  fiche: DriveItem;
  mesure: DriveItem | null;
  photos: DriveItem[];
  plans: DriveItem[];
}

export function buildSortieSteps(
  gw: Gateways,
  inv: Inventory,
  number: ProjectNumber,
  sel: SortieSelection,
  out: { folder?: DriveItem },
  now = new Date(),
): Step[] {
  let target: DriveItem;
  const copyInto = async (sub: string, items: DriveItem[]) => {
    if (!items.length) return;
    const folder = await gw.drive.ensureFolder(target, sub);
    const names = new Set<string>();
    const copied: DriveItem[] = [];
    for (const item of items) {
      const name = freeName(names, item.name);
      names.add(name.toLowerCase());
      copied.push(await gw.drive.copyFile(item, folder, name));
    }
    return copied;
  };
  const plural = (n: number) => `${n} fichier${n > 1 ? 's' : ''}`;
  return [
    {
      label: 'Dossier horodaté créé sous « Sorties dossier »',
      run: async () => {
        const parent = (await findChild(gw.drive, inv.projectDir, 'Sorties dossier')) ?? (await gw.drive.ensureFolder(inv.projectDir, 'Sorties dossier'));
        const existing = new Set((await gw.drive.listChildren(parent)).map((c) => c.name.toLowerCase()));
        let name = outputFolderName(number, now);
        for (let i = 2; existing.has(name.toLowerCase()); i++) name = `${outputFolderName(number, now)} (${i})`;
        target = await gw.drive.ensureFolder(parent, name);
        out.folder = target;
      },
    },
    {
      label: '01 - Fiche dossier (1 fichier) · date d’atelier en E2',
      run: async () => {
        const [copy] = (await copyInto('01 - Fiche dossier', [sel.fiche]))!;
        await ensureAtelierDate(gw.workbooks.open(copy));
      },
    },
    { label: `02 - Prise de cote (${plural(sel.mesure ? 1 : 0)})`, run: async () => void (await copyInto('02 - Prise de cote', sel.mesure ? [sel.mesure] : [])) },
    { label: `03 - Photos (${plural(sel.photos.length)})`, run: async () => void (await copyInto('03 - Photos', sel.photos)) },
    { label: `04 - Plans (${plural(sel.plans.length)})`, run: async () => void (await copyInto('04 - Plans', sel.plans)) },
  ];
}
