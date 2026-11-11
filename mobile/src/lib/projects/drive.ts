/**
 * OneDrive / SharePoint folders through Microsoft Graph: what the desktop does on the
 * synced file system (`Path.exists`, `iterdir`, `shutil.copy2`, `rename`, openpyxl),
 * done here with the drive and workbook APIs. The demo mode implements the same
 * gateway in memory (`demoProjects.ts`).
 */

import { sharingToken } from '@/lib/base64';
import {
  driveItemByPath,
  drivePathFromSetting,
  GraphError,
  graphFetch,
  graphRequest,
  type TokenSource,
} from '@/lib/graph';
import { FICHE_RANGE, ficheCellsFromRange, type CellValue, type CellWrite, type FicheCells } from '@/lib/projects/fiche';

export type DriveRef = { driveId: string; id: string };

export type DriveItem = DriveRef & {
  name: string;
  folder: boolean;
  size: number;
  /** ISO date of the last change. */
  modified: string;
  /** Address of the item in OneDrive / SharePoint (empty in the demo mode). */
  webUrl: string;
  /**
   * The file's own address (`webDavUrl`): what an Office app opens. `webUrl` of an Office file
   * is the page of Office for the web, which the apps cannot open.
   */
  davUrl?: string;
  /** Small preview of a photo, when Microsoft has one. */
  thumbnailUrl?: string;
  /** Number of items in a folder, when Microsoft tells it. */
  count?: number;
};

/** A local file to upload: a photo chosen on the phone. */
export type UploadSource = { uri: string; size?: number | null; mimeType?: string | null; file?: Blob | null };

export interface DriveGateway {
  /** The item a settings value designates: a OneDrive/SharePoint link, or a path in OneDrive. */
  resolve(location: string, label: string): Promise<DriveItem>;
  children(folder: DriveRef, options?: { thumbnails?: boolean }): Promise<DriveItem[]>;
  /** A new folder; fails when the name is taken. */
  createFolder(parent: DriveRef, name: string): Promise<DriveItem>;
  /** Copy `item` under `parent` as `name`, and wait until Microsoft has finished. */
  copy(item: DriveRef, parent: DriveRef, name: string, what: string): Promise<DriveItem>;
  rename(item: DriveRef, name: string): Promise<DriveItem>;
  upload(parent: DriveRef, name: string, source: UploadSource): Promise<DriveItem>;
  /** The fiche cells of the first worksheet of a workbook. */
  readFiche(item: DriveRef): Promise<FicheCells>;
  /** Write cells of the first worksheet of a workbook. */
  writeCells(item: DriveRef, writes: CellWrite[]): Promise<void>;
  /** A short-lived address that downloads the file without signing in (`@microsoft.graph.downloadUrl`). */
  downloadUrl(item: DriveRef): Promise<string>;
}

export class DriveError extends Error {}

