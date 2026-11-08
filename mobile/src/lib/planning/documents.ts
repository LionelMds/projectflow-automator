/**
 * The envelope every planning document carries (`core/planning/models.py`): id, schema
 * version, author and time of the creation and of the last change, soft deletion. A
 * document is kept as its whole JSON so fields this app does not know survive a write.
 */

import type { Author, DocumentKind } from '@/lib/planning/models';
import type { Json } from '@/lib/planning/store';

export const SCHEMA_VERSION = 1;

/** One document before and after a change; `before` is null for a creation. */
export type Change = { kind: DocumentKind; id: string; before: Json | null; after: Json };

/** 32 hexadecimal characters, like Python's `uuid4().hex`. */
export function newDocumentId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return uuid.replace(/-/g, '');
  let text = '';
  for (let i = 0; i < 32; i += 1) text += Math.floor(Math.random() * 16).toString(16);
  return text;
}

/** « 2026-11-07T09:14:03Z », the desktop's `utc_now_text`. */
export function utcNowText(now: Date = new Date()): string {
  return now.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Copy marked as changed now by `author`; the creation fields are filled once. */
export function stamped(document: Json, author: Author, now: string = utcNowText()): Json {
  const result: Json = { ...document, updated_at: now, updated_by: { ...author } };
  if (!document.created_at) {
    result.created_at = now;
    result.created_by = { ...author };
  }
  if (result.schema_version === undefined) result.schema_version = SCHEMA_VERSION;
  return result;
}

/** A new document's envelope, before its own fields. */
export function envelope(id: string = newDocumentId()): Json {
  return {
    id,
    schema_version: SCHEMA_VERSION,
    created_at: '',
    created_by: { initials: '', name: '', poste: '', poste_id: '' },
    updated_at: '',
    updated_by: { initials: '', name: '', poste: '', poste_id: '' },
    deleted_at: null,
    deleted_by: null,
  };
}

export function creation(kind: DocumentKind, document: Json, author: Author, now?: string): Change {
  return { kind, id: String(document.id), before: null, after: stamped(document, author, now) };
}

/** The change of `document` with `fields` replaced; null when nothing differs. */
export function update(kind: DocumentKind, document: Json, fields: Json, author: Author, now?: string): Change | null {
  const after: Json = { ...document, ...fields };
  const differs = Object.keys(fields).some((name) => JSON.stringify(document[name] ?? null) !== JSON.stringify(after[name] ?? null));
  if (!differs) return null;
  return { kind, id: String(document.id), before: document, after: stamped(after, author, now) };
}
