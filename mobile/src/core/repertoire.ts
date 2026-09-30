// Répertoire chantier : un onglet par année, colonnes A:E saisies par ProjectFlow,
// colonnes F:L comptables jamais touchées (repris de core/repertoire_service.py).
import type { Workbook } from './gateways';
import { formatNumber, isMainNumber, parentOf, parseNumber, type ProjectNumber } from './numero';
import { cellText, swissDate } from './text';

export const WRITABLE_WIDTH = 5;
export const TABLE_WIDTH = 12;
const ROW_RE = /^\d{4}-\d+(?:-\d+)?$/;

export class RepertoireError extends Error {}

export interface RepertoireRow {
  /** Ligne Excel en base 0 (ligne affichée = rowIndex + 1). */
  rowIndex: number;
  number: string;
  values: unknown[];
  text: string[];
}

export interface RepertoireSnapshot {
  year: number;
  rows: RepertoireRow[];
  next: { number: string; rowIndex: number } | null;
}

export interface ProjectFields {
  number: ProjectNumber;
  designation: string;
  societe: string;
  contact: string;
}

const widen = <T,>(row: T[] | undefined, fill: T, width = WRITABLE_WIDTH): T[] => {
  const r = [...(row ?? [])];
  while (r.length < width) r.push(fill);
  return r.slice(0, width);
};

export function infoColumnsEmpty(row: unknown[]): boolean {
  return [1, 2, 3, 4].every((c) => cellText(row[c]) === '');
}

export function sameCell(a: unknown, b: unknown): boolean {
  return cellText(a) === cellText(b);
}

export function yearSheets(names: string[]): string[] {
  return names.filter((n) => /^\d{4}$/.test(n.trim())).sort();
}

export async function readSnapshot(wb: Workbook, year: number): Promise<RepertoireSnapshot> {
  return wb.session(async () => {
    const sheet = await assertSheet(wb, year);
    const range = await wb.usedRange(sheet);
    const rows: RepertoireRow[] = [];
    let next: RepertoireSnapshot['next'] = null;
    range.values.forEach((raw, i) => {
      const number = cellText(raw[0]);
      if (!ROW_RE.test(number)) return;
      const values = widen(raw, '' as unknown);
      const rowIndex = range.startRow + i;
      rows.push({ rowIndex, number, values, text: widen(range.text[i], '') });
      if (!next && isMainNumber(number) && infoColumnsEmpty(values)) next = { number, rowIndex };
    });
    return { year, rows, next };
  });
}

/** Met à jour B:E d'une ligne affichée, en refusant d'écraser une modification concurrente. */
export async function updateEditableRow(
  wb: Workbook,
  year: number,
  row: RepertoireRow,
  edited: string[],
): Promise<void> {
  await wb.session(async () => {
    const sheet = await assertSheet(wb, year);
    const current = await rowAt(wb, sheet, row.rowIndex);
    if (cellText(current[0]) !== row.number) throw new RepertoireError('Le numéro du projet ne peut pas être modifié ici.');
    if (!current.every((v, i) => sameCell(v, row.values[i]))) {
      throw new RepertoireError('La ligne a été modifiée dans le fichier partagé. Actualisez le répertoire avant de recommencer.');
    }
    const values = [row.number, ...widen(edited, '', 4)];
    await wb.updateRange(sheet, `A${row.rowIndex + 1}:E${row.rowIndex + 1}`, [values]);
  });
}

