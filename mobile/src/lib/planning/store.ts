import { encodeDrivePath, GraphError, graphFetch, graphRequest, graphText, type TokenSource } from '@/lib/graph';
import {
  DOCUMENT_KINDS,
  type Absence,
  type DocumentKind,
  type PlanningData,
  type PlanningMeta,
  type PlanningTask,
  type Resource,
} from '@/lib/planning/models';

/**
 * The desktop's OneDrive planning store (`graph/drive_planning_store.py`): one file
 * `<kind>__<id>.json` per document in a folder at the root of the signed-in account's
 * OneDrive; any other file of the folder is ignored.
 *
 * Changes are read with the delta API of the whole drive: a first read takes a
 * `token=latest` link, then lists the folder, so a change made during the listing comes
 * back with the next delta. Writes are conditional, as on the desktop: a creation fails
 * when the file exists (409), an update when its eTag moved (If-Match → 412).
 */

const FILE_RE = new RegExp(`^(${DOCUMENT_KINDS.join('|')})__([A-Za-z0-9_-]{1,128})\\.json$`);
const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/;
const PAGE_SIZE = 200;
const DOWNLOAD_CONCURRENCY = 6;
const DRIVE = '/me/drive';
const LATEST_DELTA = `${DRIVE}/root/delta?token=latest`;
const MAX_PAGES = 10_000;

export const CONFLICT_MESSAGE =
  'Cet élément du planning a été modifié sur un autre poste entre-temps (412). ' +
  'Actualisez le planning puis recommencez la modification.';
export const EXISTS_MESSAGE = 'Cet élément du planning existe déjà : il a été créé sur un autre poste.';
export const MISSING_MESSAGE = 'Cet élément du planning a été supprimé sur un autre poste entre-temps.';

export type Json = Record<string, unknown>;
export type DocumentKey = `${DocumentKind}:${string}`;
export type StoredDocument = { kind: DocumentKind; id: string; etag: string; data: Json | null; deleted?: boolean };
export type StoreChanges = { documents: StoredDocument[]; cursor: string; full: boolean };

/** What the planning context reads and writes; the OneDrive folder or the demo memory. */
export interface PlanningStore {
  changes(cursor: string | null): Promise<StoreChanges>;
  get(kind: DocumentKind, id: string): Promise<StoredDocument | null>;
  create(kind: DocumentKind, id: string, data: Json): Promise<StoredDocument>;
  update(kind: DocumentKind, id: string, etag: string, data: Json): Promise<string>;
}

export class PlanningStoreError extends Error {}
/** A write refused because another poste changed (or created, or removed) the document. */
export class StoreConflictError extends Error {
  constructor(
    message: string,
    readonly reason: 'exists' | 'changed' | 'missing',
  ) {
    super(message);
  }
}

export function documentKey(kind: DocumentKind, id: string): DocumentKey {
  return `${kind}:${id}`;
}

export function fileName(kind: DocumentKind, id: string): string {
  if (!SAFE_ID.test(id)) throw new PlanningStoreError(`Identifiant de document de planning invalide: ${id}`);
  return `${kind}__${id}.json`;
}

export function parseFileName(name: unknown): { kind: DocumentKind; id: string } | null {
  if (typeof name !== 'string') return null;
  const match = FILE_RE.exec(name);
  return match ? { kind: match[1] as DocumentKind, id: match[2] } : null;
}

/** JSON of a file, null when unreadable or when it is a copy of another document. */
export function parseDocument(id: string, text: string): Json | null | 'foreign' {
  let data: unknown;
  try {
    data = JSON.parse(text.replace(/^﻿/, ''));
  } catch {
    return null;
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const own = (data as Json).id;
  if (typeof own === 'string' && own && own !== id) return 'foreign';
  return data as Json;
}

/** Sort documents by kind; a file whose own id differs is a stray copy. */
export function collect(entries: { kind: DocumentKind; id: string; data: unknown }[]): PlanningData {
  const data: PlanningData = { tasks: [], resources: [], absences: [], meta: null };
  for (const { kind, id, data: raw } of entries) {
    if (!raw || typeof raw !== 'object') continue;
    const document = raw as { id?: unknown };
    if (typeof document.id === 'string' && document.id && document.id !== id) continue;
    // The file name is the reference id; the JSON is trusted only for its other fields.
    const withId: unknown = { ...(raw as Json), id };
    if (kind === 'task') data.tasks.push(withId as PlanningTask);
    else if (kind === 'resource') data.resources.push(withId as Resource);
    else if (kind === 'absence') data.absences.push(withId as Absence);
    else data.meta = withId as PlanningMeta;
  }
  return data;
}

type DriveItem = {
  id: string;
  name?: string;
  eTag?: string;
  file?: unknown;
  folder?: unknown;
  deleted?: unknown;
  parentReference?: { id?: string; driveId?: string };
  '@microsoft.graph.downloadUrl'?: string;
};
type Page = { value?: DriveItem[]; '@odata.nextLink'?: string; '@odata.deltaLink'?: string };

async function inBatches<T, R>(items: T[], size: number, work: (item: T) => Promise<R>): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.min(size, items.length) }, async () => {
    while (next < items.length) {
      const index = next++;
      results[index] = await work(items[index]);
    }
  });
  await Promise.all(runners);
  return results;
}

