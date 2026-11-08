import * as AuthSession from 'expo-auth-session';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { REDIRECT_URI } from '@/lib/authRedirect';
import { jwtClaims } from '@/lib/base64';
import { readItem, removeItem, writeItem } from '@/lib/storage';

/**
 * Same Microsoft Entra app registration, tenant and redirect address as the desktop
 * (`application_settings.py`, `auth/msal_client.py`, `auth/browser_sign_in.py`): the
 * registration is single-tenant, so /common is refused. Nothing has to be added to it.
 */
export const TENANT_ID = 'e7cf482b-7ac5-41e7-855c-507af4329317';
export const CLIENT_ID = 'ced436ff-2be9-4792-8551-02e12351c6c9';
/** Files for the répertoire, the planning and the project folders; Planner as on the desktop (`PLANNER_GRAPH_SCOPES`). */
export const SCOPES = [
  'openid',
  'profile',
  'offline_access',
  'User.Read',
  'Files.ReadWrite.All',
  'Tasks.ReadWrite',
  'User.ReadBasic.All',
  'GroupMember.Read.All',
];
/** The in-app sign-in page needs the phone: the browser version only has the demo mode. */
const CAN_SIGN_IN = Platform.OS !== 'web';

const AUTHORITY = `https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0`;
const DISCOVERY: AuthSession.DiscoveryDocument = {
  authorizationEndpoint: `${AUTHORITY}/authorize`,
  tokenEndpoint: `${AUTHORITY}/token`,
  endSessionEndpoint: `${AUTHORITY}/logout`,
};
const REFRESH_KEY = 'projectflow.refresh-token';
const ACCOUNT_KEY = 'projectflow.account';
/** Renew the access token this long before Microsoft would refuse it. */
const EXPIRY_MARGIN_MS = 2 * 60 * 1000;

export type Account = { name: string; username: string };
type Status = 'loading' | 'signedOut' | 'signedIn';

export class SignInRequiredError extends Error {
  constructor() {
    super('Reconnexion Microsoft requise. Ouvrez les paramètres et cliquez « Se connecter à Microsoft ».');
  }
}

type AuthValue = {
  status: Status;
  account: Account | null;
  error: string;
  canSignIn: boolean;
  /** Preparing the sign-in page or exchanging Microsoft's reply. */
  busy: boolean;
  /** Microsoft's sign-in page to show (`components/MicrosoftSignIn`), null when none is open. */
  signInPage: string | null;
  signIn: () => Promise<void>;
  /** Microsoft's reply to http://localhost, caught by the sign-in page. */
  finishSignIn: (returnUrl: string) => Promise<void>;
  cancelSignIn: () => void;
  signOut: () => Promise<void>;
  getAccessToken: () => Promise<string>;
};

const AuthContext = createContext<AuthValue | null>(null);