/** Folder names compared like Windows users type them: no accents, no case (`folder_key`). */
export function folderKey(name: string): string {
  return name
    .trim()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/** `find_child_directory`: « Plan d'exécution » found when looking for « plan d'execution ». */
export function findChildFolder<T extends { name: string; folder: boolean }>(children: readonly T[], name: string): T | null {
  const wanted = folderKey(name);
  const folders = children.filter((child) => child.folder && folderKey(child.name) === wanted);
  folders.sort((left, right) => (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
  return folders[0] ?? null;
}

/** A file of `children` by exact name (OneDrive names are case-insensitive). */
export function findChildFile<T extends { name: string; folder: boolean }>(children: readonly T[], name: string): T | null {
  const wanted = name.toLowerCase();
  return children.find((child) => !child.folder && child.name.toLowerCase() === wanted) ?? null;
}

/**
 * A path of the settings, relative to the root of the connected OneDrive. A path copied
 * from the poste (« C:\Users\…\OneDrive - Balz Métal SA\Projets ») keeps what follows
 * the OneDrive folder.
 */
export { drivePathFromSetting } from '@/lib/graph';

/** Status of a copy read from its monitor address. */
export type CopyMonitor = { status?: string; resourceId?: string; percentageComplete?: number; error?: { message?: string } };

export function copyFinished(monitor: CopyMonitor): 'done' | 'failed' | 'running' {
  const status = (monitor.status ?? '').toLowerCase();
  if (status === 'completed') return 'done';
  if (status === 'failed' || status === 'cancelled' || status === 'deletefailed') return 'failed';
  return 'running';
}

// ------------------------------------------------------------------ Graph ----

type GraphItem = {
  id: string;
  name?: string;
  size?: number;
  webUrl?: string;
  webDavUrl?: string;
  lastModifiedDateTime?: string;
  folder?: { childCount?: number };
  file?: unknown;
  parentReference?: { driveId?: string };
  remoteItem?: {
    id?: string;
    folder?: { childCount?: number };
    webUrl?: string;
    webDavUrl?: string;
    parentReference?: { driveId?: string };
  };
  thumbnails?: { medium?: { url?: string }; small?: { url?: string } }[];
};

const SELECT = '$select=id,name,size,webUrl,webDavUrl,lastModifiedDateTime,folder,file,parentReference,remoteItem';
const COPY_TIMEOUT_MS = 3 * 60 * 1000;
const UPLOAD_SIMPLE_LIMIT = 4 * 1024 * 1024;
/** Upload-session chunks must be multiples of 320 KiB. */
const UPLOAD_CHUNK = 10 * 320 * 1024;

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toItem(raw: GraphItem, fallbackDrive = ''): DriveItem {
  const driveId = raw.remoteItem?.parentReference?.driveId ?? raw.parentReference?.driveId ?? fallbackDrive;
  return {
    driveId,
    id: raw.remoteItem?.id ?? raw.id,
    name: raw.name ?? '',
    folder: raw.folder !== undefined || raw.remoteItem?.folder !== undefined,
    size: raw.size ?? 0,
    modified: raw.lastModifiedDateTime ?? '',
    webUrl: raw.webUrl ?? raw.remoteItem?.webUrl ?? '',
    davUrl: raw.webDavUrl ?? raw.remoteItem?.webDavUrl ?? '',
    thumbnailUrl: raw.thumbnails?.[0]?.medium?.url ?? raw.thumbnails?.[0]?.small?.url,
    count: raw.folder?.childCount ?? raw.remoteItem?.folder?.childCount,
  };
}

function itemPath(ref: DriveRef): string {
  return `/drives/${encodeURIComponent(ref.driveId)}/items/${encodeURIComponent(ref.id)}`;
}

function odata(value: string): string {
  return value.replace(/'/g, "''");
}

/** Retry the first workbook call of a file Microsoft has just copied or renamed. */
async function settled<T>(call: () => Promise<T>): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      return await call();
    } catch (exc) {
      lastError = exc;
      const retry = exc instanceof GraphError && [404, 409, 423, 429, 500, 502, 503, 504].includes(exc.status);
      if (!retry || attempt === 4) throw exc;
      await wait(1200 * (attempt + 1));
    }
  }
  throw lastError;
}

export function graphDrive(token: TokenSource): DriveGateway {
  const sheets = new Map<string, string>();

  async function firstSheet(item: DriveRef): Promise<string> {
    const known = sheets.get(item.id);
    if (known) return known;
    const payload = await settled(() =>
      graphRequest<{ value?: { name?: string; position?: number }[] }>(
        token,
        `${itemPath(item)}/workbook/worksheets?$select=name,position`,
      ),
    );
    const first = [...(payload.value ?? [])].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))[0];
    if (!first?.name) throw new DriveError("Le classeur de la fiche n'a aucune feuille.");
    sheets.set(item.id, first.name);
    return first.name;
  }

  async function byPath(parent: DriveRef, name: string): Promise<DriveItem | null> {
    try {
      const raw = await graphRequest<GraphItem>(token, `${itemPath(parent)}:/${encodeURIComponent(name)}:?${SELECT}`);
      return toItem(raw, parent.driveId);
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 404) return null;
      throw exc;
    }
  }

  return {
    async resolve(location, label) {
      const raw = location.trim();
      if (!raw) throw new DriveError(`${label} non configuré. Collez son lien OneDrive / SharePoint dans les paramètres.`);
      const notFound = new DriveError(`${label} introuvable sur OneDrive / SharePoint. Vérifiez le lien dans les paramètres.`);
      let item: GraphItem | null;
      try {
        item = raw.includes('://')
          ? await graphRequest<GraphItem>(token, `/shares/${sharingToken(raw)}/driveItem?${SELECT}`, {
              headers: { Prefer: 'redeemSharingLink' },
            })
          : await driveItemByPath<GraphItem>(token, drivePathFromSetting(raw), SELECT);
      } catch (exc) {
        if (exc instanceof GraphError && exc.status === 404) throw notFound;
        throw exc;
      }
      if (!item) throw notFound;
      const resolved = toItem(item);
      if (!resolved.driveId || !resolved.folder) throw new DriveError(`Le lien « ${label} » ne désigne pas un dossier.`);
      return resolved;
    },

    async children(folder, options) {
      const expand = options?.thumbnails ? '&$expand=thumbnails' : '';
      let next: string | undefined = `${itemPath(folder)}/children?$top=999&${SELECT}${expand}`;
      const items: DriveItem[] = [];
      while (next) {
        const payload: { value?: GraphItem[]; '@odata.nextLink'?: string } = await graphRequest(token, next);
        items.push(...(payload.value ?? []).map((raw) => toItem(raw, folder.driveId)));
        next = payload['@odata.nextLink'];
      }
      return items;
    },

    async createFolder(parent, name) {
      const raw = await graphRequest<GraphItem>(token, `${itemPath(parent)}/children`, {
        method: 'POST',
        body: { name, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' },
      });
      return toItem(raw, parent.driveId);
    },

    async copy(item, parent, name, what) {
      const response = await graphFetch(token, `${itemPath(item)}/copy?@microsoft.graph.conflictBehavior=fail`, {
        method: 'POST',
        body: { parentReference: { driveId: parent.driveId, id: parent.id }, name },
      });
      const monitor = response.headers.get('Location') ?? response.headers.get('location');
      const started = Date.now();
      let delay = 800;
      while (Date.now() - started < COPY_TIMEOUT_MS) {
        await wait(delay);
        delay = Math.min(delay * 1.5, 4000);
        if (monitor) {
          let state: CopyMonitor;
          try {
            // In progress, the monitor answers 202 with its status: read the body in any case.
            const text = await (await graphFetch(token, monitor, { authenticated: false })).text();
            state = text ? (JSON.parse(text) as CopyMonitor) : {};
          } catch (exc) {
            // A finished copy may redirect to the new item, which needs a token: look it up.
            if (exc instanceof GraphError && exc.status !== 0) {
              const found = await byPath(parent, name);
              if (found) return found;
            }
            throw exc;
          }
          const finished = copyFinished(state);
          if (finished === 'failed') {
            const detail = state.error?.message ? ` (${state.error.message})` : '';
            throw new DriveError(`La copie de ${what} a échoué chez Microsoft${detail}.`);
          }
          if (finished === 'done') {
            if (state.resourceId) {
              const raw = await graphRequest<GraphItem>(
                token,
                `/drives/${encodeURIComponent(parent.driveId)}/items/${encodeURIComponent(state.resourceId)}?${SELECT}`,
              );
              return toItem(raw, parent.driveId);
            }
            const found = await byPath(parent, name);
            if (found) return found;
          }
        } else {
          // No monitor address readable (browser): wait for the copy to appear.
          const found = await byPath(parent, name);
          if (found) return found;
        }
      }
      throw new DriveError(
        `La copie de ${what} n'est pas terminée après 3 minutes. Vérifiez le dossier sur OneDrive avant de recommencer.`,
      );
    },

    async rename(item, name) {
      const raw = await graphRequest<GraphItem>(token, `${itemPath(item)}?${SELECT}`, { method: 'PATCH', body: { name } });
      return toItem(raw, item.driveId);
    },

    async upload(parent, name, source) {
      const blob = source.file ?? (await (await fetch(source.uri)).blob());
      const size = blob.size || source.size || 0;
      const contentType = source.mimeType ?? (blob.type || 'application/octet-stream');
      const target = `${itemPath(parent)}:/${encodeURIComponent(name)}:`;
      if (size <= UPLOAD_SIMPLE_LIMIT) {
        const raw = await graphRequest<GraphItem>(token, `${target}/content?@microsoft.graph.conflictBehavior=rename`, {
          method: 'PUT',
          raw: blob,
          contentType,
        });
        return toItem(raw, parent.driveId);
      }
      const session = await graphRequest<{ uploadUrl?: string }>(token, `${target}/createUploadSession`, {
        method: 'POST',
        body: { item: { '@microsoft.graph.conflictBehavior': 'rename' } },
      });
      if (!session.uploadUrl) throw new DriveError("Microsoft n'a pas ouvert d'envoi pour la photo.");
      let last: GraphItem | undefined;
      for (let start = 0; start < size; start += UPLOAD_CHUNK) {
        const end = Math.min(start + UPLOAD_CHUNK, size);
        const response = await graphFetch(token, session.uploadUrl, {
          method: 'PUT',
          raw: blob.slice(start, end),
          contentType: 'application/octet-stream',
          headers: { 'Content-Range': `bytes ${start}-${end - 1}/${size}` },
          authenticated: false,
        });
        if (end === size) last = (await response.json()) as GraphItem;
      }
      if (!last) throw new DriveError("L'envoi de la photo n'a pas abouti.");
      return toItem(last, parent.driveId);
    },

    async downloadUrl(item) {
      // As Microsoft documents it (`select`, not `$select`, which OneDrive Business may answer
      // without the link); else the whole item, which carries it.
      type WithDownload = { '@microsoft.graph.downloadUrl'?: string };
      const selected = await graphRequest<WithDownload>(token, `${itemPath(item)}?select=id,@microsoft.graph.downloadUrl`);
      const url =
        selected['@microsoft.graph.downloadUrl'] ??
        (await graphRequest<WithDownload>(token, itemPath(item)))['@microsoft.graph.downloadUrl'];
      if (!url) throw new DriveError('Microsoft ne donne pas ce fichier à télécharger.');
      return url;
    },

    async readFiche(item) {
      const sheet = await firstSheet(item);
      const range = await settled(() =>
        graphRequest<{ values?: CellValue[][]; text?: string[][] }>(
          token,
          `${itemPath(item)}/workbook/worksheets/${encodeURIComponent(sheet)}/range(address='${FICHE_RANGE}')?$select=values,text`,
        ),
      );
      return ficheCellsFromRange(range.values ?? [], range.text);
    },

    async writeCells(item, writes) {
      if (!writes.length) return;
      const sheet = await firstSheet(item);
      const workbook = `${itemPath(item)}/workbook`;
      let session = '';
      try {
        const opened = await settled(() =>
          graphRequest<{ id?: string }>(token, `${workbook}/createSession`, { method: 'POST', body: { persistChanges: true } }),
        );
        session = opened.id ?? '';
      } catch {
        // Without a session the writes still go through, one by one.
      }
      const headers: Record<string, string> = session ? { 'workbook-session-id': session } : {};
      try {
        for (const write of writes) {
          const body: Record<string, unknown> = { values: [[write.value]] };
          if (write.numberFormat) body.numberFormat = [[write.numberFormat]];
          await settled(() =>
            graphRequest(
              token,
              `${workbook}/worksheets/${encodeURIComponent(sheet)}/range(address='${odata(write.address)}')`,
              { method: 'PATCH', body, headers },
            ),
          );
        }
      } finally {
        if (session) {
          try {
            await graphRequest(token, `${workbook}/closeSession`, { method: 'POST', headers });
          } catch {
            // The changes are persisted as they are written.
          }
        }
      }
    },
  };
}