/** Delta items sorted into deletions, files to download, and whether a deletion is unplaced. */
export function sortDelta(
  items: DriveItem[],
  folderId: string,
  known: Map<string, { kind: DocumentKind; id: string }>,
): { deletions: { kind: DocumentKind; id: string; itemId: string }[]; wanted: DriveItem[]; ambiguous: boolean } {
  const deletions = new Map<DocumentKey, { kind: DocumentKind; id: string; itemId: string }>();
  const wanted = new Map<DocumentKey, DriveItem>();
  let ambiguous = false;
  for (const item of items.filter((raw) => raw.deleted !== undefined)) {
    const key = known.get(item.id) ?? null;
    const parent = item.parentReference?.id ?? null;
    const named = parseFileName(item.name);
    if (key) deletions.set(documentKey(key.kind, key.id), { ...key, itemId: item.id });
    else if (named && (parent === null || parent === folderId)) {
      deletions.set(documentKey(named.kind, named.id), { ...named, itemId: item.id });
    } else if (parent === null || parent === folderId) ambiguous = true;
  }
  for (const item of items.filter((raw) => raw.deleted === undefined)) {
    const was = known.get(item.id) ?? null;
    const key = parseFileName(item.name);
    const inside = item.parentReference?.id === folderId && item.file !== undefined;
    if (was && (!inside || !key || key.kind !== was.kind || key.id !== was.id)) {
      // Moved out of the folder or renamed: the document it held is gone.
      deletions.set(documentKey(was.kind, was.id), { ...was, itemId: item.id });
    }
    if (!key || !inside) continue;
    deletions.delete(documentKey(key.kind, key.id));
    wanted.set(documentKey(key.kind, key.id), item);
  }
  return { deletions: [...deletions.values()], wanted: [...wanted.values()], ambiguous };
}

export class DrivePlanningStore implements PlanningStore {
  private folderId: string | null = null;
  /** item id → document, and back, learnt from every read and write. */
  private keys = new Map<string, { kind: DocumentKind; id: string }>();
  private items = new Map<DocumentKey, string>();
  private complete = false;

  constructor(
    private readonly token: TokenSource,
    private readonly folder: string,
  ) {}

