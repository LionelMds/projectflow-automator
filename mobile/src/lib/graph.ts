/** Minimal Microsoft Graph client: JSON in, JSON out, French errors for the screens. */

export const GRAPH_ROOT = 'https://graph.microsoft.com/v1.0';

export type TokenSource = () => Promise<string>;

export class GraphError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = '',
  ) {
    super(message);
  }
}

function describe(status: number, code: string, detail: string): string {
  if (status === 401) return 'Session Microsoft expirée. Reconnectez-vous dans les paramètres.';
  if (status === 403) return 'Accès refusé par Microsoft (403). Vérifiez le compte connecté.';
  if (status === 404) return 'Élément introuvable sur OneDrive / SharePoint (404).';
  if (status === 409) return 'Conflit : cet élément existe déjà ou a changé (409).';
  if (status === 412) return 'Cet élément a été modifié ailleurs entre-temps (412). Actualisez puis recommencez.';
  if (status === 423) return 'Le fichier est verrouillé pour le moment (423). Réessayez dans un instant.';
  if (status === 429) return 'Microsoft limite les requêtes (429). Réessayez dans un instant.';
  if (status >= 500) return `Microsoft ne répond pas correctement (${status}). Réessayez plus tard.`;
  return detail || code || `Erreur Microsoft Graph (${status}).`;
}

export type GraphInit = {
  method?: string;
  /** JSON body (serialised here), unless `raw` is given. */
  body?: unknown;
  /** Body sent as is (file content), with `contentType`. */
  raw?: string | Blob | ArrayBuffer | Uint8Array;
  contentType?: string;
  headers?: Record<string, string>;
  /** False for a pre-authenticated address (download, copy monitor): no bearer token. */
  authenticated?: boolean;
};

const OFFLINE_MESSAGE = 'Réseau indisponible : Microsoft est injoignable pour le moment.';

/** One request; a failed status becomes a `GraphError` with a French message. */
export async function graphFetch(token: TokenSource, path: string, init: GraphInit = {}): Promise<Response> {
  const url = path.startsWith('https://') ? path : `${GRAPH_ROOT}${path}`;
  const headers: Record<string, string> = {};
  if (init.authenticated !== false) headers.Authorization = `Bearer ${await token()}`;
  let body: BodyInit | undefined;
  if (init.raw !== undefined) {
    body = init.raw as BodyInit;
    headers['Content-Type'] = init.contentType ?? 'application/octet-stream';
  } else if (init.body !== undefined) {
    body = JSON.stringify(init.body);
    headers['Content-Type'] = 'application/json';
  }
  let response: Response;
  try {
    response = await fetch(url, { method: init.method ?? 'GET', headers: { ...headers, ...init.headers }, body });
  } catch {
    throw new GraphError(OFFLINE_MESSAGE, 0, 'offline');
  }
  if (!response.ok) {
    let code = '';
    let detail = '';
    try {
      const payload = (await response.json()) as { error?: { code?: string; message?: string } };
      code = payload.error?.code ?? '';
      detail = payload.error?.message ?? '';
    } catch {
      // No JSON body.
    }
    throw new GraphError(describe(response.status, code, detail), response.status, code);
  }
  return response;
}

export async function graphRequest<T>(token: TokenSource, path: string, init: GraphInit = {}): Promise<T> {
  const response = await graphFetch(token, path, init);
  if (response.status === 204 || response.status === 202) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** Text of a file (a download address or a `/content` path), without a UTF-8 BOM. */
export async function graphText(token: TokenSource, path: string, authenticated = true): Promise<string> {
  const response = await graphFetch(token, path, { authenticated });
  return (await response.text()).replace(/^﻿/, '');
}

/** Encode each segment of a OneDrive path for `/root:/<path>:`. */
export function encodeDrivePath(path: string): string {
  return path
    .split(/[\\/]+/)
    .filter((segment) => segment.length > 0)
    .map((segment) => encodeURIComponent(segment))
    .join('/');
}

/**
 * Path in the signed-in OneDrive from a setting: a path copied from the desktop
 * (`E:\OneDrive - Balz Metal Sa\Clients`) keeps what follows the OneDrive folder.
 */
export function drivePathFromSetting(location: string): string {
  const segments = location.trim().split(/[\\/]+/).filter(Boolean);
  const oneDrive = segments.findIndex((segment) => /^onedrive( - .+)?$/i.test(segment));
  const kept = oneDrive >= 0 ? segments.slice(oneDrive + 1) : segments.filter((segment) => !/^[a-z]:$/i.test(segment));
  return kept.join('/');
}

async function optionalRequest<T>(token: TokenSource, path: string): Promise<T | null> {
  try {
    return await graphRequest<T>(token, path);
  } catch (exc) {
    if (exc instanceof GraphError && exc.status === 404) return null;
    throw exc;
  }
}

/**
 * An item of the signed-in OneDrive by its path, as the desktop resolves it (`graph/onedrive.py`):
 * directly, then through a shortcut at the root (a shared folder added to « Mes fichiers »),
 * reading the rest of the path in the shared drive. Null when neither exists.
 */
export async function driveItemByPath<T>(token: TokenSource, path: string, select: string): Promise<T | null> {
  const segments = path.split(/[\\/]+/).filter(Boolean);
  const direct = await optionalRequest<T>(token, `/me/drive/root:/${encodeDrivePath(path)}:?${select}`);
  if (direct !== null || segments.length < 2) return direct;
  const shortcut = await optionalRequest<{ remoteItem?: { id?: string; parentReference?: { driveId?: string } } }>(
    token,
    `/me/drive/root:/${encodeURIComponent(segments[0])}:?$select=id,remoteItem`,
  );
  const driveId = shortcut?.remoteItem?.parentReference?.driveId;
  const itemId = shortcut?.remoteItem?.id;
  if (!driveId || !itemId) return null;
  const rest = encodeDrivePath(segments.slice(1).join('/'));
  return optionalRequest<T>(
    token,
    `/drives/${encodeURIComponent(driveId)}/items/${encodeURIComponent(itemId)}:/${rest}:?${select}`,
  );
}
