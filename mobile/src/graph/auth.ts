// Connexion Microsoft (MSAL, redirection pleine page : fiable sur iOS en mode PWA).
import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
} from '@azure/msal-browser';

export const SCOPES = {
  base: ['User.Read', 'Files.ReadWrite.All'],
  planner: ['Tasks.ReadWrite', 'User.ReadBasic.All', 'GroupMember.Read.All'],
  mail: ['Mail.Send', 'Mail.ReadWrite'],
} as const;

export type ScopeSet = keyof typeof SCOPES;

const CLIENT_ID = import.meta.env.VITE_MS_CLIENT_ID || 'ced436ff-2be9-4792-8551-02e12351c6c9';
const TENANT = import.meta.env.VITE_MS_TENANT || 'organizations';

let pca: PublicClientApplication | null = null;

export async function initAuth(): Promise<AccountInfo | null> {
  pca = new PublicClientApplication({
    auth: {
      clientId: CLIENT_ID,
      authority: `https://login.microsoftonline.com/${TENANT}`,
      redirectUri: new URL('./', window.location.href).href,
    },
    cache: { cacheLocation: 'localStorage' },
  });
  await pca.initialize();
  const result = await pca.handleRedirectPromise();
  if (result?.account) pca.setActiveAccount(result.account);
  const account = pca.getActiveAccount() ?? pca.getAllAccounts()[0] ?? null;
  if (account) pca.setActiveAccount(account);
  return account;
}

function app(): PublicClientApplication {
  if (!pca) throw new Error('Connexion Microsoft non initialisée.');
  return pca;
}

export async function signIn(): Promise<void> {
  await app().loginRedirect({ scopes: [...SCOPES.base], prompt: 'select_account' });
}

export async function signOut(): Promise<void> {
  await app().logoutRedirect({ account: app().getActiveAccount() ?? undefined });
}

export function account(): AccountInfo | null {
  return pca?.getActiveAccount() ?? null;
}

/** Jeton pour un ensemble de droits ; redirige vers Microsoft si un consentement est nécessaire. */
export async function token(sets: ScopeSet[] = ['base'], forceRefresh = false): Promise<string> {
  const scopes = sets.flatMap((s) => [...SCOPES[s]]);
  const acc = account();
  if (!acc) throw new Error('Connectez-vous à votre compte Microsoft.');
  try {
    const r = await app().acquireTokenSilent({ scopes, account: acc, forceRefresh });
    return r.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await app().acquireTokenRedirect({ scopes, account: acc });
      throw new Error('Redirection vers Microsoft pour autoriser ProjectFlow…');
    }
    throw error;
  }
}

/** À appeler avant une opération longue, pour qu'une éventuelle redirection survienne avant toute écriture. */
export async function ensureScopes(sets: ScopeSet[]): Promise<void> {
  await token(sets);
}
