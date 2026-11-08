import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Platform } from 'react-native';

import { useAuth } from '@/lib/auth';
import { demoPlanning } from '@/lib/demo';
import { changedFields } from '@/lib/planning/merge';
import type { Author, DocumentKind, PlanningData } from '@/lib/planning/models';
import { stamped, type Change } from '@/lib/planning/documents';
import {
  collect,
  documentKey,
  DrivePlanningStore,
  MemoryPlanningStore,
  type DocumentKey,
  type Json,
  type PlanningStore,
  type StoredDocument,
} from '@/lib/planning/store';
import { writeChange } from '@/lib/planning/sync';
import { useSettings } from '@/lib/settings';
import { readItem, writeItem } from '@/lib/storage';

const POSTE_ID_KEY = 'projectflow.poste-id';

export const INITIALS_REQUIRED =
  'Renseignez vos initiales dans Paramètres pour modifier le planning : elles signent chaque modification.';

/** A change undone by « Annuler »: the label shown and the changes that revert it. */
export type Undo = { label: string; changes: Change[] };

type State = { loading: boolean; error: string; loadedAt: Date | null };

type PlanningValue = State & {
  data: PlanningData | null;
  /** Conflict or information message from the last write (cleared by the next one). */
  notice: string;
  clearNotice: () => void;
  /** Full read the first time, then only the changes (delta). */
  load: () => Promise<void>;
  /** Write changes; throws an Error with a French message when nothing could be saved. */
  apply: (changes: Change[], undoLabel?: string) => Promise<void>;
  undo: Undo | null;
  runUndo: () => Promise<void>;
  author: Author;
  /** Empty when the phone may edit the planning, else why not. */
  editBlocker: string;
};

const PlanningContext = createContext<PlanningValue | null>(null);

function newPosteId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return (random ?? `${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`).replace(/-/g, '');
}

function demoSeed(): { kind: DocumentKind; id: string; data: Json }[] {
  const data = demoPlanning();
  return [
    ...data.tasks.map((task) => ({ kind: 'task' as const, id: task.id, data: task as Json })),
    ...data.resources.map((resource) => ({ kind: 'resource' as const, id: resource.id, data: resource as Json })),
    ...data.absences.map((absence) => ({ kind: 'absence' as const, id: absence.id, data: absence as Json })),
    ...(data.meta ? [{ kind: 'meta' as const, id: data.meta.id, data: data.meta as Json }] : []),
  ];
}

