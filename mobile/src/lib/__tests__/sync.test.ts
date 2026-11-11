import { creation, stamped, update } from '@/lib/planning/documents';
import {
  documentKey,
  EXISTS_MESSAGE,
  MemoryPlanningStore,
  parseDocument,
  sortDelta,
  type StoredDocument,
} from '@/lib/planning/store';
import { writeChange, type Documents } from '@/lib/planning/sync';

const PHONE = { initials: 'LM', name: '', poste: 'iPhone', poste_id: 'phone' };
const DESK = { initials: 'KDU', name: '', poste: 'Atelier', poste_id: 'desk' };

function task(fields: Record<string, unknown> = {}) {
  return {
    id: 't1',
    title: 'Soudure',
    status: 'confirmed',
    notes: '',
    start: '2026-11-09T06:30',
    end: '2026-11-09T16:30',
    assignments: { kdu: {} },
    deleted_at: null,
    ...fields,
  };
}

async function opened(store: MemoryPlanningStore): Promise<Documents> {
  const documents: Documents = new Map();
  for (const document of (await store.changes(null)).documents) {
    documents.set(documentKey(document.kind, document.id), document);
  }
  return documents;
}

describe('planning files', () => {
  it('reads a document, ignores a copy saved under another name', () => {
    expect(parseDocument('t1', '﻿{"id":"t1","title":"A"}')).toEqual({ id: 't1', title: 'A' });
    expect(parseDocument('t1', '{"id":"t2"}')).toBe('foreign');
    expect(parseDocument('t1', 'not json')).toBeNull();
    expect(parseDocument('t1', '[1]')).toBeNull();
  });

  it('sorts a delta into deletions and files to download', () => {
    const folder = 'F';
    const known = new Map([['item-1', { kind: 'task' as const, id: 'a' }], ['item-2', { kind: 'task' as const, id: 'b' }]]);
    const sorted = sortDelta(
      [
        { id: 'item-1', deleted: {}, parentReference: { id: folder } },
        { id: 'item-2', name: 'notes.txt', file: {}, parentReference: { id: folder } },
        { id: 'item-3', name: 'task__c.json', file: {}, parentReference: { id: folder }, eTag: 'e3' },
        { id: 'item-4', name: 'task__d.json', file: {}, parentReference: { id: 'elsewhere' } },
        { id: 'item-5', deleted: {}, parentReference: { id: folder } },
      ],
      folder,
      known,
    );
    // a deleted, b renamed away from its planning name, c new; d is outside the folder.
    expect(sorted.deletions.map((deletion) => deletion.id).sort()).toEqual(['a', 'b']);
    expect(sorted.wanted.map((item) => item.id)).toEqual(['item-3']);
    // An unknown deleted item of the folder cannot be placed without a full read.
    expect(sorted.ambiguous).toBe(true);
  });
});

describe('writing a change', () => {
  it('creates, then updates with the eTag it got', async () => {
    const store = new MemoryPlanningStore([]);
    const documents = await opened(store);
    const created = creation('task', task(), PHONE);
    expect(await writeChange(store, documents, created, PHONE)).toBe('');
    const saved = documents.get('task:t1') as StoredDocument;
    expect(saved.data?.created_by).toEqual(PHONE);
    const change = update('task', saved.data!, { status: 'done' }, PHONE)!;
    await writeChange(store, documents, change, PHONE);
    expect((await store.get('task', 't1'))?.data?.status).toBe('done');
  });

  it('refuses to create what another poste created meanwhile', async () => {
    const store = new MemoryPlanningStore([{ kind: 'task', id: 't1', data: task() }]);
    const documents: Documents = new Map();
    await expect(writeChange(store, documents, creation('task', task(), PHONE), PHONE)).rejects.toThrow(EXISTS_MESSAGE);
    expect(documents.has('task:t1')).toBe(true);
  });

  it('merges onto the version another poste saved meanwhile', async () => {
    const store = new MemoryPlanningStore([{ kind: 'task', id: 't1', data: task() }]);
    const documents = await opened(store);
    const mine = documents.get('task:t1')!.data!;
    // The desk changes the notes, the phone the status, from the same version.
    const theirs = stamped({ ...mine, notes: 'Contrôler les soudures' }, DESK);
    await store.update('task', 't1', documents.get('task:t1')!.etag, theirs);
    const message = await writeChange(store, documents, update('task', mine, { status: 'done' }, PHONE)!, PHONE);
    expect(message).toBe('');
    const stored = (await store.get('task', 't1'))!.data!;
    expect(stored).toMatchObject({ status: 'done', notes: 'Contrôler les soudures' });
    expect(stored.updated_by).toEqual(PHONE);
  });

  it('keeps the other poste’s value of a field changed on both sides and says so', async () => {
    const store = new MemoryPlanningStore([{ kind: 'task', id: 't1', data: task() }]);
    const documents = await opened(store);
    const mine = documents.get('task:t1')!.data!;
    await store.update('task', 't1', documents.get('task:t1')!.etag, stamped({ ...mine, start: '2026-11-10T06:30', end: '2026-11-10T16:30' }, DESK));
    const message = await writeChange(
      store,
      documents,
      update('task', mine, { start: '2026-11-11T06:30', end: '2026-11-11T16:30' }, PHONE)!,
      PHONE,
    );
    expect(message).toBe("KDU · Atelier a modifié cette tâche entre-temps : votre modification n'a pas été enregistrée.");
    expect((await store.get('task', 't1'))!.data).toMatchObject({ start: '2026-11-10T06:30', end: '2026-11-10T16:30' });
  });

  it('tells when another poste deleted the task meanwhile', async () => {
    const store = new MemoryPlanningStore([{ kind: 'task', id: 't1', data: task() }]);
    const documents = await opened(store);
    const mine = documents.get('task:t1')!.data!;
    await store.update(
      'task',
      't1',
      documents.get('task:t1')!.etag,
      stamped({ ...mine, deleted_at: '2026-11-07T10:00:00Z', deleted_by: DESK }, DESK),
    );
    await expect(writeChange(store, documents, update('task', mine, { notes: 'x' }, PHONE)!, PHONE)).rejects.toThrow(
      "KDU · Atelier a supprimé cette tâche entre-temps : votre modification n'a pas été enregistrée.",
    );
  });
});
