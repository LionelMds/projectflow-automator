import type { DriveGateway, DriveItem } from './gateways';
import { folderKey } from './text';

/** Trouve « Plan d’exécution » en cherchant « plan d'execution » (accents, casse, apostrophes). */
export async function findChild(drive: DriveGateway, parent: DriveItem, name: string, folder = true): Promise<DriveItem | null> {
  const wanted = folderKey(name.replace(/[’‘]/g, "'"));
  const children = await drive.listChildren(parent);
  return children.find((c) => c.isFolder === folder && folderKey(c.name.replace(/[’‘]/g, "'")) === wanted) ?? null;
}

/** Descend un chemin « a/b/c » ; renvoie null dès qu'un segment manque. */
export async function findPath(drive: DriveGateway, root: DriveItem, path: string[]): Promise<DriveItem | null> {
  let current: DriveItem | null = root;
  for (const part of path) {
    if (!current) return null;
    current = await findChild(drive, current, part);
  }
  return current;
}

/** Descend un chemin en créant les dossiers manquants (en réutilisant ceux qui existent sous un autre accent). */
export async function ensurePath(drive: DriveGateway, root: DriveItem, path: string[]): Promise<DriveItem> {
  let current = root;
  for (const part of path) {
    current = (await findChild(drive, current, part)) ?? (await drive.ensureFolder(current, part));
  }
  return current;
}

export interface WalkedFile {
  item: DriveItem;
  /** Chemin relatif des dossiers parents. */
  dirs: string[];
}

export async function walkFiles(drive: DriveGateway, folder: DriveItem, dirs: string[] = []): Promise<{ files: WalkedFile[]; folders: string[][] }> {
  const files: WalkedFile[] = [];
  const folders: string[][] = [];
  for (const child of await drive.listChildren(folder)) {
    if (child.isFolder) {
      const sub = [...dirs, child.name];
      folders.push(sub);
      const nested = await walkFiles(drive, child, sub);
      files.push(...nested.files);
      folders.push(...nested.folders);
    } else {
      files.push({ item: child, dirs });
    }
  }
  return { files, folders };
}

/** Copie l'arborescence de référence dans le projet sans jamais écraser un fichier existant. */
export async function copyTreeNoOverwrite(
  drive: DriveGateway,
  source: DriveItem,
  destination: DriveItem,
  rename: (name: string) => string = (n) => n,
): Promise<{ copied: DriveItem[]; skipped: number }> {
  const { files, folders } = await walkFiles(drive, source);
  const cache = new Map<string, DriveItem>([['', destination]]);
  const folderFor = async (dirs: string[]): Promise<DriveItem> => {
    const key = dirs.map(rename).join('/');
    const hit = cache.get(key);
    if (hit) return hit;
    const parent = await folderFor(dirs.slice(0, -1));
    const created = await ensurePath(drive, parent, [rename(dirs[dirs.length - 1])]);
    cache.set(key, created);
    return created;
  };
  for (const dirs of folders) await folderFor(dirs);
  const copied: DriveItem[] = [];
  let skipped = 0;
  const listing = new Map<string, Set<string>>();
  for (const f of files) {
    const target = await folderFor(f.dirs);
    let names = listing.get(target.id);
    if (!names) {
      names = new Set((await drive.listChildren(target)).map((c) => c.name.toLowerCase()));
      listing.set(target.id, names);
    }
    const name = rename(f.item.name);
    if (names.has(name.toLowerCase())) {
      skipped++;
      continue;
    }
    copied.push(await drive.copyFile(f.item, target, name));
    names.add(name.toLowerCase());
  }
  return { copied, skipped };
}

/** Nom libre « nom (2).ext » dans un dossier. */
export function freeName(existing: Set<string>, name: string): string {
  if (!existing.has(name.toLowerCase())) return name;
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  for (let i = 2; ; i++) {
    const candidate = `${stem} (${i})${ext}`;
    if (!existing.has(candidate.toLowerCase())) return candidate;
  }
}