export function PlanningProvider({ children }: { children: ReactNode }) {
  const { getAccessToken, status } = useAuth();
  const { settings } = useSettings();
  const [state, setState] = useState<State>({ loading: false, error: '', loadedAt: null });
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState('');
  const [undo, setUndo] = useState<Undo | null>(null);
  const [posteId, setPosteId] = useState('');
  const documents = useRef(new Map<DocumentKey, StoredDocument>());
  const cursor = useRef<string | null>(null);
  const loading = useRef<Promise<void> | null>(null);

  useEffect(() => {
    let alive = true;
    void readItem(POSTE_ID_KEY).then(async (stored) => {
      const id = stored || newPosteId();
      if (!stored) await writeItem(POSTE_ID_KEY, id);
      if (alive) setPosteId(id);
    });
    return () => {
      alive = false;
    };
  }, []);

  // One store per source: a new folder, account mode or demo switch starts afresh.
  const store = useMemo<PlanningStore>(
    () => (settings.demo ? new MemoryPlanningStore(demoSeed()) : new DrivePlanningStore(getAccessToken, settings.planningFolder)),
    [settings.demo, settings.planningFolder, getAccessToken],
  );
  useEffect(() => {
    documents.current = new Map();
    cursor.current = null;
    loading.current = null;
    setUndo(null);
    setNotice('');
    setState({ loading: false, error: '', loadedAt: null });
    setVersion((value) => value + 1);
  }, [store]);

  const author = useMemo<Author>(
    () => ({
      initials: settings.initials,
      name: '',
      poste: Platform.OS === 'ios' ? 'iPhone' : Platform.OS === 'android' ? 'Android' : 'Mobile',
      poste_id: posteId,
    }),
    [settings.initials, posteId],
  );

  const putDocument = useCallback((document: StoredDocument) => {
    const key = documentKey(document.kind, document.id);
    if (document.deleted || !document.data) documents.current.delete(key);
    else documents.current.set(key, document);
  }, []);

  const load = useCallback(async () => {
    if (loading.current) return loading.current;
    const run = (async () => {
      setState((previous) => ({ ...previous, loading: true, error: '' }));
      try {
        const changes = await store.changes(cursor.current);
        if (changes.full) documents.current = new Map();
        for (const document of changes.documents) putDocument(document);
        cursor.current = changes.cursor;
        setVersion((value) => value + 1);
        setState({ loading: false, error: '', loadedAt: new Date() });
      } catch (exc) {
        setState((previous) => ({ ...previous, loading: false, error: exc instanceof Error ? exc.message : 'Erreur inattendue.' }));
      } finally {
        loading.current = null;
      }
    })();
    loading.current = run;
    return run;
  }, [store, putDocument]);

  const editBlocker = !settings.initials
    ? INITIALS_REQUIRED
    : !settings.demo && status !== 'signedIn'
      ? 'Connectez-vous à Microsoft pour modifier le planning.'
      : '';

  const write = useCallback((change: Change) => writeChange(store, documents.current, change, author), [store, author]);

  const apply = useCallback(
    async (changes: Change[], undoLabel?: string) => {
      if (editBlocker) throw new Error(editBlocker);
      setNotice('');
      const written: Change[] = [];
      try {
        for (const change of changes) {
          const message = await write(change);
          if (message) setNotice(message);
          written.push(change);
        }
      } finally {
        setVersion((value) => value + 1);
      }
      if (undoLabel) {
        // Undo puts back the version this phone replaced; a creation is soft-deleted.
        const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
        const reverts: Change[] = written.map((change) => {
          const saved = documents.current.get(documentKey(change.kind, change.id))?.data ?? change.after;
          const target: Json = { ...saved };
          if (change.before) {
            // Only the fields this change touched go back: another poste's edits stay.
            for (const [name, [previous]] of changedFields(change.before, change.after)) {
              if (previous === undefined) delete target[name];
              else target[name] = previous;
            }
          } else {
            target.deleted_at = now;
            target.deleted_by = author;
          }
          return { kind: change.kind, id: change.id, before: saved, after: stamped(target, author) };
        });
        setUndo({ label: undoLabel, changes: reverts.reverse() });
      } else {
        setUndo(null);
      }
    },
    [editBlocker, write, author],
  );

  const runUndo = useCallback(async () => {
    if (!undo) return;
    const { changes } = undo;
    setUndo(null);
    await apply(changes);
  }, [undo, apply]);

  const data = useMemo<PlanningData | null>(() => {
    if (!state.loadedAt && documents.current.size === 0) return null;
    return collect([...documents.current.values()].map(({ kind, id, data: raw }) => ({ kind, id, data: raw })));
    // `version` marks every change of the documents held in the ref.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, state.loadedAt]);

  const clearNotice = useCallback(() => setNotice(''), []);

  const value = useMemo(
    () => ({ ...state, data, notice, clearNotice, load, apply, undo, runUndo, author, editBlocker }),
    [state, data, notice, clearNotice, load, apply, undo, runUndo, author, editBlocker],
  );
  return <PlanningContext.Provider value={value}>{children}</PlanningContext.Provider>;
}

export function usePlanning(): PlanningValue {
  const context = useContext(PlanningContext);
  if (!context) throw new Error('usePlanning hors de PlanningProvider');
  return context;
}

/** The planning for a screen opened on its own (a link, a reload): read it once when possible. */
export function useLoadedPlanning(): PlanningValue & { waiting: boolean } {
  const planning = usePlanning();
  const { settings, loaded } = useSettings();
  const { status } = useAuth();
  const ready = loaded && (settings.demo || status === 'signedIn');
  const { data, loading, error, load } = planning;
  useEffect(() => {
    if (ready && !data && !loading && !error) void load();
  }, [ready, data, loading, error, load]);
  return { ...planning, waiting: !data && (loading || !loaded || status === 'loading' || (ready && !error)) };
}
