// Session courante : passerelles Microsoft Graph (compte réel) ou mémoire (démo).
import type { Gateways, UserInfo } from '../core/gateways';
import { loadSettings, type Settings } from '../core/settings';
import { memoryGateways, type MemoryStore } from '../demo/memory';
import { seedDemo } from '../demo/seed';
import { ensureScopes, initAuth, signIn, signOut, type ScopeSet } from '../graph/auth';
import { graphGateways } from '../graph';

export interface Session {
  gw: Gateways;
  me: UserInfo;
  settings: Settings;
  settingsKey: string;
  store?: MemoryStore;
  ensureScopes(sets: ScopeSet[]): Promise<void>;
  signOut(): Promise<void>;
}

const DEMO_FLAG = 'projectflow.demo';

export function isDemoRequested(): boolean {
  try {
    return new URLSearchParams(location.search).has('demo') || sessionStorage.getItem(DEMO_FLAG) === '1';
  } catch {
    return false;
  }
}

export function startDemo(): Session {
  try {
    sessionStorage.setItem(DEMO_FLAG, '1');
  } catch {
    // stockage indisponible : la démo dure le temps de la page
  }
  const { store, settings } = seedDemo();
  const gw = memoryGateways(store);
  return {
    gw,
    me: store.me,
    settings,
    settingsKey: 'projectflow.demo.settings',
    store,
    ensureScopes: async () => {},
    signOut: async () => {
      try {
        sessionStorage.removeItem(DEMO_FLAG);
      } catch {
        // rien à nettoyer
      }
      location.href = location.pathname;
    },
  };
}

/** Reprend une session Microsoft existante (retour de redirection compris), sinon null. */
export async function resumeGraphSession(): Promise<Session | null> {
  const account = await initAuth();
  if (!account) return null;
  const me = await graphGateways.me();
  const settings = loadSettings();
  return {
    gw: graphGateways,
    me,
    settings,
    settingsKey: 'projectflow.settings.v1',
    ensureScopes: (sets) => ensureScopes(['base', ...sets]),
    signOut,
  };
}

export { signIn };
