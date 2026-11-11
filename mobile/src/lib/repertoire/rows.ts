/**
 * Rules of the répertoire chantier, ported from `core/repertoire_service.py`:
 * one worksheet per year, columns A:E are ProjectFlow's (No., Date, Client, Contact,
 * Désignation) and the accounting columns F:L are never read into the app nor written.
 */

export const WRITABLE_WIDTH = 5;
export const HEADERS = ['No.', 'Date', 'Client', 'Contact', 'Désignation'] as const;

export type Cell = string | number | boolean | null;

export type RepertoireRow = {
  /** 1-based row of the worksheet, as in the address `A{sheetRow}:E{sheetRow}`. */
  sheetRow: number;
  number: string;
  /** Raw A:E values as Graph returned them (dates as Excel serial numbers). */
  values: Cell[];
  /** A:E as Excel displays them (dates formatted). */
  texts: string[];
};

export type RepertoireSnapshot = {
  year: number;
  rows: RepertoireRow[];
  /** First main-project row whose client, contact and designation are still empty. */
  nextAvailable: { number: string; sheetRow: number } | null;
};

const DASHES = /[‐‑‒–—―−]/g;
const PROJECT_RE = /^(\d{4})-(\d{3,})(?:-([A-Z0-9]+(?:-[A-Z0-9]+)*))?$/;
const MAIN_PROJECT_RE = /^(\d{4})-(\d+)$/;
/** Client, contact and designation: empty on a free row (`PROJECT_INFO_COLUMNS`). */
const INFO_COLUMNS = [2, 3, 4];

export function cellText(value: unknown): string {
  if (value === null || value === undefined) return '';
  return String(value).trim();
}

export function canonicalNumber(value: unknown): string {
  return cellText(value).replace(DASHES, '-').replace(/\s+/g, '').toUpperCase();
}

export function isProjectNumber(value: unknown): boolean {
  return PROJECT_RE.test(canonicalNumber(value));
}

/** Start row and column (1-based) of a range address such as `'2026'!A1:L400`. */
export function addressStart(address: string): { row: number; column: number } {
  const local = address.includes('!') ? address.slice(address.lastIndexOf('!') + 1) : address;
  const match = /^\$?([A-Z]+)\$?(\d+)/.exec(local.trim());
  if (!match) return { row: 1, column: 1 };
  let column = 0;
  for (const letter of match[1]) column = column * 26 + (letter.charCodeAt(0) - 64);
  return { row: Number(match[2]), column };
}

function widen<T>(row: readonly T[] | undefined, filler: T, offset: number): T[] {
  // The used range may start after column A: shift so index 0 is always column A.
  const cells: T[] = [...Array(offset).fill(filler), ...(row ?? [])];
  while (cells.length < WRITABLE_WIDTH) cells.push(filler);
  return cells.slice(0, WRITABLE_WIDTH);
}

export function buildSnapshot(
  year: number,
  usedRange: { address: string; values: Cell[][]; text?: string[][] },
): RepertoireSnapshot {
  const start = addressStart(usedRange.address);
  const offset = Math.max(0, start.column - 1);
  const rows: RepertoireRow[] = [];
  let nextAvailable: RepertoireSnapshot['nextAvailable'] = null;
  usedRange.values.forEach((raw, index) => {
    const values = widen<Cell>(raw, '', offset);
    const texts = widen<string>(usedRange.text?.[index]?.map(String), '', offset).map((text, column) =>
      text.trim() || cellText(values[column]),
    );
    const number = cellText(values[0]);
    const sheetRow = start.row + index;
    if (!nextAvailable && MAIN_PROJECT_RE.test(number) && INFO_COLUMNS.every((column) => cellText(values[column]) === '')) {
      nextAvailable = { number, sheetRow };
    }
    if (isProjectNumber(number)) rows.push({ sheetRow, number, values, texts });
  });
  return { year, rows, nextAvailable };
}

/** Rows shown in the list: filled rows, plus nothing after the next free one. */
export function visibleRows(snapshot: RepertoireSnapshot): RepertoireRow[] {
  return snapshot.rows.filter((row) => INFO_COLUMNS.some((column) => cellText(row.values[column]) !== ''));
}

