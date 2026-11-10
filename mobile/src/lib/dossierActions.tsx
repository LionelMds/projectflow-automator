/**
 * What can be done with a dossier from anywhere in the app, as the desktop's « Ouvrir le
 * dossier », « Ouvrir la fiche », « Voir dans Répertoire », « Charger dans Création »
 * (`ui/planning_controller.py` `bar_menu`, `ui/planning/detail.py` `DOSSIER_ACTIONS`).
 */

import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { CalendarRange, ClipboardCopy, FilePlus, FileSpreadsheet, FolderOpen, Table, Zap } from 'lucide-react-native';
import { useCallback, useMemo } from 'react';

import type { SheetAction } from '@/components/ActionSheet';
import { useProjectsBackend } from '@/lib/projects/backend';
import { findDossier } from '@/lib/projects/creation';
import { parseProjectNumber } from '@/lib/projects/numbers';
import { openWorkbook } from '@/lib/projects/openFile';
import { useRecentDossiers } from '@/lib/recent/RecentContext';
import { allowedActions } from '@/lib/restriction/restriction';
import { useRestriction } from '@/lib/restriction/RestrictionContext';
import { useSettings } from '@/lib/settings';
import { iconStroke } from '@/theme/tokens';

export type DossierAction = 'creation' | 'folder' | 'fiche' | 'repertoire' | 'planning' | 'task' | 'copy';

export function useDossierActions() {
  const { settings } = useSettings();
  const { drive } = useProjectsBackend();
  const { remember } = useRecentDossiers();
  // « Accès limité »: only the dossier's folder, its planning and its number.
  const { restricted } = useRestriction();

  const locate = useCallback(
    async (number: string) => {
      if (!settings.demo && !settings.racine) throw new Error('Collez la racine projets dans les paramètres.');
      return findDossier(drive, settings.racine, parseProjectNumber(number));
    },
    [drive, settings.demo, settings.racine],
  );

  const openFiche = useCallback(
    async (number: string) => {
      const { folder, fiche, folderPath } = await locate(number);
      if (!folder) throw new Error(`Dossier introuvable : ${folderPath}`);
      if (!fiche) throw new Error(`Aucune fiche Excel dans ${folderPath}`);
      // In the Excel app, edited in place on OneDrive (Excel for the web without the app).
      await openWorkbook(fiche);
      remember(number);
    },
    [locate, remember],
  );

  // `at` makes each request new, so that asking twice for the same dossier works twice.
  const run = useMemo(
    () => ({
      creation: (number: string) => router.navigate({ pathname: '/', params: { charger: number, at: String(Date.now()) } }),
      // The dossier's folder in the app's explorer: iOS and Android cannot open a OneDrive
      // folder of their Files app from another app.
      folder: (number: string) => router.push({ pathname: '/dossier', params: { number } }),
      fiche: openFiche,
      repertoire: (number: string) =>
        router.navigate({ pathname: '/repertoire', params: { dossier: number, at: String(Date.now()) } }),
      planning: (number: string) => router.navigate({ pathname: '/planning', params: { dossier: number, at: String(Date.now()) } }),
      task: (number: string) => router.push({ pathname: '/saisie', params: { text: `${number} ` } }),
      copy: (number: string) => Clipboard.setStringAsync(number).then(() => undefined),
    }),
    [openFiche],
  );

  /** The sheet rows for `number`, in the order asked (the desktop's order by default). */
  const actions = useCallback(
    (number: string, keys: DossierAction[]): SheetAction[] => {
      const rows: Record<DossierAction, Omit<SheetAction, 'onPress'>> = {
        creation: { key: 'creation', label: 'Charger dans Création', icon: (c) => <FilePlus size={20} color={c} strokeWidth={iconStroke} /> },
        folder: {
          key: 'folder',
          label: 'Ouvrir le dossier',
          detail: 'Fichiers du dossier',
          icon: (c) => <FolderOpen size={20} color={c} strokeWidth={iconStroke} />,
        },
        fiche: {
          key: 'fiche',
          label: 'Ouvrir la fiche',
          detail: 'Excel',
          icon: (c) => <FileSpreadsheet size={20} color={c} strokeWidth={iconStroke} />,
        },
        repertoire: { key: 'repertoire', label: 'Voir dans Répertoire', icon: (c) => <Table size={20} color={c} strokeWidth={iconStroke} /> },
        planning: { key: 'planning', label: 'Voir dans le planning', icon: (c) => <CalendarRange size={20} color={c} strokeWidth={iconStroke} /> },
        task: { key: 'task', label: 'Nouvelle tâche', detail: 'Saisie rapide', icon: (c) => <Zap size={20} color={c} strokeWidth={iconStroke} /> },
        copy: { key: 'copy', label: 'Copier le numéro', icon: (c) => <ClipboardCopy size={20} color={c} strokeWidth={iconStroke} /> },
      };
      return allowedActions(keys, restricted).map((key) => ({ ...rows[key], onPress: () => run[key](number) }));
    },
    [run, restricted],
  );

  return { actions, ...run };
}
