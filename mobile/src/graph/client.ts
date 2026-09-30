// Client HTTP Microsoft Graph : jetons, reprises prudentes et messages d'erreur en français
// (repris de graph/client.py).
import { token, type ScopeSet } from './auth';

export const GRAPH = 'https://graph.microsoft.com/v1.0';
const RETRY = new Set([429, 500, 502, 503, 504]);
const CONFLICT = new Set(['accessconflict', 'conflictuncategorized', 'invalidsessionaccessconflict']);

export class GraphError extends Error {
  constructor(
    message: string,
    public status = 0,
    public code = '',
    public innerCode = '',
  ) {
    super(message);
  }
  get invalidSession(): boolean {
    return [this.code, this.innerCode].some((c) => c.toLowerCase().startsWith('invalidsession'));
  }
}

export interface RequestOptions {
  body?: unknown;
  headers?: Record<string, string>;
  scopes?: ScopeSet[];
  raw?: boolean;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function graphFetch(method: string, path: string, opts: RequestOptions = {}): Promise<Response> {
  const url = path.startsWith('https://') ? path : GRAPH + path;
  if (!url.startsWith(GRAPH + '/')) throw new GraphError('Lien Microsoft Graph invalide.');
  const send = async (refresh: boolean) => {
    const headers: Record<string, string> = { Accept: 'application/json', ...opts.headers, Authorization: `Bearer ${await token(opts.scopes, refresh)}` };
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
    let response: Response | null = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        response = await fetch(url, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
      } catch {
        throw new GraphError('La communication avec Microsoft Graph a été interrompue. Vérifiez la connexion internet et actualisez les données avant de relancer l’opération.');
      }
      if (attempt === 2 || !(await shouldRetry(response, method))) break;
      const after = Number(response.headers.get('Retry-After'));
      await sleep(Number.isFinite(after) && after > 0 ? after * 1000 : 500 * (attempt + 1));
    }
    return response!;
  };
  let response = await send(false);
  // Jeton refusé avant traitement : une seule relance avec un jeton neuf ne peut rien dupliquer.
  if (response.status === 401) response = await send(true);
  if (!response.ok) throw await toError(response, method);
  return response;
}

export async function graph<T = Record<string, unknown>>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const response = await graphFetch(method, path, opts);
  const text = await response.text();
  if (!text) return {} as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new GraphError('Microsoft Graph a retourné une réponse illisible. Actualisez les données avant de relancer l’opération.', response.status);
  }
}

/** Parcourt toutes les pages d'une collection. */
export async function graphAll<T>(path: string, opts: RequestOptions = {}): Promise<T[]> {
  const items: T[] = [];
  let next: string | undefined = path;
  while (next) {
    const page: { value?: T[]; '@odata.nextLink'?: string } = await graph('GET', next, opts);
    items.push(...(page.value ?? []));
    next = page['@odata.nextLink'];
  }
  return items;
}

async function codes(response: Response): Promise<{ code: string; inner: string; message: string }> {
  try {
    const payload = await response.clone().json();
    const e = payload?.error ?? {};
    const inner = e.innerError ?? e.innererror ?? {};
    return { code: String(e.code ?? ''), inner: String(inner.code ?? ''), message: String(e.message ?? '') };
  } catch {
    return { code: '', inner: '', message: '' };
  }
}

async function shouldRetry(response: Response, method: string): Promise<boolean> {
  if (!RETRY.has(response.status)) return false;
  const { code, inner } = await codes(response);
  const all = [code, inner].map((c) => c.toLowerCase());
  if (all.some((c) => CONFLICT.has(c) || c.startsWith('invalidsession') || c === 'internalservererroruncategorized')) return false;
  // Une mutation échouée peut avoir pris effet : la rejouer pourrait dupliquer une ligne.
  return response.status === 429 || method === 'GET' || method === 'HEAD';
}

async function toError(response: Response, method: string): Promise<GraphError> {
  const { code, inner, message } = await codes(response);
  const all = [code, inner].map((c) => c.toLowerCase());
  let text: string;
  if (response.status === 401) {
    text = 'La connexion Microsoft a expiré ou a été refusée (401). Utilisez Paramètres > Se reconnecter au compte Microsoft, puis actualisez les données.';
  } else if (all.some((c) => CONFLICT.has(c)) || response.status === 423) {
    text =
      'Le classeur Excel partagé est verrouillé ou en conflit avec une autre session. Ouvrez le fichier original dans Excel pour le web, résolvez le conflit, puis actualisez. Ne remplacez pas le fichier original par une copie non fusionnée.';
  } else if (all.some((c) => c.startsWith('invalidsession'))) {
    text = 'La session Excel du classeur partagé n’est plus valide. Actualisez pour ouvrir une nouvelle session.';
  } else if (response.status === 403) {
    text =
      'Microsoft Graph a refusé l’opération (403) : accès refusé. Vérifiez les autorisations de ProjectFlow (Files.ReadWrite.All, Tasks.ReadWrite, Mail.Send…) et l’accès de votre compte à la ressource.';
  } else {
    text = `Microsoft Graph a refusé l’opération (${response.status})${message ? ' : ' + message : '.'}`;
  }
  if (method !== 'GET' && RETRY.has(response.status) && response.status !== 429) {
    text += ' Le résultat de l’opération peut être incomplet. Actualisez les données avant de recommencer.';
  }
  return new GraphError(text, response.status, code, inner);
}

export const seg = (v: string) => encodeURIComponent(v);
export const odata = (v: string) => v.replaceAll("'", "''");