/** Search by number, client, contact or designation, ignoring case and accents. */
export function filterRows(rows: RepertoireRow[], query: string): RepertoireRow[] {
  const wanted = fold(query);
  if (!wanted) return rows;
  const words = wanted.split(/\s+/);
  return rows.filter((row) => {
    const haystack = fold([row.number, ...row.texts.slice(2)].join(' '));
    return words.every((word) => haystack.includes(word));
  });
}

function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

/** Same comparison as `_same_cell` on the desktop: by trimmed text. */
export function sameCells(left: readonly unknown[], right: readonly unknown[]): boolean {
  for (let column = 0; column < WRITABLE_WIDTH; column += 1) {
    if (cellText(left[column]) !== cellText(right[column])) return false;
  }
  return true;
}

export type RowEdit = { date: string; client: string; contact: string; designation: string };

export function editFromRow(row: RepertoireRow): RowEdit {
  return { date: row.texts[1], client: row.texts[2], contact: row.texts[3], designation: row.texts[4] };
}

/**
 * Values written back to A:E. Unchanged cells keep their raw value (a date stays an
 * Excel date); a retyped date is written as text « 29.10.2026 », as the desktop does.
 */
export function editedValues(row: RepertoireRow, edit: RowEdit): Cell[] {
  const original = editFromRow(row);
  const fields: (keyof RowEdit)[] = ['date', 'client', 'contact', 'designation'];
  const values: Cell[] = [row.values[0]];
  fields.forEach((field, index) => {
    const column = index + 1;
    values.push(edit[field].trim() === original[field].trim() ? row.values[column] : edit[field].trim());
  });
  return values;
}

export function changedFields(row: RepertoireRow, edit: RowEdit): Set<keyof RowEdit> {
  const original = editFromRow(row);
  const changed = new Set<keyof RowEdit>();
  (Object.keys(edit) as (keyof RowEdit)[]).forEach((field) => {
    if (edit[field].trim() !== original[field].trim()) changed.add(field);
  });
  return changed;
}

export function rowAddress(sheetRow: number): string {
  return `A${sheetRow}:E${sheetRow}`;
}

// ------------------------------------------------------------- upsert ----

/** Width of the répertoire table, accounting columns included (`REPERTOIRE_TABLE_WIDTH`). */
export const TABLE_WIDTH = 12;

export class RepertoireUpsertError extends Error {}

/** What a project form writes into the répertoire (`ProjectInput` fields used there). */
export type ProjectRowInput = {
  /** « 2026-4995 » or « 2026-4995-2 ». */
  number: string;
  /** Main project number of a subproject; null for a main project. */
  parent: string | null;
  societe: string;
  contact: string;
  designation: string;
};

/** The write that `upsert_project` makes, as 1-based worksheet rows. */
export type UpsertPlan =
  | { kind: 'update'; sheetRow: number; values: Cell[] }
  | { kind: 'insert'; sheetRow: number; formatSourceRow: number | null; values: Cell[] };

/** B:E, the date included (`PROJECT_INFO_COLUMNS` of `duplication.py`). */
const INFO_COLUMNS_WITH_DATE = [1, 2, 3, 4];

const DOTTED = (date: Date) =>
  `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}.${date.getFullYear()}`;

/**
 * Plan the répertoire write of a project, as `RepertoireService.upsert_project`:
 * a main project fills its pre-numbered row (refused when B:E already hold something,
 * unless « Mettre à jour »); a subproject updates its row, or gets a new row inserted
 * after its parent's group. The date is written « 06.11.2026 », as the desktop sends it.
 */
