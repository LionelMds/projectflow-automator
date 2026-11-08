/**
 * The « Fiche dossier clients » workbook, ported from `core/fiche_service.py`: where it is
 * looked for, its standard name, and the cells ProjectFlow reads and writes on its first
 * worksheet (C3 number, D3:D6 prefixed client fields, C6 manager, C9 initials, B9 creation
 * date, E2 workshop date).
 */

import { numberText, type ProjectNumber } from '@/lib/projects/numbers';

export const FICHE_SUFFIX = ' - Fiche dossier clients.xlsx';

/** One read covers every cell of the fiche ProjectFlow uses. */
export const FICHE_RANGE = 'B2:E9';

export type CellValue = string | number | boolean | null;

export type FicheCells = {
  C3: CellValue;
  D3: CellValue;
  D4: CellValue;
  D5: CellValue;
  D6: CellValue;
  C6: CellValue;
  C9: CellValue;
  B9: CellValue;
  E2: CellValue;
};

export type FicheData = {
  number: string;
  societe: string;
  contact: string;
  designation: string;
  localisation: string;
  gere_par: string;
  user_initials: string;
};

export type FicheProject = {
  number: ProjectNumber;
  designation: string;
  societe: string;
  contact: string;
  localisation: string;
  gere_par: string;
};

/** One cell to write; a date also gets its number format, as openpyxl saves it. */
export type CellWrite = { address: keyof FicheCells; value: CellValue; numberFormat?: string };

