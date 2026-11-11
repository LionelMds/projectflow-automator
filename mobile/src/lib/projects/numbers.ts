/**
 * Dossier numbers, ported from `core/numero.py`: « 2026-4995 » for a project,
 * « 2026-4995-2 » for a subproject, written by hand « 2024-4493-A1 » or « 2026-5102-REV03 ».
 */

export class ProjectNumberError extends Error {}

export type ProjectNumber = {
  year: number;
  projectId: string;
  /** « 2 », or a suffix written by hand in the répertoire (« A1 », « REV03 »). */
  subprojectId: string | null;
};

const MAIN_PROJECT_RE = /^(\d{4})-(\d{3,})$/;
const PROJECT_RE = /^(\d{4})-(\d{3,})(?:-([A-Z0-9]+(?:-[A-Z0-9]+)*))?$/;
const DASHES = /[‐‑‒–—―−]/g;
const LOOSE_RE = /^(\d{4})\s*-\s*(\d{3,})(?:\s*[-_/.\s]\s*(.+))?$/;
const SUFFIX_PARTS_RE = /[^A-Za-z0-9]+/;

/**
 * The number as written in the répertoire, whatever was typed around it:
 * « 2024 - 4493 a1 » reads as `2024-4493-A1`, « 2026-118-02 » as `2026-118-2`.
 * Text that is not a number at all is only stripped.
 */
export function canonicalProjectNumber(value: unknown): string {
  const text = String(value ?? '')
    .replace(DASHES, '-')
    .split(/\s+/)
    .filter(Boolean)
    .join(' ');
  const match = LOOSE_RE.exec(text);
  if (!match) return text;
  const number = `${match[1]}-${match[2]}`;
  const rest = match[3];
  if (!rest) return number;
  let parts = rest
    .split(SUFFIX_PARTS_RE)
    .filter(Boolean)
    .map((part) => part.toUpperCase());
  if (parts.length === 0) return number;
  if (parts.length === 1 && /^\d+$/.test(parts[0])) parts = [String(Number.parseInt(parts[0], 10))];
  return `${number}-${parts.join('-')}`;
}

export function isProjectNumber(value: unknown): boolean {
  return PROJECT_RE.test(canonicalProjectNumber(value));
}

export function validateYear(year: number | string): number {
  const raw = String(year).trim();
  if (!/^\d{4}$/.test(raw)) throw new ProjectNumberError("L'année doit contenir exactement 4 chiffres.");
  return Number(raw);
}

export function validateProjectId(projectId: number | string): string {
  const raw = String(projectId).trim();
  if (!/^\d{3,}$/.test(raw)) throw new ProjectNumberError("L'ID projet doit contenir au moins 3 chiffres.");
  return raw;
}

export function validateSubprojectId(subprojectId: number | string | null | undefined): string | null {
  if (subprojectId === null || subprojectId === undefined) return null;
  const raw = String(subprojectId).trim();
  if (raw === '') return null;
  if (!/^\d+$/.test(raw)) throw new ProjectNumberError('Le sous-projet doit contenir uniquement des chiffres.');
  if (Number(raw) <= 0) throw new ProjectNumberError('Le sous-projet doit être supérieur à zéro.');
  return raw;
}

export function formatProjectNumber(
  year: number | string,
  projectId: number | string,
  subprojectId?: number | string | null,
): string {
  const validYear = validateYear(year);
  const validId = validateProjectId(projectId);
  const validSub = validateSubprojectId(subprojectId);
  return validSub === null ? `${validYear}-${validId}` : `${validYear}-${validId}-${validSub}`;
}

export function parseProjectNumber(value: string): ProjectNumber {
  const match = PROJECT_RE.exec(canonicalProjectNumber(value));
  if (!match) {
    throw new ProjectNumberError('Le numéro de projet doit respecter le format YYYY-NNN ou YYYY-NNN-S.');
  }
  return { year: Number(match[1]), projectId: match[2], subprojectId: match[3] ?? null };
}

/** The number as text (`str(ProjectNumber)` on the desktop). */
export function numberText(number: ProjectNumber): string {
  const sub = number.subprojectId;
  if (sub && !/^\d+$/.test(sub)) return `${formatProjectNumber(number.year, number.projectId)}-${sub}`;
  return formatProjectNumber(number.year, number.projectId, sub);
}

export function isSubproject(number: ProjectNumber): boolean {
  return number.subprojectId !== null;
}

export function parentNumber(number: ProjectNumber): ProjectNumber {
  return { year: number.year, projectId: number.projectId, subprojectId: null };
}

export function isMainProjectNumber(value: string): boolean {
  return MAIN_PROJECT_RE.test(value.trim());
}

/** Folder of a dossier under `<racine>/<year>`: a subproject lives in its parent's. */
export function projectFolderName(number: ProjectNumber | string): string {
  const parsed = typeof number === 'string' ? parseProjectNumber(number) : number;
  return numberText(parentNumber(parsed));
}

export function nextProjectId(numbers: readonly string[], year: number | string): string | null {
  const validYear = validateYear(year);
  const candidates: number[] = [];
  for (const number of numbers) {
    const match = MAIN_PROJECT_RE.exec(number.trim());
    if (match && Number(match[1]) === validYear) candidates.push(Number(match[2]));
  }
  return candidates.length ? String(Math.max(...candidates) + 1) : null;
}

/** The number of the creation form (`format_project_number` then `parse_project_number`). */
export function parseFormNumber(year: string, projectId: string, subprojectId: string): ProjectNumber {
  return parseProjectNumber(formatProjectNumber(year, projectId, subprojectId));
}