export function planUpsert(
  usedRange: { address: string; values: Cell[][] },
  project: ProjectRowInput,
  options: { today: Date; forceOverwrite: boolean },
): UpsertPlan {
  const start = addressStart(usedRange.address);
  const offset = Math.max(0, start.column - 1);
  const rows = usedRange.values.map((raw) => widen<Cell>(raw, '', offset));
  const created = DOTTED(options.today);
  const find = (number: string) => rows.findIndex((row) => cellText(row[0]) === number);

  if (project.parent !== null) {
    const values: Cell[] = [project.number, created, project.societe.trim(), project.contact.trim(), project.designation.trim()];
    const existing = find(project.number);
    if (existing >= 0) return { kind: 'update', sheetRow: start.row + existing, values };
    const parentIndex = find(project.parent);
    if (parentIndex < 0) {
      throw new RepertoireUpsertError(`Projet parent introuvable dans le répertoire : ${project.parent}`);
    }
    let insertAfter = parentIndex;
    for (let index = parentIndex + 1; index < rows.length; index += 1) {
      const cell = cellText(rows[index][0]);
      if (cell === project.parent || cell.startsWith(`${project.parent}-`)) {
        insertAfter = index;
        continue;
      }
      break;
    }
    const formatSource = rows.findIndex(
      (row) => MAIN_PROJECT_RE.test(cellText(row[0])) && INFO_COLUMNS_WITH_DATE.every((column) => cellText(row[column]) === ''),
    );
    return {
      kind: 'insert',
      sheetRow: start.row + insertAfter + 1,
      formatSourceRow: formatSource >= 0 ? start.row + formatSource : null,
      values,
    };
  }

  const index = find(project.number);
  if (index < 0) throw new RepertoireUpsertError(`Projet introuvable dans le répertoire : ${project.number}`);
  const row = [...rows[index]];
  if (!options.forceOverwrite && INFO_COLUMNS_WITH_DATE.some((column) => cellText(row[column]) !== '')) {
    throw new RepertoireUpsertError(
      'Les colonnes B à E du répertoire contiennent déjà des informations. Utilisez « Mettre à jour » pour remplacer.',
    );
  }
  row[0] = project.number;
  row[1] = created;
  if (project.societe.trim()) row[2] = project.societe.trim();
  if (project.contact.trim()) row[3] = project.contact.trim();
  if (project.designation.trim()) row[4] = project.designation.trim();
  return { kind: 'update', sheetRow: start.row + index, values: row.slice(0, WRITABLE_WIDTH) };
}

/** An Excel table of the worksheet, as `WorkbookTable` on the desktop (0-based `rowIndex`). */
export type WorkbookTable = {
  id: string;
  rowIndex: number;
  rowCount: number;
  columnIndex: number;
  columnCount: number;
  showHeaders: boolean;
  showTotals: boolean;
};

/** Index of a 1-based sheet row inside the table body, or null when outside (one past the end is in). */
export function tableRowIndexForSheetRow(table: WorkbookTable, sheetRow: number): number | null {
  const dataStart = table.rowIndex + 1 + (table.showHeaders ? 1 : 0);
  const dataCount = Math.max(0, table.rowCount - (table.showHeaders ? 1 : 0) - (table.showTotals ? 1 : 0));
  const dataEnd = dataStart + dataCount - 1;
  if (sheetRow < dataStart || sheetRow > dataEnd + 1) return null;
  return sheetRow - dataStart;
}

/** Address of A:L of one row, for the inserted line. */
export function tableRowAddress(sheetRow: number): string {
  return `A${sheetRow}:L${sheetRow}`;
}

/**
 * Apply an upsert to an in-memory worksheet (the demo répertoire): rows below an
 * inserted line move down, as Excel's « insert, shift down ».
 */
export function applyUpsertPlan(
  sheet: { address: string; values: Cell[][]; text: string[][] },
  plan: UpsertPlan,
): { address: string; values: Cell[][]; text: string[][] } {
  const start = addressStart(sheet.address);
  const values = sheet.values.map((row) => [...row]);
  const text = sheet.text.map((row) => [...row]);
  const index = plan.sheetRow - start.row;
  const shown = plan.values.map((value) => (value === null ? '' : String(value)));
  if (plan.kind === 'insert') {
    values.splice(index, 0, [...plan.values]);
    text.splice(index, 0, shown);
  } else {
    values[index] = [...plan.values, ...(values[index] ?? []).slice(WRITABLE_WIDTH)];
    text[index] = [...shown, ...(text[index] ?? []).slice(WRITABLE_WIDTH)];
  }
  const local = sheet.address.includes('!') ? sheet.address.slice(0, sheet.address.lastIndexOf('!') + 1) : '';
  const width = Math.max(WRITABLE_WIDTH, ...values.map((row) => row.length));
  const lastColumn = String.fromCharCode(64 + Math.min(width, 26));
  return { address: `${local}A${start.row}:${lastColumn}${start.row + values.length - 1}`, values, text };
}
