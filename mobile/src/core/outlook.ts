// Arborescence de dossiers Outlook d'un projet (repris de project_service.py), créée via Graph.
import type { MailFolder, MailGateway } from './gateways';
import { formatNumber, type ProjectNumber } from './numero';

const squash = (v: string) => v.split(/\s+/).filter(Boolean).join(' ');

export function outlookProjectFolderName(number: ProjectNumber, designation: string): string {
  const d = squash(designation);
  return d ? `${formatNumber(number)} (${d})` : formatNumber(number);
}

export function renderFolderName(template: string, number: ProjectNumber, designation: string): string {
  if (template.includes('[PROJECT_FOLDER]')) {
    return squash(template.replace('[PROJECT_FOLDER]', outlookProjectFolderName(number, designation)));
  }
  const isNumberTemplate = template.includes('[NUMERO]') || template.includes('[PROJECT]') || (template.includes('[YYYY]') && template.includes('[XXXX]'));
  if (!template.includes('[DESIGNATION]') && isNumberTemplate) return outlookProjectFolderName(number, designation);
  return squash(
    template
      .replaceAll('[YYYY]', String(number.year))
      .replaceAll('[XXXX]', number.projectId)
      .replaceAll('[NUMERO]', formatNumber(number))
      .replaceAll('[PROJECT]', formatNumber(number))
      .replaceAll('[DESIGNATION]', designation.trim()),
  );
}

export function renderPaths(paths: string[][], number: ProjectNumber, designation: string): string[][] {
  return paths.map((p) => p.map((t) => renderFolderName(t, number, designation)));
}

export async function ensureFolderPath(gw: MailGateway, names: string[]): Promise<void> {
  let parent: string | null = null;
  for (const name of names) {
    const children = await gw.childFolders(parent);
    const hit = children.find((c) => c.displayName.toLowerCase() === name.toLowerCase());
    parent = (hit ?? (await gw.createFolder(parent, name))).id;
  }
}

export async function deleteFolderPath(gw: MailGateway, names: string[]): Promise<boolean> {
  let parent: string | null = null;
  let leaf: string | null = null;
  for (const name of names) {
    const hit: MailFolder | undefined = (await gw.childFolders(parent)).find((c) => c.displayName.toLowerCase() === name.toLowerCase());
    if (!hit) return false;
    parent = leaf = hit.id;
  }
  if (!leaf) return false;
  await gw.deleteFolder(leaf);
  return true;
}
