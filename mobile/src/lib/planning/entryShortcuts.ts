/**
 * Shortcuts of the quick entry on the phone: words added or removed with a touch (people,
 * days, durations), and the répertoire's dossiers proposed as the dossier is typed. They
 * only write the same words a user would type: the quick entry grammar reads them.
 */

import { foldText, type ProjectInfo } from '@/lib/planning/quickEntry';

export type ShortcutKind = 'person' | 'day' | 'duration' | 'flag';

export const SHORTCUT_DAYS = ['lun', 'mar', 'mer', 'jeu', 'ven'] as const;
export const SHORTCUT_DURATIONS = ['½j', '1j', '2j', '3j', '5j'] as const;

const DAY_RE = /^(lun|mar|mer|jeu|ven|sam|dim)(-(lun|mar|mer|jeu|ven|sam|dim))?$/i;
const DURATION_RE = /^(\d+(?:[.,]\d+)?|½|1\/2)j$/i;

function words(text: string): string[] {
  return text.split(/\s+/).filter(Boolean);
}

function same(left: string, right: string): boolean {
  return foldText(left) === foldText(right);
}

/** Whether `token` is in the entry, as its own word. */
export function hasToken(text: string, token: string): boolean {
  return words(text).some((word) => same(word, token));
}

/**
 * The entry with `token` added at the end, or removed when it is there. A day replaces
 * the day typed before, a duration the duration: one of each is what the grammar reads.
 */
export function toggleToken(text: string, token: string, kind: ShortcutKind): string {
  const current = words(text);
  if (current.some((word) => same(word, token))) {
    const kept = current.filter((word) => !same(word, token));
    return kept.length ? `${kept.join(' ')} ` : '';
  }
  const kept = current.filter((word) => {
    if (kind === 'day') return !DAY_RE.test(word);
    if (kind === 'duration') return !DURATION_RE.test(word);
    return true;
  });
  return `${[...kept, token].join(' ')} `;
}

/** The word being typed: the last one, when the entry does not end with a space. */
export function typedWord(text: string): string {
  if (!text || /\s$/.test(text)) return '';
  return words(text).pop() ?? '';
}

/** The entry with the word being typed replaced by `value`, ready for the next word. */
export function replaceTypedWord(text: string, value: string): string {
  return `${text.replace(/\S+$/, '')}${value} `;
}

/**
 * Dossiers of the répertoire for the word being typed: digits match the number, three
 * letters or more the client, the contact or the designation; latest dossiers first.
 */
export function dossierSuggestions(text: string, projects: Iterable<ProjectInfo>, limit = 4): ProjectInfo[] {
  const word = typedWord(text);
  if (word.length < 2) return [];
  const digits = /^\d+$/.test(word);
  if (!digits && word.length < 3) return [];
  const wanted = foldText(word);
  const found: ProjectInfo[] = [];
  for (const project of projects) {
    const id = project.number.split('-')[1] ?? '';
    const matches = digits
      ? id.startsWith(word) || project.number.replace(/-/g, '').includes(word)
      : [project.client, project.contact, project.designation].some((field) => foldText(field ?? '').includes(wanted));
    if (matches) found.push(project);
  }
  return found.sort((left, right) => (left.number < right.number ? 1 : left.number > right.number ? -1 : 0)).slice(0, limit);
}