  private async ensureFolder(): Promise<string> {
    if (this.folderId) return this.folderId;
    try {
      const item = await graphRequest<DriveItem>(
        this.token,
        `${DRIVE}/root:/${encodeDrivePath(this.folder)}:?$select=id,folder`,
      );
      if (!item.folder) throw new PlanningStoreError(`« ${this.folder} » n'est pas un dossier OneDrive.`);
      this.folderId = item.id;
      return item.id;
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 404) {
        throw new PlanningStoreError(
          `Dossier du planning introuvable : « ${this.folder} » à la racine du OneDrive connecté. ` +
            'Connectez-vous avec le compte Microsoft partagé du répertoire chantier.',
        );
      }
      throw exc;
    }
  }

  private remember(itemId: string, kind: DocumentKind, id: string): void {
    const key = documentKey(kind, id);
    const previous = this.items.get(key);
    if (previous && previous !== itemId) this.keys.delete(previous);
    this.items.set(key, itemId);
    this.keys.set(itemId, { kind, id });
  }

  private forget(itemId: string): void {
    const key = this.keys.get(itemId);
    this.keys.delete(itemId);
    if (key && this.items.get(documentKey(key.kind, key.id)) === itemId) this.items.delete(documentKey(key.kind, key.id));
  }

  async changes(cursor: string | null): Promise<StoreChanges> {
    const folderId = await this.ensureFolder();
    if (!cursor) return this.fullRead();
    let items: DriveItem[];
    let link: string;
    try {
      [items, link] = await this.collect(cursor);
    } catch (exc) {
      // 410: resync asked; 400/404: the cursor is no longer accepted.
      if (exc instanceof GraphError && [400, 404, 410].includes(exc.status)) return this.fullRead();
      throw exc;
    }
    if (items.some((item) => item.id === folderId && item.deleted !== undefined)) {
      this.folderId = null;
      this.keys.clear();
      this.items.clear();
      this.complete = false;
      await this.ensureFolder();
      return this.fullRead();
    }
    const sorted = sortDelta(items, folderId, this.keys);
    if (sorted.ambiguous && !this.complete) return this.fullRead();
    for (const deletion of sorted.deletions) this.forget(deletion.itemId);
    const documents: StoredDocument[] = sorted.deletions.map(({ kind, id }) => ({ kind, id, etag: '', data: null, deleted: true }));
    const read = await this.downloadAll(sorted.wanted);
    return { documents: [...documents, ...read.documents], cursor: link, full: false };
  }

  private async fullRead(): Promise<StoreChanges> {
    const [, link] = await this.collect(LATEST_DELTA);
    const folderId = await this.ensureFolder();
    const children: DriveItem[] = [];
    let next: string | undefined = `${DRIVE}/items/${encodeURIComponent(folderId)}/children?$top=${PAGE_SIZE}`;
    for (let page = 0; next && page < MAX_PAGES; page += 1) {
      const payload: Page = await graphRequest<Page>(this.token, next);
      children.push(...(payload.value ?? []));
      next = payload['@odata.nextLink'];
    }
    this.keys.clear();
    this.items.clear();
    const files = children.filter((child) => child.file !== undefined && parseFileName(child.name));
    const read = await this.downloadAll(files);
    if (read.complete) this.complete = true;
    return { documents: read.documents, cursor: link, full: read.complete };
  }

  private async collect(link: string): Promise<[DriveItem[], string]> {
    const items = new Map<string, DriveItem>();
    let url = link;
    for (let page = 0; page < MAX_PAGES; page += 1) {
      const payload = await graphRequest<Page>(this.token, url);
      for (const item of payload.value ?? []) {
        items.delete(item.id);
        items.set(item.id, item);
      }
      if (payload['@odata.nextLink']) {
        url = payload['@odata.nextLink'];
        continue;
      }
      if (payload['@odata.deltaLink']) return [[...items.values()], payload['@odata.deltaLink']];
      break;
    }
    throw new PlanningStoreError("Microsoft Graph n'a pas retourné de lien de suivi des modifications.");
  }

  private async downloadAll(items: DriveItem[]): Promise<{ documents: StoredDocument[]; complete: boolean }> {
    const results = await inBatches(items, DOWNLOAD_CONCURRENCY, (item) => this.downloadOne(item));
    const documents: StoredDocument[] = [];
    let complete = true;
    for (const [document, readable] of results) {
      complete = complete && readable;
      if (document) documents.push(document);
    }
    return { documents, complete };
  }

  private async downloadOne(item: DriveItem): Promise<[StoredDocument | null, boolean]> {
    const key = parseFileName(item.name);
    if (!key) return [null, true];
    this.remember(item.id, key.kind, key.id);
    let text: string;
    try {
      text = await this.content(item);
    } catch (exc) {
      // Deleted since it was listed: the next delta reports it.
      if (exc instanceof GraphError && exc.status === 404) return [null, true];
      throw exc;
    }
    const data = parseDocument(key.id, text);
    if (data === 'foreign') return [null, true];
    if (data === null) return [null, false];
    return [{ kind: key.kind, id: key.id, etag: item.eTag ?? '', data }, true];
  }

  private async content(item: DriveItem): Promise<string> {
    const url = item['@microsoft.graph.downloadUrl'];
    if (url) {
      try {
        return await graphText(this.token, url, false);
      } catch (exc) {
        // The pre-authenticated address expired: the content request gives a new one.
        if (!(exc instanceof GraphError && [401, 403, 404].includes(exc.status))) throw exc;
      }
    }
    return graphText(this.token, `${DRIVE}/items/${encodeURIComponent(item.id)}/content`);
  }

  private async metadata(kind: DocumentKind, id: string): Promise<DriveItem | null> {
    const folderId = await this.ensureFolder();
    const known = this.items.get(documentKey(kind, id));
    if (known) {
      try {
        const item = await graphRequest<DriveItem>(this.token, `${DRIVE}/items/${encodeURIComponent(known)}`);
        const key = parseFileName(item.name);
        if (item.deleted === undefined && item.parentReference?.id === folderId && key?.kind === kind && key.id === id) {
          return item;
        }
      } catch (exc) {
        if (!(exc instanceof GraphError && exc.status === 404)) throw exc;
      }
      this.forget(known);
    }
    try {
      const item = await graphRequest<DriveItem>(this.token, this.path(folderId, fileName(kind, id)));
      if (!item.file) return null;
      this.remember(item.id, kind, id);
      return item;
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 404) return null;
      throw exc;
    }
  }

  async get(kind: DocumentKind, id: string): Promise<StoredDocument | null> {
    const item = await this.metadata(kind, id);
    if (!item) return null;
    const [document] = await this.downloadOne(item);
    return document;
  }

  async create(kind: DocumentKind, id: string, data: Json): Promise<StoredDocument> {
    const folderId = await this.ensureFolder();
    try {
      const response = await graphFetch(
        this.token,
        `${this.path(folderId, fileName(kind, id))}:/content?@microsoft.graph.conflictBehavior=fail`,
        { method: 'PUT', raw: JSON.stringify(data), contentType: 'application/json' },
      );
      const item = (await response.json()) as DriveItem;
      this.remember(item.id, kind, id);
      return { kind, id, etag: item.eTag ?? '', data };
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 409) throw new StoreConflictError(EXISTS_MESSAGE, 'exists');
      throw exc;
    }
  }

  async update(kind: DocumentKind, id: string, etag: string, data: Json): Promise<string> {
    const itemId = this.items.get(documentKey(kind, id)) ?? (await this.metadata(kind, id))?.id;
    if (!itemId) throw new StoreConflictError(MISSING_MESSAGE, 'missing');
    try {
      const response = await graphFetch(this.token, `${DRIVE}/items/${encodeURIComponent(itemId)}/content`, {
        method: 'PUT',
        raw: JSON.stringify(data),
        contentType: 'application/json',
        headers: etag ? { 'If-Match': etag } : {},
      });
      const item = (await response.json()) as DriveItem;
      return item.eTag ?? '';
    } catch (exc) {
      if (exc instanceof GraphError && exc.status === 412) throw new StoreConflictError(CONFLICT_MESSAGE, 'changed');
      if (exc instanceof GraphError && exc.status === 404) {
        this.forget(itemId);
        throw new StoreConflictError(MISSING_MESSAGE, 'missing');
      }
      throw exc;
    }
  }

  private path(folderId: string, name: string): string {
    return `${DRIVE}/items/${encodeURIComponent(folderId)}:/${encodeURIComponent(name)}`;
  }
}

