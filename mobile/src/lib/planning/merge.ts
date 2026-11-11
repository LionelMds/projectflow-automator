/**
 * Conflict handling of a refused write, as the desktop does it
 * (`core/planning/service.py`: `merge_documents`, `write_conflicted`): the fields this
 * phone changed are reapplied onto the version another poste saved meanwhile; a field
 * the other poste changed too keeps its value and is reported as lost.
 */

import type { Author, DocumentKind } from '@/lib/planning/models';

export type Json = Record<string, unknown>;

const VOLATILE_FIELDS = new Set(['updated_at', 'updated_by', 'schema_version']);
const CREATION_FIELDS = new Set(['created_at', 'created_by']);
const DELETION_FIELDS = ['deleted_at', 'deleted_by'];
const LINKED_FIELDS = [['start', 'end'], ['first_day', 'last_day'], DELETION_FIELDS];
const ASSIGNMENTS = 'assignments';

export const DOCUMENT_PHRASES: Record<DocumentKind, string> = {
  task: 'cette tâche',
  resource: 'cette ressource',
  absence: 'cette absence',
  meta: 'les réglages du planning',
};
const DELETED_PHRASES: Record<DocumentKind, string> = {
  task: 'cette tâche a été supprimée',
  resource: 'cette ressource a été supprimée',
  absence: 'cette absence a été supprimée',
  meta: 'les réglages du planning ont été supprimés',
};
export const NOT_SAVED = "votre modification n'a pas été enregistrée.";
const OTHER_POSTE = 'un autre poste';

/** Deep equality of JSON values (key order ignored). */
export function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') {
    return (left ?? null) === (right ?? null);
  }
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, i) => sameJson(value, right[i]));
  }
  const a = left as Json;
  const b = right as Json;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) if (!sameJson(a[key], b[key])) return false;
  return true;
}

/** Top-level fields whose value differs, as [before, after] (`changed_fields`). */
export function changedFields(before: Json | null, after: Json | null): Map<string, [unknown, unknown]> {
  const a = before ?? {};
  const b = after ?? {};
  const changed = new Map<string, [unknown, unknown]>();
  for (const name of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (!VOLATILE_FIELDS.has(name) && !sameJson(a[name], b[name])) changed.set(name, [a[name], b[name]]);
  }
  return changed;
}

function asObject(value: unknown): Json {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {};
}

function mergeAssignments(old: unknown, mine: unknown, current: unknown): [Json, boolean] {
  const base = asObject(old);
  const wantedAll = asObject(mine);
  const theirs = asObject(current);
  const merged: Json = { ...theirs };
  let conflicted = false;
  const ids = [...Object.keys(base), ...Object.keys(wantedAll).filter((id) => !(id in base))];
  for (const id of ids) {
    const wanted = wantedAll[id];
    if (sameJson(base[id], wanted) || sameJson(theirs[id], wanted)) continue;
    if (!sameJson(theirs[id], base[id])) conflicted = true;
    else if (wanted === undefined) delete merged[id];
    else merged[id] = wanted;
  }
  return [merged, conflicted];
}

/** `after` reapplied onto `server`: the merged document and the fields kept from `server`. */
export function mergeDocuments(before: Json | null, after: Json, server: Json): { merged: Json; lost: Set<string> } {
  const touched = changedFields(before, after);
  const merged: Json = { ...server };
  const lost = new Set<string>();
  for (const [name, [old, wanted]] of touched) {
    const current = server[name];
    if (sameJson(current, wanted)) continue;
    if (name === ASSIGNMENTS) {
      const [value, conflicted] = mergeAssignments(old, wanted, current);
      merged[name] = value;
      if (conflicted) lost.add(name);
    } else if (sameJson(current, old)) {
      if (wanted === undefined) delete merged[name];
      else merged[name] = wanted;
    } else {
      lost.add(name);
    }
  }
  for (const name of CREATION_FIELDS) lost.delete(name);
  const serverLive = (server.deleted_at ?? null) === null;
  const mineLive = (after.deleted_at ?? null) === null;
  if (serverLive === mineLive) {
    for (const name of DELETION_FIELDS) lost.delete(name);
  }
  for (const group of LINKED_FIELDS) {
    if (group.some((name) => lost.has(name))) {
      for (const name of group) {
        if (touched.has(name)) {
          merged[name] = server[name];
          lost.add(name);
        }
      }
    }
  }
  return { merged, lost };
}

/** Whether the other poste soft-deleted the document while this phone edited it. */
export function deletedMeanwhile(before: Json | null, after: Json, server: Json): boolean {
  const base = before ? (before.deleted_at ?? null) : null;
  return (server.deleted_at ?? null) !== null && (after.deleted_at ?? null) === null && server.deleted_at !== base;
}

function capitalized(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

/** « LM · Poste atelier », else « Un autre poste » (`Author.label`, `_who`). */
export function who(author: Author | null | undefined): string {
  const label = [author?.initials || author?.name, author?.poste].filter(Boolean).join(' · ');
  return label || capitalized(OTHER_POSTE);
}

export function modifiedMessage(kind: DocumentKind, author: Author | null | undefined): string {
  return `${who(author)} a modifié ${DOCUMENT_PHRASES[kind]} entre-temps`;
}

export function softDeletedMessage(kind: DocumentKind, author: Author | null | undefined): string {
  return `${who(author)} a supprimé ${DOCUMENT_PHRASES[kind]} entre-temps : ${NOT_SAVED}`;
}

export function deletedMessage(kind: DocumentKind): string {
  return `${capitalized(DELETED_PHRASES[kind])} sur un autre poste entre-temps : ${NOT_SAVED}`;
}
