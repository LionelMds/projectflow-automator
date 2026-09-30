// Numéros de projet « YYYY-NNN » et sous-projets « YYYY-NNN-S » (repris de core/numero.py).

export class ProjectNumberError extends Error {}

const MAIN_RE = /^(\d{4})-(\d{3,})$/;
const PROJECT_RE = /^(\d{4})-(\d{3,})(?:-(\d+))?$/;

export interface ProjectNumber {
  year: number;
  projectId: string;
  subprojectId: string | null;
}

export function validateYear(year: string | number): number {
  const raw = String(year).trim();
  if (!/^\d{4}$/.test(raw)) throw new ProjectNumberError('L’année doit contenir exactement 4 chiffres.');
  return Number(raw);
}

export function validateProjectId(id: string | number): string {
  const raw = String(id).trim();
  if (!/^\d{3,}$/.test(raw)) throw new ProjectNumberError('L’ID projet doit contenir au moins 3 chiffres.');
  return raw;
}

export function validateSubprojectId(sub: string | number | null | undefined): string | null {
  if (sub == null) return null;
  const raw = String(sub).trim();
  if (raw === '') return null;
  if (!/^\d+$/.test(raw)) throw new ProjectNumberError('Le sous-projet doit contenir uniquement des chiffres.');
  if (Number(raw) <= 0) throw new ProjectNumberError('Le sous-projet doit être supérieur à zéro.');
  return raw;
}

export function makeNumber(year: string | number, id: string | number, sub?: string | number | null): ProjectNumber {
  return { year: validateYear(year), projectId: validateProjectId(id), subprojectId: validateSubprojectId(sub) };
}

export function formatNumber(n: ProjectNumber): string {
  return n.subprojectId ? `${n.year}-${n.projectId}-${n.subprojectId}` : `${n.year}-${n.projectId}`;
}

export function parseNumber(value: string): ProjectNumber {
  const m = PROJECT_RE.exec(value.trim());
  if (!m) throw new ProjectNumberError('Le numéro de projet doit respecter le format YYYY-NNN ou YYYY-NNN-S.');
  return { year: Number(m[1]), projectId: m[2], subprojectId: m[3] ?? null };
}

export function tryParseNumber(value: string): ProjectNumber | null {
  try {
    return parseNumber(value);
  } catch {
    return null;
  }
}

export function parentOf(n: ProjectNumber): ProjectNumber {
  return { year: n.year, projectId: n.projectId, subprojectId: null };
}

export function isMainNumber(value: string): boolean {
  return MAIN_RE.test(value.trim());
}

export function isSubproject(n: ProjectNumber): boolean {
  return n.subprojectId !== null;
}

/** Nom du dossier projet : toujours le numéro principal. */
export function projectFolderName(n: ProjectNumber): string {
  return formatNumber(parentOf(n));
}

/** Prochain numéro de sous-projet libre pour un parent, d'après les numéros connus. */
export function nextSubprojectId(numbers: string[], parent: ProjectNumber): string {
  const prefix = formatNumber(parentOf(parent)) + '-';
  let max = 1;
  for (const raw of numbers) {
    const v = raw.trim();
    if (!v.startsWith(prefix)) continue;
    const sub = Number(v.slice(prefix.length));
    if (Number.isInteger(sub) && sub > max) max = sub;
  }
  return String(max + 1);
}
