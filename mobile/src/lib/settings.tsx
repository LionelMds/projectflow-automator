import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { readItem, writeItem } from '@/lib/storage';

/** Same default as the desktop (`DEFAULT_ONEDRIVE_FOLDER`). */
export const DEFAULT_PLANNING_FOLDER = 'ProjectFlow Planning';
/** Same default as the desktop (`PlannerConfig.due_days`). */
export const DEFAULT_PLANNER_DUE_DAYS = 7;

export type Settings = {
  /** OneDrive/SharePoint link of the répertoire chantier workbook, or its path in OneDrive. */
  repertoire: string;
  /** Folder of the shared planning, at the root of the signed-in account's OneDrive. */
  planningFolder: string;
  /** User initials, written as the author of changes (« LM ») and in C9 of the fiche. */
  initials: string;
  /** « Mode démo »: sample data in memory, nothing read from or written to Microsoft. */
  demo: boolean;
  /** « Racine projets »: folder holding `<year>/<YYYY-NNNN>` (link, or path in OneDrive). */
  racine: string;
  /** « Dossier de référence »: copied as a new project folder (link, or path in OneDrive). */
  reference: string;
  /** Microsoft Planner, as `PlannerConfig` on the desktop. */
  plannerEnabled: boolean;
  /** Plan id, read from the Planner/Teams link pasted in the settings. */
  plannerPlanId: string;
  plannerBucketId: string;
  plannerBucketName: string;
  plannerDueDays: number;
  /** Members preselected in the creation form: the connected account may be shared. */
  plannerAssigneeIds: string[];
  plannerAssigneeNames: string[];
};

export const DEFAULT_SETTINGS: Settings = {
  repertoire: '',
  planningFolder: DEFAULT_PLANNING_FOLDER,
  initials: '',
  demo: false,
  racine: '',
  reference: '',
  plannerEnabled: false,
  plannerPlanId: '',
  plannerBucketId: '',
  plannerBucketName: '',
  plannerDueDays: DEFAULT_PLANNER_DUE_DAYS,
  plannerAssigneeIds: [],
  plannerAssigneeNames: [],
};
const STORAGE_KEY = 'projectflow.settings';

type SettingsContextValue = {
  settings: Settings;
  loaded: boolean;
  save: (next: Settings) => Promise<void>;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

/** Days between 0 and 365, as the desktop validates them; 7 otherwise. */
export function dueDays(value: unknown): number {
  const number = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN;
  if (!Number.isInteger(number) || number < 0 || number > 365) return DEFAULT_PLANNER_DUE_DAYS;
  return number;
}

/** Stored settings; JSON written by an earlier version (fewer fields) is accepted as is. */
export function parseSettings(raw: string | null): Settings {
  if (!raw) return DEFAULT_SETTINGS;
  try {
    const data = JSON.parse(raw) as Partial<Record<keyof Settings, unknown>>;
    return {
      repertoire: text(data.repertoire),
      planningFolder:
        typeof data.planningFolder === 'string' && data.planningFolder.trim()
          ? data.planningFolder
          : DEFAULT_PLANNING_FOLDER,
      initials: text(data.initials),
      demo: data.demo === true,
      racine: text(data.racine),
      reference: text(data.reference),
      plannerEnabled: data.plannerEnabled === true,
      plannerPlanId: text(data.plannerPlanId),
      plannerBucketId: text(data.plannerBucketId),
      plannerBucketName: text(data.plannerBucketName),
      plannerDueDays: dueDays(data.plannerDueDays),
      plannerAssigneeIds: strings(data.plannerAssigneeIds),
      plannerAssigneeNames: strings(data.plannerAssigneeNames),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function cleanSettings(next: Settings): Settings {
  const ids: string[] = [];
  const names: string[] = [];
  next.plannerAssigneeIds.forEach((id, index) => {
    if (id.trim() && !ids.includes(id.trim())) {
      ids.push(id.trim());
      names.push((next.plannerAssigneeNames[index] ?? '').trim());
    }
  });
  return {
    repertoire: next.repertoire.trim(),
    planningFolder: next.planningFolder.trim() || DEFAULT_PLANNING_FOLDER,
    initials: next.initials.trim().toUpperCase(),
    demo: next.demo,
    racine: next.racine.trim(),
    reference: next.reference.trim(),
    plannerEnabled: next.plannerEnabled,
    plannerPlanId: next.plannerPlanId.trim(),
    plannerBucketId: next.plannerBucketId.trim(),
    plannerBucketName: next.plannerBucketName.trim(),
    plannerDueDays: dueDays(next.plannerDueDays),
    plannerAssigneeIds: ids,
    plannerAssigneeNames: names,
  };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    readItem(STORAGE_KEY)
      .then((raw) => {
        if (alive) setSettings(parseSettings(raw));
      })
      .finally(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  const save = useCallback(async (next: Settings) => {
    const cleaned = cleanSettings(next);
    setSettings(cleaned);
    await writeItem(STORAGE_KEY, JSON.stringify(cleaned));
  }, []);

  const value = useMemo(() => ({ settings, loaded, save }), [settings, loaded, save]);
  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsContextValue {
  const context = useContext(SettingsContext);
  if (!context) throw new Error('useSettings hors de SettingsProvider');
  return context;
}
