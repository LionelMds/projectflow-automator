// Fiche dossier client Excel (repris de core/fiche_service.py).
// C3 numéro · D3..D6 « Societe : … » · C6 géré par · C9 initiales · B9 date de création · E2 date d'atelier.
import { findChild } from './drive';
import type { DriveGateway, DriveItem, Workbook } from './gateways';
import { formatNumber, type ProjectNumber } from './numero';
import { cellText, excelSerial, swissDate } from './text';

export const FICHE_SUFFIX = ' - Fiche dossier clients.xlsx';
const PREFIX_RE = /^\s*(societe|société|contact|projet|localisation)\s*:\s*/i;
const ATELIER_PREFIX_RE = /^\s*fiche\s+d['’]atelier\s+le\s*:?\s*$/i;
const ATELIER_DATE_RE = /\b\d{2}\.\d{2}\.\d{4}\s*$/;

export interface FicheInput {
  number: ProjectNumber;
  designation: string;
  societe: string;
  contact: string;
  localisation: string;
  gerePar: string;
}

export interface FicheData {
  number: string;
  societe: string;
  contact: string;
  designation: string;
  localisation: string;
  gerePar: string;
  initials: string;
}

export const standardFicheName = (n: ProjectNumber) => formatNumber(n) + FICHE_SUFFIX;

/** Dossiers où chercher la fiche : le dossier projet, puis son sous-dossier au nom du numéro. */
export async function ficheSearchDirs(drive: DriveGateway, projectDir: DriveItem, number: ProjectNumber): Promise<DriveItem[]> {
  const nested = await findChild(drive, projectDir, formatNumber(number));
  return nested ? [projectDir, nested] : [projectDir];
}

export async function listFicheCandidates(drive: DriveGateway, projectDir: DriveItem, number: ProjectNumber): Promise<DriveItem[]> {
  const exact = standardFicheName(number);
  const all: DriveItem[] = [];
  for (const dir of await ficheSearchDirs(drive, projectDir, number)) {
    for (const c of await drive.listChildren(dir)) {
      if (!c.isFolder && c.name.toLowerCase().endsWith('.xlsx') && !c.name.startsWith('~$')) all.push(c);
    }
  }
  const rank = (c: DriveItem) => [c.name === exact ? 0 : 1, c.name.toLowerCase().includes('fiche') ? 0 : 1] as const;
  return all.sort((a, b) => {
    const [ra, rb] = [rank(a), rank(b)];
    return ra[0] - rb[0] || ra[1] - rb[1] || a.name.toLowerCase().localeCompare(b.name.toLowerCase());
  });
}

export async function locateFiche(drive: DriveGateway, projectDir: DriveItem, number: ProjectNumber): Promise<DriveItem> {
  const [first] = await listFicheCandidates(drive, projectDir, number);
  if (!first) throw new Error(`Aucune fiche Excel trouvée pour ${formatNumber(number)} dans ${projectDir.name}`);
  return first;
}

/** Renomme la fiche trouvée au nom standard « NUMERO - Fiche dossier clients.xlsx ». */
export async function standardizeFiche(drive: DriveGateway, projectDir: DriveItem, number: ProjectNumber): Promise<DriveItem> {
  const fiche = await locateFiche(drive, projectDir, number);
  const wanted = standardFicheName(number);
  if (fiche.name === wanted) return fiche;
  return drive.rename(fiche, wanted);
}

export async function firstSheet(wb: Workbook): Promise<string> {
  const [name] = await wb.worksheetNames();
  if (!name) throw new Error('La fiche Excel ne contient aucune feuille.');
  return name;
}

export async function fillFiche(
  wb: Workbook,
  project: FicheInput,
  opts: { newFiche: boolean; initials: string; today?: Date },
): Promise<void> {
  const today = opts.today ?? new Date();
  await wb.session(async () => {
    const sheet = await firstSheet(wb);
    const current = await wb.readRange(sheet, 'B2:E9');
    const at = (address: string) => {
      const col = address.charCodeAt(0) - 66;
      const row = Number(address.slice(1)) - 2;
      return current.values[row]?.[col];
    };
    await wb.updateRange(sheet, 'C3', [[formatNumber(project.number)]]);
    const prefixed: [string, string, string][] = [
      ['D3', 'Societe', project.societe],
      ['D4', 'Contact', project.contact],
      ['D5', 'Projet', project.designation],
      ['D6', 'Localisation', project.localisation],
    ];
    for (const [cell, prefix, value] of prefixed) {
      if (value.trim()) await wb.updateRange(sheet, cell, [[`${prefix} : ${value.trim()}`]]);
    }
    if (project.gerePar.trim()) await wb.updateRange(sheet, 'C6', [[project.gerePar.trim()]]);
    if (opts.initials.trim()) await wb.updateRange(sheet, 'C9', [[opts.initials.trim()]]);
    if (cellText(at('B9')) === '' || opts.newFiche) {
      await wb.updateRange(sheet, 'B9', [[excelSerial(today)]], [['dd.mm.yyyy']]);
    }
    if (opts.newFiche) {
      const e2 = cellText(at('E2'));
      const prefix = e2.replace(ATELIER_DATE_RE, '').trimEnd();
      if (ATELIER_DATE_RE.test(e2) && ATELIER_PREFIX_RE.test(prefix)) await wb.updateRange(sheet, 'E2', [[prefix]]);
    }
  });
}

/** Date une fiche copiée pour une sortie de dossier (E2), sans toucher au reste. */
export async function ensureAtelierDate(wb: Workbook, today = new Date()): Promise<void> {
  await wb.session(async () => {
    const sheet = await firstSheet(wb);
    const { values } = await wb.readRange(sheet, 'E2');
    const current = cellText(values[0]?.[0]).replace(ATELIER_DATE_RE, '').trimEnd();
    const stamp = swissDate(today);
    const next = !current || ATELIER_PREFIX_RE.test(current) ? `fiche d'atelier le ${stamp}` : `${current} ${stamp}`;
    await wb.updateRange(sheet, 'E2', [[next]]);
  });
}

export async function readFiche(wb: Workbook): Promise<FicheData> {
  return wb.session(async () => {
    const sheet = await firstSheet(wb);
    const { text } = await wb.readRange(sheet, 'B2:E9');
    const at = (address: string) => cellText(text[Number(address.slice(1)) - 2]?.[address.charCodeAt(0) - 66]);
    const strip = (v: string) => v.replace(PREFIX_RE, '').trim();
    return {
      number: at('C3'),
      societe: strip(at('D3')),
      contact: strip(at('D4')),
      designation: strip(at('D5')),
      localisation: strip(at('D6')),
      gerePar: at('C6'),
      initials: at('C9'),
    };
  });
}
