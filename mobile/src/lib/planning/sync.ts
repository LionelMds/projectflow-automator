/**
 * Writing one change to the shared planning, as the desktop's sync does
 * (`core/planning/sync.py`, `service.write_conflicted`): a refused write (another poste
 * saved meanwhile) is merged onto the version on OneDrive and sent again, up to three
 * times; what the other poste changed too keeps its value and is reported.
 */

import { stamped, type Change } from '@/lib/planning/documents';
import {
  changedFields,
  deletedMeanwhile,
  deletedMessage,
  mergeDocuments,
  modifiedMessage,
  NOT_SAVED,
  softDeletedMessage,
} from '@/lib/planning/merge';
import type { Author } from '@/lib/planning/models';
import {
  CONFLICT_MESSAGE,
  documentKey,
  EXISTS_MESSAGE,
  StoreConflictError,
  type DocumentKey,
  type PlanningStore,
  type StoredDocument,
} from '@/lib/planning/store';

export const MAX_CONFLICT_ATTEMPTS = 3;

export type Documents = Map<DocumentKey, StoredDocument>;

function put(documents: Documents, document: StoredDocument): void {
  const key = documentKey(document.kind, document.id);
  if (document.deleted || !document.data) documents.delete(key);
  else documents.set(key, document);
}

/**
 * Send `change`; `documents` (the phone's copy) follows what is on OneDrive. Returns a
 * message when another poste's values were kept; throws an Error (French message) when
 * nothing of the change could be saved.
 */
export async function writeChange(
  store: PlanningStore,
  documents: Documents,
  change: Change,
  author: Author,
): Promise<string> {
  let { before, after } = change;
  const { kind, id } = change;
  let message = '';
  for (let attempt = 0; attempt < MAX_CONFLICT_ATTEMPTS; attempt += 1) {
    const current = documents.get(documentKey(kind, id));
    try {
      if (!before) put(documents, await store.create(kind, id, after));
      else put(documents, { kind, id, etag: await store.update(kind, id, current?.etag ?? '', after), data: after });
      return message;
    } catch (exc) {
      if (!(exc instanceof StoreConflictError)) throw exc;
      const server = await store.get(kind, id);
      if (!server?.data) {
        documents.delete(documentKey(kind, id));
        if (!before) continue; // A refused creation with nothing on OneDrive: again.
        throw new Error(deletedMessage(kind));
      }
      put(documents, server);
      if (!before) throw new Error(EXISTS_MESSAGE);
      const by = (server.data.updated_by ?? null) as Author | null;
      if (deletedMeanwhile(before, after, server.data)) {
        throw new Error(softDeletedMessage(kind, (server.data.deleted_by ?? by) as Author | null));
      }
      const { merged, lost } = mergeDocuments(before, after, server.data);
      if (lost.size) message = modifiedMessage(kind, by);
      if (changedFields(server.data, merged).size === 0) return message ? `${message} : ${NOT_SAVED}` : '';
      before = server.data;
      after = stamped(merged, author);
    }
  }
  throw new Error(CONFLICT_MESSAGE);
}