const PREFIX_RE = /^\s*(societe|société|contact|projet|localisation)\s*:\s*/iu;
const ATELIER_PREFIX_RE = /^\s*fiche\s+d['’]atelier\s+le\s*:?\s*$/iu;
const ATELIER_DATE_RE = /\b\d{2}\.\d{2}\.\d{4}\s*$/;
export const DATE_FORMAT = 'DD.MM.YYYY';

export function standardFicheName(number: ProjectNumber): string {
  return `${numberText(number)}${FICHE_SUFFIX}`;
}

export function cellText(value: unknown): string {
  return value === null || value === undefined ? '' : String(value).trim();
}

function isEmptyCell(value: unknown): boolean {
  return value === null || value === undefined || value === '';
}

export function stripPrefix(value: string): string {
  return value.replace(PREFIX_RE, '').trim();
}

/** Cells of the range `B2:E9` (`values`, or Excel's `text` when it says more). */
export function ficheCellsFromRange(values: CellValue[][], text?: string[][]): FicheCells {
  const at = (address: string): CellValue => {
    const column = address.charCodeAt(0) - 'B'.charCodeAt(0);
    const row = Number(address.slice(1)) - 2;
    const value = values[row]?.[column] ?? null;
    return value === undefined ? null : value;
  };
  const textAt = (address: string): CellValue => {
    const column = address.charCodeAt(0) - 'B'.charCodeAt(0);
    const row = Number(address.slice(1)) - 2;
    const shown = text?.[row]?.[column];
    const value = at(address);
    // A number typed in C3 or C6 reads as Excel shows it; a date stays a serial number.
    return typeof value === 'number' && shown ? shown : value;
  };
  return {
    C3: textAt('C3'),
    D3: at('D3'),
    D4: at('D4'),
    D5: at('D5'),
    D6: at('D6'),
    C6: textAt('C6'),
    C9: textAt('C9'),
    B9: at('B9'),
    E2: at('E2'),
  };
}

/** `read_fiche`: the dossier fields, prefixes « Societe : » etc. removed. */
export function ficheData(cells: FicheCells): FicheData {
  return {
    number: cellText(cells.C3),
    societe: stripPrefix(cellText(cells.D3)),
    contact: stripPrefix(cellText(cells.D4)),
    designation: stripPrefix(cellText(cells.D5)),
    localisation: stripPrefix(cellText(cells.D6)),
    gere_par: cellText(cells.C6),
    user_initials: cellText(cells.C9),
  };
}

/** Days since 1899-12-30: how Excel stores a date. */
export function excelSerial(date: Date): number {
  const day = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((day - Date.UTC(1899, 11, 30)) / 86_400_000);
}

/**
 * E2 of a new fiche: a workshop date inherited from the copied model is removed when
 * it follows the recognised label (`_clear_inherited_atelier_date`); null when unchanged.
 */
export function clearedAtelierDate(current: unknown): string | null {
  const text = cellText(current);
  const prefix = text.replace(ATELIER_DATE_RE, '').trimEnd();
  if (ATELIER_DATE_RE.test(text) && ATELIER_PREFIX_RE.test(prefix)) return prefix;
  return null;
}

function prefixed(prefix: string, value: string): string | null {
  return value.trim() ? `${prefix} : ${value.trim()}` : null;
}

/**
 * Cells written by `fill_fiche` / `fill_subproject_fiche`: the number always, the client
 * fields when typed, C6 and C9 when given, B9 when empty or on a new fiche, and the
 * inherited workshop date removed from a new fiche.
 */
export function ficheWrites(
  current: FicheCells,
  project: FicheProject,
  options: { initials: string; newFiche: boolean; today: Date },
): CellWrite[] {
  const writes: CellWrite[] = [{ address: 'C3', value: numberText(project.number) }];
  const fields: [keyof FicheCells, string, string][] = [
    ['D3', 'Societe', project.societe],
    ['D4', 'Contact', project.contact],
    ['D5', 'Projet', project.designation],
    ['D6', 'Localisation', project.localisation],
  ];
  for (const [address, prefix, value] of fields) {
    const text = prefixed(prefix, value);
    if (text !== null) writes.push({ address, value: text });
  }
  if (project.gere_par.trim()) writes.push({ address: 'C6', value: project.gere_par.trim() });
  if (options.initials.trim()) writes.push({ address: 'C9', value: options.initials.trim() });
  if (isEmptyCell(current.B9) || options.newFiche) {
    writes.push({ address: 'B9', value: excelSerial(options.today), numberFormat: DATE_FORMAT });
  }
  if (options.newFiche) {
    const cleared = clearedAtelierDate(current.E2);
    if (cleared !== null) writes.push({ address: 'E2', value: cleared });
  }
  return writes;
}

/** A workbook found while looking for the fiche. */
export type FicheCandidate<T = unknown> = {
  name: string;
  /** Folder names from the project folder (`[]` for the project folder itself). */
  folder: string[];
  item: T;
};

/** `*.xlsx` without Excel's lock files « ~$… ». */
export function isWorkbookName(name: string): boolean {
  return /\.xlsx$/i.test(name) && !name.startsWith('~$');
}

/**
 * Order of `list_candidates`: the standard name first, then names containing « fiche »,
 * then by name; the project folder before the numbered subfolder on a tie.
 */
export function sortFicheCandidates<T>(candidates: readonly FicheCandidate<T>[], number: ProjectNumber | null): FicheCandidate<T>[] {
  const standard = number ? standardFicheName(number) : null;
  const key = (candidate: FicheCandidate<T>): [number, number, string] => [
    standard !== null && candidate.name === standard ? 0 : 1,
    candidate.name.toLowerCase().includes('fiche') ? 0 : 1,
    candidate.name.toLowerCase(),
  ];
  return candidates
    .map((candidate, index) => ({ candidate, index, key: key(candidate) }))
    .sort((left, right) => {
      for (let part = 0; part < 2; part += 1) {
        const difference = (left.key[part] as number) - (right.key[part] as number);
        if (difference) return difference;
      }
      if (left.key[2] !== right.key[2]) return left.key[2] < right.key[2] ? -1 : 1;
      return left.index - right.index;
    })
    .map(({ candidate }) => candidate);
}

/** Form fields that differ from the fiche (`_non_empty_changed`): an empty field never counts. */
export function infoWouldChange(project: FicheProject, fiche: FicheData): boolean {
  const pairs: [string, string][] = [
    [project.designation, fiche.designation],
    [project.societe, fiche.societe],
    [project.contact, fiche.contact],
    [project.localisation, fiche.localisation],
    [project.gere_par, fiche.gere_par],
  ];
  return pairs.some(([current, existing]) => current.trim() !== '' && current.trim() !== existing.trim());
}
