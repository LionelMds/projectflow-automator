import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react';

import { useAuth } from '@/lib/auth';
import { demoUsedRange } from '@/lib/demo';
import type { ProjectInfo } from '@/lib/planning/quickEntry';
import {
  applyUpsertPlan,
  buildSnapshot,
  editedValues,
  planUpsert,
  sameCells,
  type ProjectRowInput,
  type RepertoireRow,
  type RepertoireSnapshot,
  type RowEdit,
  type UpsertPlan,
} from '@/lib/repertoire/rows';
import { readSnapshot, resolveWorkbook, saveRow, upsertProjectRow, type WorkbookTarget } from '@/lib/repertoire/workbook';
import { useSettings } from '@/lib/settings';

type DemoSheet = ReturnType<typeof demoUsedRange>;
type Directory = { source: string; map: Map<string, ProjectInfo> };

const NO_PROJECTS: ReadonlyMap<string, ProjectInfo> = new Map();

/** The dossiers of a sheet: its rows with a client, a contact or a designation. */
function projectsOf(snapshot: RepertoireSnapshot): ProjectInfo[] {
  const found: ProjectInfo[] = [];
  for (const row of snapshot.rows) {
    const [, , client, contact, designation] = row.texts.map((cell) => (cell ?? '').trim());
    if (client || contact || designation) found.push({ number: row.number, client, contact, designation });
  }
  return found;
}

type State = {
  snapshot: RepertoireSnapshot | null;
  loading: boolean;
  error: string;
  loadedAt: Date | null;
};

type RepertoireValue = State & {
  load: (year: number) => Promise<void>;
  save: (row: RepertoireRow, edit: RowEdit) => Promise<void>;
  rowAt: (sheetRow: number) => RepertoireRow | undefined;
  /**
   * Read a year without changing the year shown by the répertoire tab (the shown
   * snapshot is refreshed when it is that year). Errors are thrown, not stored.
   */
  peek: (year: number) => Promise<RepertoireSnapshot>;
  /** Write a created or updated project (`upsert_project`); the shown year is reloaded. */
  upsertProject: (year: number, project: ProjectRowInput, options: { forceOverwrite: boolean }) => Promise<UpsertPlan>;
  /**
   * The dossiers of every year read so far (client, contact, designation), as the desktop
   * planning keeps them (`_projects`): what a task shows and copies of its dossier.
   */
  projects: ReadonlyMap<string, ProjectInfo>;
  /** Read the years of these dossiers not read yet (`_lookup_projects`); resolves with the dossiers. */
  wantProjects: (numbers: readonly string[]) => Promise<ReadonlyMap<string, ProjectInfo>>;
};

const RepertoireContext = createContext<RepertoireValue | null>(null);

function message(exc: unknown): string {
  return exc instanceof Error ? exc.message : 'Erreur inattendue.';
}

