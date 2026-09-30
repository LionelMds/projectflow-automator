// Copie des modèles CAO « 20XX-XXXX-* » renommés au numéro du projet (repris de cad/templates.py).
// La liaison des assemblages SolidWorks (Document Manager, COM) reste sur le poste Windows.
import { ensurePath, findChild, walkFiles } from './drive';
import type { DriveItem } from './gateways';
import { formatNumber, type ProjectNumber } from './numero';
import type { Ctx } from './project';
import { cadSubfolderParts } from './settings';

export const TEMPLATE_MARKER = '20XX-XXXX';
const TEMPORARY = ['.bak', '.dwl', '.dwl2'];

export const replaceMarker = (name: string, number: string) => name.replaceAll(TEMPLATE_MARKER, number);

export function isTemplateFile(name: string): boolean {
  const lower = name.toLowerCase();
  return name.includes(TEMPLATE_MARKER) && !name.startsWith('~$') && !TEMPORARY.some((s) => lower.endsWith(s));
}

export async function applyCadTemplates(
  ctx: Ctx,
  projectDir: DriveItem,
  number: ProjectNumber,
  kind: 'solidworks' | 'autocad',
): Promise<{ created: number; skipped: number }> {
  const ref = ctx.settings.cad[kind];
  if (!ref) throw new Error(`Dossier modèle ${kind === 'solidworks' ? 'SolidWorks' : 'AutoCAD'} non configuré (Paramètres > Modèles CAO).`);
  const templateDir: DriveItem = { driveId: ref.driveId, id: ref.id, name: ref.name, isFolder: true, size: 0, lastModified: '', webUrl: ref.webUrl };
  const n = formatNumber(number);
  let base = projectDir;
  if (number.subprojectId) base = (await findChild(ctx.gw.drive, projectDir, n)) ?? projectDir;
  const destination = await ensurePath(ctx.gw.drive, base, cadSubfolderParts(ctx.settings.cad.subfolder));
  const { files } = await walkFiles(ctx.gw.drive, templateDir);
  let created = 0;
  let skipped = 0;
  for (const f of files) {
    if (!isTemplateFile(f.item.name)) continue;
    const folder = await ensurePath(ctx.gw.drive, destination, f.dirs.map((d) => replaceMarker(d, n)));
    const name = replaceMarker(f.item.name, n);
    const exists = (await ctx.gw.drive.listChildren(folder)).some((c) => c.name.toLowerCase() === name.toLowerCase());
    if (exists) {
      skipped++;
      continue;
    }
    await ctx.gw.drive.copyFile(f.item, folder, name);
    created++;
  }
  return { created, skipped };
}