function accountFrom(idToken: string | undefined): Account | null {
  if (!idToken) return null;
  const claims = jwtClaims(idToken);
  const username = String(claims.preferred_username ?? claims.email ?? '');
  const name = String(claims.name ?? username);
  return username || name ? { name, username } : null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState('');
  const access = useRef<{ token: string; expiresAt: number } | null>(null);
  const refreshToken = useRef<string | null>(null);
  const pending = useRef<Promise<string> | null>(null);
  const signInRequest = useRef<AuthSession.AuthRequest | null>(null);
  const [signInPage, setSignInPage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([readItem(REFRESH_KEY), readItem(ACCOUNT_KEY)])
      .then(([token, rawAccount]) => {
        if (!alive) return;
        refreshToken.current = token;
        if (rawAccount) {
          try {
            setAccount(JSON.parse(rawAccount) as Account);
          } catch {
            setAccount(null);
          }
        }
        setStatus(token ? 'signedIn' : 'signedOut');
      })
      .catch(() => {
        if (alive) setStatus('signedOut');
      });
    return () => {
      alive = false;
    };
  }, []);

  const keep = useCallback(async (response: AuthSession.TokenResponse) => {
    const lifetime = (response.expiresIn ?? 3600) * 1000;
    access.current = { token: response.accessToken, expiresAt: Date.now() + lifetime };
    if (response.refreshToken) {
      refreshToken.current = response.refreshToken;
      await writeItem(REFRESH_KEY, response.refreshToken);
    }
    const signedIn = accountFrom(response.idToken);
    if (signedIn) {
      setAccount(signedIn);
      await writeItem(ACCOUNT_KEY, JSON.stringify(signedIn));
    }
  }, []);

  const forget = useCallback(async () => {
    access.current = null;
    refreshToken.current = null;
    setAccount(null);
    setStatus('signedOut');
    await Promise.all([removeItem(REFRESH_KEY), removeItem(ACCOUNT_KEY)]);
  }, []);

  /** Prepare Microsoft's page (PKCE and state, as the desktop's MSAL does); the screen shows it. */
  const signIn = useCallback(async () => {
    if (!CAN_SIGN_IN) return;
    setError('');
    setBusy(true);
    try {
      const request = new AuthSession.AuthRequest({
        clientId: CLIENT_ID,
        redirectUri: REDIRECT_URI,
        scopes: SCOPES,
        prompt: AuthSession.Prompt.SelectAccount,
        usePKCE: true,
      });
      const url = await request.makeAuthUrlAsync(DISCOVERY);
      signInRequest.current = request;
      setSignInPage(url);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Connexion Microsoft impossible.');
    } finally {
      setBusy(false);
    }
  }, []);

  const cancelSignIn = useCallback(() => {
    signInRequest.current = null;
    setSignInPage(null);
  }, []);

  /** Check Microsoft's reply against the request (state), then exchange its code for tokens. */
  const finishSignIn = useCallback(
    async (returnUrl: string) => {
      const request = signInRequest.current;
      signInRequest.current = null;
      setSignInPage(null);
      if (!request) return;
      const result = request.parseReturnUrl(returnUrl);
      if (result.type === 'error') {
        setError(result.error?.description ?? result.error?.message ?? 'Connexion Microsoft refusée.');
        return;
      }
      if (result.type !== 'success' || !result.params.code) {
        setError('Connexion Microsoft interrompue.');
        return;
      }
      setBusy(true);
      try {
        const response = await AuthSession.exchangeCodeAsync(
          {
            clientId: CLIENT_ID,
            code: result.params.code,
            redirectUri: REDIRECT_URI,
            scopes: SCOPES,
            extraParams: request.codeVerifier ? { code_verifier: request.codeVerifier } : undefined,
          },
          DISCOVERY,
        );
        await keep(response);
        setStatus('signedIn');
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : 'Connexion Microsoft impossible.');
      } finally {
        setBusy(false);
      }
    },
    [keep],
  );

  const getAccessToken = useCallback(async () => {
    const current = access.current;
    if (current && current.expiresAt - EXPIRY_MARGIN_MS > Date.now()) return current.token;
    if (pending.current) return pending.current;
    const token = refreshToken.current;
    if (!token) throw new SignInRequiredError();
    pending.current = (async () => {
      try {
        const response = await AuthSession.refreshAsync(
          { clientId: CLIENT_ID, refreshToken: token, scopes: SCOPES },
          DISCOVERY,
        );
        await keep(response);
        return response.accessToken;
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : '';
        // A refused refresh token (expired, revoked, password changed) needs a new sign-in.
        if (/invalid_grant|interaction_required|consent_required|AADSTS(50173|700082|70008|65001)/i.test(message)) {
          await forget();
          throw new SignInRequiredError();
        }
        throw exc;
      } finally {
        pending.current = null;
      }
    })();
    return pending.current;
  }, [keep, forget]);

  const value = useMemo<AuthValue>(
    () => ({
      status,
      account,
      error,
      canSignIn: CAN_SIGN_IN,
      busy,
      signInPage,
      signIn,
      finishSignIn,
      cancelSignIn,
      signOut: forget,
      getAccessToken,
    }),
    [status, account, error, busy, signInPage, signIn, finishSignIn, cancelSignIn, forget, getAccessToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth hors de AuthProvider');
  return context;
}