/** Écrit un projet principal sur sa ligne, ou insère un sous-projet après le groupe du parent. */
export async function upsertProject(
  wb: Workbook,
  project: ProjectFields,
  opts: { forceOverwrite?: boolean; today?: Date } = {},
): Promise<void> {
  const today = swissDate(opts.today ?? new Date());
  const number = formatNumber(project.number);
  await wb.session(async () => {
    const sheet = await assertSheet(wb, project.number.year);
    const range = await wb.usedRange(sheet);
    const rows = range.values;
    const find = (n: string) => rows.findIndex((r) => cellText(r[0]) === n);

    if (project.number.subprojectId) {
      const existing = find(number);
      const full = [number, today, project.societe.trim(), project.contact.trim(), project.designation.trim()];
      if (existing >= 0) {
        const at = range.startRow + existing + 1;
        await wb.updateRange(sheet, `A${at}:E${at}`, [full]);
        return;
      }
      const parent = formatNumber(parentOf(project.number));
      const parentIndex = find(parent);
      if (parentIndex < 0) throw new RepertoireError(`Projet parent introuvable dans le répertoire : ${parent}`);
      let after = parentIndex;
      for (let i = parentIndex + 1; i < rows.length; i++) {
        const cell = cellText(rows[i][0]);
        if (cell === parent || cell.startsWith(parent + '-')) after = i;
        else break;
      }
      const insertAt = range.startRow + after + 1;
      await wb.insertBlankRow(sheet, insertAt, TABLE_WIDTH);
      await wb.updateRange(sheet, `A${insertAt + 1}:E${insertAt + 1}`, [full]);
      return;
    }

    const index = find(number);
    if (index < 0) throw new RepertoireError(`Projet introuvable dans le répertoire : ${number}`);
    const row = widen(rows[index], '' as unknown);
    if (!opts.forceOverwrite && !infoColumnsEmpty(row)) {
      throw new RepertoireError('Les colonnes B à E du répertoire contiennent déjà des informations. Utilisez « Mettre à jour » pour remplacer.');
    }
    const updated = [...row];
    updated[0] = number;
    updated[1] = today;
    if (project.societe.trim()) updated[2] = project.societe.trim();
    if (project.contact.trim()) updated[3] = project.contact.trim();
    if (project.designation.trim()) updated[4] = project.designation.trim();
    const at = range.startRow + index + 1;
    await wb.updateRange(sheet, `A${at}:E${at}`, [updated]);
  });
}

/** Lignes du groupe à supprimer : le projet et ses sous-projets (ou le seul sous-projet). */
export function deletionGroup(rows: RepertoireRow[], number: ProjectNumber): RepertoireRow[] {
  const n = formatNumber(number);
  return rows.filter((r) => (number.subprojectId ? r.number === n : r.number === n || r.number.startsWith(n + '-')));
}

async function assertGroupUnchanged(wb: Workbook, sheet: string, number: ProjectNumber, expected: RepertoireRow[]): Promise<void> {
  if (!expected.length) throw new RepertoireError('Aucune ligne de répertoire à supprimer.');
  const range = await wb.usedRange(sheet);
  const current = range.values
    .map((r, i) => ({ rowIndex: range.startRow + i, number: cellText(r[0]), values: widen(r, '' as unknown), text: [] as string[] }))
    .filter((r) => ROW_RE.test(r.number));
  const currentGroup = deletionGroup(current, number);
  const same =
    currentGroup.length === expected.length &&
    expected.every((e) => {
      const c = currentGroup.find((x) => x.rowIndex === e.rowIndex);
      return c && c.number === e.number && c.values.every((v, i) => sameCell(v, e.values[i]));
    });
  if (!same) {
    throw new RepertoireError('Le groupe de projet a changé dans le fichier partagé. Actualisez le répertoire avant de supprimer.');
  }
}

/** Vérifie, avant toute suppression, que les lignes du groupe n'ont pas changé. */
export async function validateDeletion(wb: Workbook, number: ProjectNumber, expected: RepertoireRow[]): Promise<void> {
  await wb.session(async () => assertGroupUnchanged(wb, await assertSheet(wb, number.year), number, expected));
}

/** Vide B:E des lignes du groupe après avoir vérifié qu'elles n'ont pas changé. */
export async function clearProjectRows(wb: Workbook, number: ProjectNumber, expected: RepertoireRow[]): Promise<void> {
  await wb.session(async () => {
    const sheet = await assertSheet(wb, number.year);
    await assertGroupUnchanged(wb, sheet, number, expected);
    for (const row of expected) {
      await wb.updateRange(sheet, `B${row.rowIndex + 1}:E${row.rowIndex + 1}`, [['', '', '', '']]);
    }
  });
}

export function rowNumber(row: RepertoireRow): ProjectNumber {
  return parseNumber(row.number);
}

async function assertSheet(wb: Workbook, year: number): Promise<string> {
  const name = String(year);
  const names = await wb.worksheetNames();
  if (!names.includes(name)) throw new RepertoireError(`Onglet introuvable dans le répertoire : ${name}`);
  return name;
}

async function rowAt(wb: Workbook, sheet: string, rowIndex: number): Promise<unknown[]> {
  const { values } = await wb.readRange(sheet, `A${rowIndex + 1}:E${rowIndex + 1}`);
  return widen(values[0], '' as unknown);
}