export function RepertoireProvider({ children }: { children: ReactNode }) {
  const { getAccessToken } = useAuth();
  const { settings } = useSettings();
  const [state, setState] = useState<State>({ snapshot: null, loading: false, error: '', loadedAt: null });
  const target = useRef<{ location: string; workbook: WorkbookTarget } | null>(null);
  // Demo worksheets live in memory for the session, edits included.
  const demoSheets = useRef(new Map<number, DemoSheet>());
  // The dossiers belong to one répertoire: another link (or the demo) starts them again.
  const source = settings.demo ? 'demo' : settings.repertoire;
  const directory = useRef<Directory>({ source, map: new Map() });
  const [shownDirectory, setShownDirectory] = useState<Directory>(directory.current);
  const years = useRef<{ source: string; read: Set<number>; pending: Map<number, Promise<void>> }>({
    source,
    read: new Set(),
    pending: new Map(),
  });

  const remember = useCallback(
    (snapshot: RepertoireSnapshot) => {
      const map = new Map(directory.current.source === source ? directory.current.map : []);
      for (const project of projectsOf(snapshot)) map.set(project.number, project);
      directory.current = { source, map };
      setShownDirectory(directory.current);
    },
    [source],
  );

  const demoSheet = useCallback((year: number) => {
    let sheet = demoSheets.current.get(year);
    if (!sheet) {
      sheet = demoUsedRange(year);
      demoSheets.current.set(year, sheet);
    }
    return sheet;
  }, []);

  const workbook = useCallback(async () => {
    if (target.current?.location === settings.repertoire) return target.current.workbook;
    const resolved = await resolveWorkbook(getAccessToken, settings.repertoire);
    target.current = { location: settings.repertoire, workbook: resolved };
    return resolved;
  }, [getAccessToken, settings.repertoire]);

  const load = useCallback(
    async (year: number) => {
      setState((previous) => ({ ...previous, loading: true, error: '' }));
      try {
        const snapshot = settings.demo
          ? buildSnapshot(year, demoSheet(year))
          : await readSnapshot(getAccessToken, await workbook(), year);
        remember(snapshot);
        setState({ snapshot, loading: false, error: '', loadedAt: new Date() });
      } catch (exc) {
        setState((previous) => ({
          snapshot: previous.snapshot?.year === year ? previous.snapshot : null,
          loading: false,
          error: message(exc),
          loadedAt: previous.loadedAt,
        }));
      }
    },
    [getAccessToken, workbook, settings.demo, demoSheet, remember],
  );

  const save = useCallback(
    async (row: RepertoireRow, edit: RowEdit) => {
      const year = state.snapshot?.year;
      if (year === undefined) throw new Error('Répertoire non chargé.');
      const values = editedValues(row, edit);
      if (settings.demo) {
        const sheet = demoSheet(year);
        const index = row.sheetRow - 1;
        if (!sameCells(sheet.values[index] ?? [], row.values)) {
          throw new Error('La ligne a été modifiée entre-temps. Actualisez le répertoire avant de recommencer.');
        }
        sheet.values[index] = values;
        sheet.text[index] = values.map((value) => (value === null ? '' : String(value)));
      } else {
        await saveRow(getAccessToken, await workbook(), year, row, values);
      }
      await load(year);
    },
    [getAccessToken, workbook, load, state.snapshot?.year, settings.demo, demoSheet],
  );

  const shownYear = state.snapshot?.year;

  const peek = useCallback(
    async (year: number) => {
      const snapshot = settings.demo
        ? buildSnapshot(year, demoSheet(year))
        : await readSnapshot(getAccessToken, await workbook(), year);
      remember(snapshot);
      if (shownYear === year) setState({ snapshot, loading: false, error: '', loadedAt: new Date() });
      return snapshot;
    },
    [settings.demo, demoSheet, getAccessToken, workbook, shownYear, remember],
  );

  const wantProjects = useCallback(
    async (numbers: readonly string[]) => {
      if (years.current.source !== source) years.current = { source, read: new Set(), pending: new Map() };
      const state = years.current;
      const wanted = new Set<number>();
      for (const number of numbers) {
        const year = Number(number.trim().slice(0, 4));
        if (year >= 2000 && year < 2100 && !state.read.has(year)) wanted.add(year);
      }
      for (const year of wanted) {
        let pending = state.pending.get(year);
        if (!pending) {
          // A year that cannot be read is asked again later (the next refresh of the planning).
          pending = peek(year)
            .then(() => void state.read.add(year))
            .catch(() => undefined)
            .finally(() => state.pending.delete(year));
          state.pending.set(year, pending);
        }
        await pending;
      }
      return directory.current.source === source ? directory.current.map : NO_PROJECTS;
    },
    [source, peek],
  );

  const projects = shownDirectory.source === source ? shownDirectory.map : NO_PROJECTS;

  const upsertProject = useCallback(
    async (year: number, project: ProjectRowInput, options: { forceOverwrite: boolean }) => {
      const today = new Date();
      let plan: UpsertPlan;
      if (settings.demo) {
        const sheet = demoSheet(year);
        plan = planUpsert(sheet, project, { today, forceOverwrite: options.forceOverwrite });
        demoSheets.current.set(year, applyUpsertPlan(sheet, plan));
      } else {
        plan = await upsertProjectRow(getAccessToken, await workbook(), year, project, {
          today,
          forceOverwrite: options.forceOverwrite,
        });
      }
      if (shownYear === year) await load(year);
      return plan;
    },
    [settings.demo, demoSheet, getAccessToken, workbook, shownYear, load],
  );

  const rowAt = useCallback(
    (sheetRow: number) => state.snapshot?.rows.find((row) => row.sheetRow === sheetRow),
    [state.snapshot],
  );

  const value = useMemo(
    () => ({ ...state, load, save, rowAt, peek, upsertProject, projects, wantProjects }),
    [state, load, save, rowAt, peek, upsertProject, projects, wantProjects],
  );
  return <RepertoireContext.Provider value={value}>{children}</RepertoireContext.Provider>;
}

export function useRepertoire(): RepertoireValue {
  const context = useContext(RepertoireContext);
  if (!context) throw new Error('useRepertoire hors de RepertoireProvider');
  return context;
}
