/**
 * The dossiers last opened on this phone (folder, fiche, Création, the dossier menus):
 * shown at the top of the Explorer tab and by the « Dossiers récents » widget.
 */

import type { ProjectInfo } from '@/lib/planning/quickEntry';
import { APP_LINK } from '@/lib/widgets/planningWidget';
import type { RecentWidgetProps } from '@/widgets/types';

export type RecentDossier = { number: string; designation: string; client: string };

/** Kept on the phone; the large widget shows them all. */
export const MAX_RECENT = 8;
export const EXPLORER_LINK = `${APP_LINK}explorer`;

/** `entry` first, without its earlier place; what it does not know is kept from before. */
export function withRecent(list: readonly RecentDossier[], entry: RecentDossier, max: number = MAX_RECENT): RecentDossier[] {
  const number = entry.number.trim();
  if (!number) return [...list];
  const before = list.find((item) => item.number === number);
  const merged: RecentDossier = {
    number,
    designation: entry.designation.trim() || before?.designation || '',
    client: entry.client.trim() || before?.client || '',
  };
  return [merged, ...list.filter((item) => item.number !== number)].slice(0, max);
}

/** The list as stored, whatever was written (a damaged value gives an empty list). */
export function parseRecent(text: string | null): RecentDossier[] {
  if (!text) return [];
  try {
    const value: unknown = JSON.parse(text);
    if (!Array.isArray(value)) return [];
    return value
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null && typeof item.number === 'string')
      .map((item) => ({
        number: String(item.number),
        designation: typeof item.designation === 'string' ? item.designation : '',
        client: typeof item.client === 'string' ? item.client : '',
      }))
      .slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

/** The répertoire's details where it knows the dossier (they may have changed since). */
export function withDirectory(list: readonly RecentDossier[], projects: ReadonlyMap<string, ProjectInfo>): RecentDossier[] {
  return list.map((item) => {
    const project = projects.get(item.number);
    if (!project) return item;
    return {
      number: item.number,
      designation: (project.designation ?? '').trim() || item.designation,
      client: (project.client ?? '').trim() || item.client,
    };
  });
}

export function recentWidgetProps(list: readonly RecentDossier[]): RecentWidgetProps {
  return {
    message: list.length ? '' : 'Les dossiers ouverts dans ProjectFlow apparaîtront ici.',
    items: list.map((item) => ({ ...item, url: `${APP_LINK}dossier?number=${encodeURIComponent(item.number)}` })),
    url: EXPLORER_LINK,
  };
}