/** « Mode démo »: the same store in memory, with eTags, so every path can be tried. */
export class MemoryPlanningStore implements PlanningStore {
  private documents = new Map<DocumentKey, StoredDocument>();
  private version = 0;
  private sent = false;

  constructor(seed: { kind: DocumentKind; id: string; data: Json }[]) {
    for (const { kind, id, data } of seed) this.documents.set(documentKey(kind, id), { kind, id, data, etag: this.nextTag() });
  }

  private nextTag(): string {
    this.version += 1;
    return `"demo-${this.version}"`;
  }

  async changes(cursor: string | null): Promise<StoreChanges> {
    // Every document on the first read; nothing changes behind the phone's back after.
    const full = !cursor || !this.sent;
    this.sent = true;
    return { documents: full ? [...this.documents.values()] : [], cursor: 'demo', full };
  }

  async get(kind: DocumentKind, id: string): Promise<StoredDocument | null> {
    return this.documents.get(documentKey(kind, id)) ?? null;
  }

  async create(kind: DocumentKind, id: string, data: Json): Promise<StoredDocument> {
    const key = documentKey(kind, id);
    if (this.documents.has(key)) throw new StoreConflictError(EXISTS_MESSAGE, 'exists');
    const document = { kind, id, data, etag: this.nextTag() };
    this.documents.set(key, document);
    return document;
  }

  async update(kind: DocumentKind, id: string, etag: string, data: Json): Promise<string> {
    const key = documentKey(kind, id);
    const current = this.documents.get(key);
    if (!current) throw new StoreConflictError(MISSING_MESSAGE, 'missing');
    if (etag && current.etag !== etag) throw new StoreConflictError(CONFLICT_MESSAGE, 'changed');
    const next = { kind, id, data, etag: this.nextTag() };
    this.documents.set(key, next);
    return next.etag;
  }
}
