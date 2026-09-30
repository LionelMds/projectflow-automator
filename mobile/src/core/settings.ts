// Paramètres locaux au téléphone (équivalent de config.py de l'application de bureau).

export interface ItemRef {
  /** Ce que l'utilisateur a saisi : lien de partage ou chemin dans « Mon OneDrive ». */
  reference: string;
  driveId: string;
  id: string;
  name: string;
  webUrl: string;
}

export interface Settings {
  initials: string;
  racine: ItemRef | null;
  reference: ItemRef | null;
  repertoire: ItemRef | null;
  outlook: {
    enabled: boolean;
    /** Un chemin par ligne, dossiers séparés par « / » ; jetons [YYYY] [XXXX] [NUMERO] [DESIGNATION] [PROJECT_FOLDER]. */
    arborescence: string;
  };
  planner: {
    enabled: boolean;
    planId: string;
    planName: string;
    bucketId: string;
    bucketName: string;
    dueDays: number;
  };
  cad: {
    solidworks: ItemRef | null;
    autocad: ItemRef | null;
    subfolder: string;
  };
  printAfterCreate: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  initials: '',
  racine: null,
  reference: null,
  repertoire: null,
  outlook: { enabled: false, arborescence: '[YYYY]/[PROJECT_FOLDER]' },
  planner: { enabled: false, planId: '', planName: '', bucketId: '', bucketName: '', dueDays: 14 },
  cad: { solidworks: null, autocad: null, subfolder: 'Plans/Plan d’exécution' },
  printAfterCreate: false,
};

const KEY = 'projectflow.settings.v1';

export function loadSettings(storageKey = KEY): Settings {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return structuredClone(DEFAULT_SETTINGS);
    const data = JSON.parse(raw) as Partial<Settings>;
    return {
      ...DEFAULT_SETTINGS,
      ...data,
      outlook: { ...DEFAULT_SETTINGS.outlook, ...data.outlook },
      planner: { ...DEFAULT_SETTINGS.planner, ...data.planner },
      cad: { ...DEFAULT_SETTINGS.cad, ...data.cad },
    };
  } catch {
    return structuredClone(DEFAULT_SETTINGS);
  }
}

export function saveSettings(settings: Settings, storageKey = KEY): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(settings));
  } catch {
    // Navigation privée : les paramètres restent en mémoire pour la session.
  }
}

export function isConfigured(s: Settings): boolean {
  return !!(s.racine && s.reference && s.repertoire);
}

export function outlookPaths(arborescence: string): string[][] {
  return arborescence
    .split('\n')
    .map((line) => line.split('/').map((p) => p.trim()).filter(Boolean))
    .filter((p) => p.length > 0);
}

export function cadSubfolderParts(subfolder: string): string[] {
  const parts = subfolder.replace(/\\/g, '/').split('/').map((p) => p.trim()).filter((p) => p && p !== '.');
  if (parts.some((p) => p === '..' || p.includes(':'))) throw new Error('Le sous-dossier CAO doit être un chemin relatif au dossier projet.');
  return parts;
}
