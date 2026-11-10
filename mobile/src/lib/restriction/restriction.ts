/**
 * « Accès limité »: a phone handed to someone who only needs the dossiers' Photos and Plans and
 * the planning, to look at. A password set in Paramètres turns it on and the same password
 * turns it off. It hides parts of the app on this phone only: what the Microsoft account
 * connected on the phone can reach does not change.
 */

import type { DossierAction } from '@/lib/dossierActions';
import { foldText } from '@/lib/planning/quickEntry';

/** The only folders shown at the root of a dossier (`Clients/<year>/<YYYY-NNNN>/…`). */
export const OPEN_FOLDERS = ['Photos', 'Plans'] as const;

/** The dossier actions kept: its folder, its tasks in the planning, its number. */
export const OPEN_DOSSIER_ACTIONS: readonly DossierAction[] = ['folder', 'planning', 'copy'];

export const RESTRICTED_PLANNING = 'Accès limité : le planning se consulte seulement.';

export const MIN_PASSWORD_LENGTH = 4;

/** The stored lock: a random salt and the SHA-256 of salt + password, never the password. */
export type Lock = { salt: string; hash: string };

export type Digest = (text: string) => Promise<string>;

const FOLDED_OPEN = OPEN_FOLDERS.map((name) => foldText(name).trim());

/** True for a folder named Photos or Plans (any case, accents, spaces around). */
export function isOpenFolder(item: { name: string; folder: boolean }): boolean {
  return item.folder && FOLDED_OPEN.includes(foldText(item.name).trim());
}

/** The dossier actions allowed: all of them, or the open ones once access is limited. */
export function allowedActions(keys: readonly DossierAction[], restricted: boolean): DossierAction[] {
  return restricted ? keys.filter((key) => OPEN_DOSSIER_ACTIONS.includes(key)) : [...keys];
}

/** Why a new password cannot be used, or '' when it can. */
export function passwordProblem(password: string, confirmation: string): string {
  if (password.length < MIN_PASSWORD_LENGTH) return `Choisissez un mot de passe d’au moins ${MIN_PASSWORD_LENGTH} caractères.`;
  if (password !== confirmation) return 'Les deux mots de passe ne sont pas les mêmes.';
  return '';
}

export async function makeLock(password: string, salt: string, digest: Digest): Promise<Lock> {
  return { salt, hash: await digest(`${salt}:${password}`) };
}

export async function passwordMatches(lock: Lock, password: string, digest: Digest): Promise<boolean> {
  return (await digest(`${lock.salt}:${password}`)) === lock.hash;
}

/** The stored lock, or null (none stored, or unreadable: the phone is then not limited). */
export function parseLock(raw: string | null): Lock | null {
  if (!raw) return null;
  try {
    const data = JSON.parse(raw) as Partial<Record<keyof Lock, unknown>>;
    if (typeof data.salt !== 'string' || typeof data.hash !== 'string' || !data.salt || !data.hash) return null;
    return { salt: data.salt, hash: data.hash };
  } catch {
    return null;
  }
}
