// État et actions de l'application : le comportement de la maquette, branché sur les vraies intégrations.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { clientRecords, suggestClients } from '../core/clients';
import { locateFiche, readFiche } from '../core/fiche';
import type { DriveItem, PlannerBucket, PlannerMember } from '../core/gateways';
import { formatNumber, makeNumber, nextSubprojectId, parseNumber, ProjectNumberError, type ProjectNumber } from '../core/numero';
import {
  buildDeleteSteps,
  buildProjectSteps,
  findProjectDir,
  precheckCreate,
  repertoireWorkbook,
  root,
  type Ctx,
  type IntegrationChoice,
  type ProjectForm,
} from '../core/project';
import { readSnapshot, updateEditableRow, yearSheets, type RepertoireRow, type RepertoireSnapshot } from '../core/repertoire';
import { saveSettings, isConfigured, type Settings } from '../core/settings';
import { buildSortieSteps, discover, type Inventory } from '../core/sortie';
import { runSteps, type Step, type StepState } from '../core/steps';
import { errorMessage, hhmm } from '../core/text';
import type { Session } from './session';

export type Tab = 'creer' | 'sortie' | 'rep';
export type SheetKind = 'more' | 'row' | 'settings' | 'progress' | 'mail' | 'picker';
export type ConfirmKind = 'update' | 'sync' | 'del';

export interface FormState { year: string; id: string; sub: string; des: string; soc: string; con: string; loc: string; ger: string }
export interface PlannerUI { on: boolean; bucketId: string; members: string[]; due: boolean; days: number; notify: boolean }
export interface ProgressUI {
  title: string;
  states: StepState[];
  done: boolean;
  failed: boolean;
  result: string;
  detail: string;
  cta: string;
  target?: DriveItem;
  print?: DriveItem;
}
export interface SortieUI {
  year: string;
  id: string;
  loading: boolean;
  err: string;
  number: ProjectNumber | null;
  inv: Inventory | null;
  fiche: number;
  mesure: number;
  photos: string[];
  plans: string[];
  selPhoto: number;
}

const EMPTY_FORM = (year: string): FormState => ({ year, id: '', sub: '', des: '', soc: '', con: '', loc: '', ger: '' });
const thisYear = String(new Date().getFullYear());

export function useProjectFlow(session: Session) {
  const { gw, me } = session;
  const [settings, setSettingsState] = useState<Settings>(session.settings);
  const ctx: Ctx = useMemo(() => ({ gw, me, settings }), [gw, me, settings]);

  const [tab, setTab] = useState<Tab>('creer');
  const [sheet, setSheet] = useState<SheetKind | null>(isConfigured(session.settings) ? null : 'settings');
  const [toastText, setToastText] = useState('');
  const [confirm, setConfirm] = useState<ConfirmKind | null>(null);
  const [error, setError] = useState('');
  const [logs, setLogs] = useState<{ t: string; m: string }[]>([]);
  const [busy, setBusy] = useState(false);

  const [years, setYears] = useState<string[]>([thisYear]);
  const [snapshots, setSnapshots] = useState<Record<string, RepertoireSnapshot>>({});
  const [repError, setRepError] = useState('');
  const [repLoading, setRepLoading] = useState(false);
  const [repYear, setRepYear] = useState(thisYear);
  const [search, setSearch] = useState('');

  const [f, setF] = useState<FormState>(EMPTY_FORM(thisYear));
  const [planner, setPlanner] = useState<PlannerUI>({ on: false, bucketId: settings.planner.bucketId, members: [me.id], due: false, days: settings.planner.dueDays || 14, notify: true });
  const [sw, setSw] = useState(false);
  const [ac, setAc] = useState(false);
  const [buckets, setBuckets] = useState<PlannerBucket[]>([]);
  const [members, setMembers] = useState<PlannerMember[]>([]);
  const [plannerError, setPlannerError] = useState('');

  const [selRow, setSelRow] = useState<RepertoireRow | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [progress, setProgress] = useState<ProgressUI | null>(null);
  const [so, setSo] = useState<SortieUI>({ year: thisYear, id: '', loading: false, err: '', number: null, inv: null, fiche: 0, mesure: 0, photos: [], plans: [], selPhoto: -1 });
  const [picker, setPicker] = useState<{ kind: 'photos' | 'plans'; sel: string[] } | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string | null>>({});

  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const toast = useCallback((m: string) => {
    clearTimeout(toastTimer.current);
    setToastText(m);
    toastTimer.current = setTimeout(() => setToastText(''), 2600);
  }, []);
  const log = useCallback((m: string) => setLogs((l) => [...l, { t: hhmm(new Date()), m }].slice(-5)), []);

  const setSettings = useCallback(
    (update: (s: Settings) => Settings) => {
      setSettingsState((prev) => {
        const next = update(prev);
        saveSettings(next, session.settingsKey);
        return next;
      });
    },
    [session.settingsKey],
  );

  // ─── Répertoire ───
  const loadSnapshot = useCallback(
    async (year: string): Promise<RepertoireSnapshot | null> => {
      if (!settings.repertoire || !/^\d{4}$/.test(year)) return null;
      setRepLoading(true);
      try {
        const snap = await readSnapshot(repertoireWorkbook(ctx, await root(ctx, 'repertoire')), Number(year));
        setSnapshots((s) => ({ ...s, [year]: snap }));
        setRepError('');
        return snap;
      } catch (e) {
        setRepError(errorMessage(e));
        return null;
      } finally {
        setRepLoading(false);
      }
    },
    [ctx, settings.repertoire],
  );

  useEffect(() => {
    if (!settings.repertoire) return;
    let cancelled = false;
    (async () => {
      try {
        const names = yearSheets(await repertoireWorkbook(ctx, await root(ctx, 'repertoire')).worksheetNames());
        if (cancelled || !names.length) return;
        setYears(names);
        const initial = names.includes(thisYear) ? thisYear : names[names.length - 1];
        setRepYear(initial);
        setF((x) => ({ ...x, year: names.includes(x.year) ? x.year : initial }));
        setSo((x) => ({ ...x, year: names.includes(x.year) ? x.year : initial }));
      } catch (e) {
        if (!cancelled) setRepError(errorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
    // Les onglets ne changent qu'avec le classeur choisi.
  }, [settings.repertoire?.id]);

  useEffect(() => {
    void loadSnapshot(f.year);
  }, [f.year, loadSnapshot]);

  useEffect(() => {
    if (tab === 'rep') void loadSnapshot(repYear);
  }, [tab, repYear, loadSnapshot]);

  // ─── Planner ───
  useEffect(() => {
    const { enabled, planId } = settings.planner;
    if (!enabled || !planId) {
      setBuckets([]);
      setMembers([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        await session.ensureScopes(['planner']);
        const [b, m] = await Promise.all([gw.planner.listBuckets(planId), gw.planner.listMembers(planId)]);
        if (cancelled) return;
        setBuckets(b);
        setMembers(m);
        setPlannerError('');
        setPlanner((p) => ({ ...p, bucketId: b.some((x) => x.id === p.bucketId) ? p.bucketId : settings.planner.bucketId || b[0]?.id || '' }));
      } catch (e) {
        if (!cancelled) setPlannerError(errorMessage(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gw, session, settings.planner.enabled, settings.planner.planId, settings.planner.bucketId]);

  // ─── Créer ───
  const snap = snapshots[f.year];
  const nextNo = snap?.next?.number ?? '—';
  const nextExcel = snap?.next ? String(snap.next.rowIndex + 1) : '—';
  const records = useMemo(() => clientRecords(Object.values(snapshots).flatMap((s) => s.rows)), [snapshots]);
  const suggestions = useMemo(() => suggestClients(records, f.soc), [records, f.soc]);
  const assignees = members.filter((m) => planner.members.includes(m.id));
  const bucketName = buckets.find((b) => b.id === planner.bucketId)?.name ?? settings.planner.bucketName;

  const formNumber = (): ProjectNumber => makeNumber(f.year, f.id, f.sub);
  const toProjectForm = (number: ProjectNumber, x: FormState = f): ProjectForm => ({
    number,
    designation: x.des,
    societe: x.soc,
    contact: x.con,
    localisation: x.loc,
    gerePar: x.ger,
  });
  const choice = (): IntegrationChoice => ({
    planner: {
      enabled: planner.on && settings.planner.enabled,
      bucketId: planner.bucketId,
      bucketName,
      assignees,
      dueDays: planner.due ? planner.days : null,
      notify: planner.notify,
    },
    solidworks: sw,
    autocad: ac,
  });
  const scopesFor = (c: IntegrationChoice) => {
    const sets: ('planner' | 'mail')[] = [];
    if (c.planner.enabled) sets.push('planner');
    if (settings.outlook.enabled || (c.planner.enabled && c.planner.notify)) sets.push('mail');
    return sets;
  };

  async function runProgress(title: string, steps: Step[], finish: (failed: boolean, warnings: number, states: StepState[]) => Omit<ProgressUI, 'title' | 'states' | 'done' | 'failed'>) {
    setBusy(true);
    setProgress({ title, states: steps.map((s) => ({ label: s.label, status: 'wait' })), done: false, failed: false, result: '', detail: '', cta: '' });
    setSheet('progress');
    const outcome = await runSteps(steps, (states) => setProgress((p) => (p ? { ...p, states } : p)));
    const extra = finish(outcome.failed, outcome.warnings, outcome.states);
    setProgress((p) => (p ? { ...p, ...extra, states: outcome.states, done: true, failed: outcome.failed } : p));
    setBusy(false);
    return outcome;
  }

  const firstError = (states: StepState[]) => states.find((s) => s.status === 'error')?.detail ?? '';

  async function create(mode: 'create' | 'update') {
    if (busy) return;
    setError('');
    if (!isConfigured(settings)) {
      setSheet('settings');
      toast('Renseignez d’abord les chemins OneDrive');
      return;
    }
    let number: ProjectNumber;
    try {
      number = formNumber();
    } catch (e) {
      setError(errorMessage(e));
      return;
    }
    if (!f.des.trim()) {
      setError('Renseignez l’ID projet (3 chiffres ou plus) et la désignation.');
      return;
    }
    const form = toProjectForm(number);
    const c = choice();
    try {
      await session.ensureScopes(scopesFor(c));
      if (mode === 'create') await precheckCreate(ctx, form);
    } catch (e) {
      setError(errorMessage(e));
      return;
    }
    const no = formatNumber(number);
    const { steps, out } = buildProjectSteps(ctx, form, c, mode);
    const verb = mode === 'create' ? 'créé' : 'mis à jour';
    const outcome = await runProgress(`${mode === 'create' ? 'Création' : 'Mise à jour'} · ${no}`, steps, (failed, warnings, states) => ({
      result: failed ? `Projet ${no} non ${verb}` : `Projet ${no} ${verb}`,
      detail: failed
        ? firstError(states)
        : `${states.filter((s) => s.status === 'done').length} étapes réussies${warnings ? ` · ${warnings} avertissement(s)` : ''}${out.notified.length ? ` · e-mail à ${out.notified.join(', ')}` : ''}.`,
      cta: 'Ouvrir dossier',
      target: out.projectDir,
      print: !failed && settings.printAfterCreate ? out.fiche : undefined,
    }));
    if (!outcome.failed) {
      const tags = [c.planner.enabled && 'Planner', out.notified.length && 'e-mail', sw && 'SolidWorks', ac && 'AutoCAD'].filter(Boolean);
      log(`Projet ${no} ${verb}${tags.length ? ' · ' + tags.join(' · ') : ''}`);
      setSw(false);
      setAc(false);
    }
    void loadSnapshot(String(number.year));
  }

  async function loadFromFiche(number: ProjectNumber, base: Partial<FormState> = {}): Promise<boolean> {
    const dir = await findProjectDir(ctx, number);
    if (!dir) return false;
    const fiche = await locateFiche(gw.drive, dir, number);
    const data = await readFiche(gw.workbooks.open(fiche));
    setF((x) => ({
      ...x,
      ...base,
      des: data.designation || base.des || x.des,
      soc: data.societe || base.soc || x.soc,
      con: data.contact || base.con || x.con,
      loc: data.localisation || base.loc || '',
      ger: data.gerePar || base.ger || '',
    }));
    return true;
  }

  function goCreate(patch: Partial<FormState>, msg?: string) {
    setTab('creer');
    setSheet(null);
    setSelRow(null);
    setError('');
    setF((x) => ({ ...x, ...patch }));
    if (msg) toast(msg);
  }

  function openUrl(item: DriveItem | undefined | null, label: string) {
    if (!item?.webUrl) return toast('Élément introuvable dans OneDrive');
    if (item.webUrl.startsWith('demo:')) return toast(`Démo : ${label} (${item.name})`);
    window.open(item.webUrl, '_blank', 'noopener');
  }

  async function printFiche(fiche: DriveItem) {
    if (fiche.webUrl.startsWith('demo:')) return toast('Démo : fiche envoyée en impression (PDF A4)');
    const win = window.open('', '_blank');
    try {
      const blob = await gw.drive.pdf(fiche);
      const url = URL.createObjectURL(blob);
      if (win) win.location.href = url;
      else window.location.href = url;
    } catch (e) {
      win?.close();
      toast(errorMessage(e));
    }
  }

  async function currentFiche(): Promise<{ dir: DriveItem; fiche: DriveItem } | null> {
    try {
      const number = formNumber();
      const dir = await findProjectDir(ctx, number);
      if (!dir) return null;
      return { dir, fiche: await locateFiche(gw.drive, dir, number) };
    } catch {
      return null;
    }
  }

  const moreActions = [
    {
      label: 'Réinitialiser',
      hint: 'vide le formulaire',
      go: () => {
        setSheet(null);
        setError('');
        setSw(false);
        setAc(false);
        setPlanner((p) => ({ ...p, on: false, due: false, members: [me.id] }));
        setF((x) => EMPTY_FORM(x.year));
        toast('Formulaire réinitialisé');
      },
    },
    {
      label: 'Charger',
      hint: 'depuis la fiche',
      go: async () => {
        setSheet(null);
        try {
          const ok = await loadFromFiche(formNumber());
          toast(ok ? `Fiche ${formatNumber(formNumber())} chargée` : 'Aucune fiche pour ce numéro');
        } catch (e) {
          toast(e instanceof ProjectNumberError ? 'Saisissez d’abord l’ID projet' : errorMessage(e));
        }
      },
    },
    {
      label: 'Ouvrir dossier',
      hint: 'OneDrive',
      go: async () => {
        setSheet(null);
        const found = await currentFiche();
        found ? openUrl(found.dir, 'dossier projet ouvert') : toast('Aucun dossier pour ce numéro');
      },
    },
    {
      label: 'Ouvrir fiche',
      hint: 'Excel pour le web',
      go: async () => {
        setSheet(null);
        const found = await currentFiche();
        found ? openUrl(found.fiche, 'fiche ouverte dans Excel') : toast('Aucune fiche pour ce numéro');
      },
    },
    {
      label: 'Imprimer fiche',
      hint: 'PDF A4',
      go: async () => {
        setSheet(null);
        const found = await currentFiche();
        found ? await printFiche(found.fiche) : toast('Aucune fiche pour ce numéro');
      },
    },
    { label: 'Ouvrir répertoire', hint: 'onglet', go: () => { setSheet(null); setTab('rep'); } },
  ];

  // ─── Répertoire : ligne ───
  function tapRow(row: RepertoireRow | null, freeNumber?: string) {
    if (row && row.text.slice(1).some((v) => v.trim())) {
      setSelRow(row);
      setDraft(row.text.slice(1, 5));
      setSheet('row');
      return;
    }
    const n = parseNumber(row?.number ?? freeNumber ?? '');
    goCreate({ ...EMPTY_FORM(String(n.year)), id: n.projectId, sub: n.subprojectId ?? '' });
  }

  async function saveRow() {
    if (!selRow) return;
    const year = selRow.number.slice(0, 4);
    try {
      await updateEditableRow(repertoireWorkbook(ctx, await root(ctx, 'repertoire')), Number(year), selRow, draft);
      toast(`Ligne ${selRow.number} enregistrée`);
      const fresh = await loadSnapshot(year);
      const updated = fresh?.rows.find((r) => r.rowIndex === selRow.rowIndex && r.number === selRow.number);
      if (updated) {
        setSelRow(updated);
        setDraft(updated.text.slice(1, 5));
      }
    } catch (e) {
      toast(errorMessage(e));
    }
  }

  async function syncRow(row: RepertoireRow) {
    const number = parseNumber(row.number);
    setSheet(null);
    let loc = '';
    let ger = '';
    try {
      const dir = await findProjectDir(ctx, number);
      if (dir) {
        const data = await readFiche(gw.workbooks.open(await locateFiche(gw.drive, dir, number)));
        loc = data.localisation;
        ger = data.gerePar;
      }
    } catch {
      // la fiche sera réécrite avec les seules colonnes du tableau
    }
    const form: ProjectForm = { number, designation: row.text[4] ?? '', societe: row.text[2] ?? '', contact: row.text[3] ?? '', localisation: loc, gerePar: ger };
    const c: IntegrationChoice = {
      planner: { enabled: settings.planner.enabled, bucketId: settings.planner.bucketId, bucketName: settings.planner.bucketName, assignees: [], dueDays: null, notify: false },
      solidworks: false,
      autocad: false,
    };
    try {
      await session.ensureScopes(scopesFor(c));
    } catch (e) {
      return toast(errorMessage(e));
    }
    const { steps, out } = buildProjectSteps(ctx, form, c, 'update');
    await runProgress(`Mise à jour · ${row.number}`, steps, (failed, warnings, states) => ({
      result: failed ? `Projet ${row.number} non synchronisé` : `Projet ${row.number} synchronisé`,
      detail: failed ? firstError(states) : `Fiche, répertoire et intégrations actives réappliqués${warnings ? ` · ${warnings} avertissement(s)` : ''}.`,
      cta: 'Ouvrir dossier',
      target: out.projectDir,
    }));
    void loadSnapshot(String(number.year));
  }

  async function deleteRow(row: RepertoireRow) {
    const number = parseNumber(row.number);
    const snapRows = snapshots[String(number.year)]?.rows ?? [];
    const c: ('planner' | 'mail')[] = [];
    if (settings.planner.enabled) c.push('planner');
    if (settings.outlook.enabled) c.push('mail');
    try {
      await session.ensureScopes(c);
    } catch (e) {
      return toast(errorMessage(e));
    }
    setSelRow(null);
    await runProgress(`Suppression · ${row.number}`, buildDeleteSteps(ctx, number, snapRows), (failed, warnings, states) => ({
      result: failed ? `Projet ${row.number} non supprimé` : `Projet ${row.number} supprimé`,
      detail: failed ? firstError(states) : `Numéro libéré${warnings ? ` · ${warnings} avertissement(s)` : ''}. Les fichiers sont dans la corbeille OneDrive.`,
      cta: 'Fermer',
    }));
    void loadSnapshot(String(number.year));
  }

  // ─── Sortie ───
  async function loadSortie() {
    const raw = so.id.trim();
    let number: ProjectNumber;
    try {
      // « 2026-4995 » tel quel, ou « 4995 » / « 4995-2 » complété par l'année choisie.
      number = /^\d{4}-\d{3,}/.test(raw) ? parseNumber(raw) : parseNumber(`${so.year}-${raw}`);
    } catch {
      setSo((x) => ({ ...x, err: 'Saisissez le numéro du projet (ex. 2026-4995).' }));
      return;
    }
    setSo((x) => ({ ...x, loading: true, err: '' }));
    try {
      const dir = await findProjectDir(ctx, number);
      if (!dir) throw new Error(`Dossier projet introuvable pour ${formatNumber(number)}.`);
      const inv = await discover(gw, dir, number);
      setSo((x) => ({ ...x, loading: false, number, inv, fiche: 0, mesure: inv.mesures.length ? 0 : -1, photos: [], plans: [], selPhoto: -1 }));
    } catch (e) {
      setSo((x) => ({ ...x, loading: false, inv: null, err: errorMessage(e) }));
    }
  }

  const byId = (items: DriveItem[] | undefined, ids: string[]) => ids.map((id) => items?.find((i) => i.id === id)).filter((i): i is DriveItem => !!i);

  async function runSortie() {
    if (!so.inv || !so.number || busy) return;
    const fiche = so.inv.fiches[so.fiche];
    if (!fiche) return toast('Aucune fiche dossier à sortir');
    const out: { folder?: DriveItem } = {};
    const no = formatNumber(so.number);
    const steps = buildSortieSteps(gw, so.inv, so.number, {
      fiche,
      mesure: so.mesure >= 0 ? so.inv.mesures[so.mesure] ?? null : null,
      photos: byId(so.inv.photos, so.photos),
      plans: byId(so.inv.plans, so.plans),
    }, out);
    await runProgress(`Sortie · ${no}`, steps, (failed, _w, states) => ({
      result: failed ? 'Sortie interrompue' : 'Dossier de sortie créé',
      detail: failed ? firstError(states) : `${out.folder?.name ?? no} · sources inchangées.`,
      cta: 'Ouvrir le dossier',
      target: out.folder,
    }));
  }

  // Miniatures des photos retenues.
  useEffect(() => {
    const missing = so.photos.filter((id) => !(id in thumbs));
    if (!missing.length || !so.inv) return;
    const items = byId(so.inv.photos, missing);
    void Promise.all(items.map(async (i) => [i.id, await gw.drive.thumbnailUrl(i).catch(() => null)] as const)).then((pairs) =>
      setThumbs((t) => ({ ...t, ...Object.fromEntries(pairs) })),
    );
  }, [so.photos, so.inv]);

  // ─── Confirmations ───
  async function doConfirm() {
    const c = confirm;
    setConfirm(null);
    if (c === 'update') await create('update');
    if (c === 'sync' && selRow) await syncRow(selRow);
    if (c === 'del' && selRow) await deleteRow(selRow);
  }

  function rowSub() {
    if (!selRow) return;
    const n = parseNumber(selRow.number);
    const all = Object.values(snapshots).flatMap((s) => s.rows.map((r) => r.number));
    goCreate(
      { year: String(n.year), id: n.projectId, sub: nextSubprojectId(all, n), des: selRow.text[4], soc: selRow.text[2], con: selRow.text[3], loc: '', ger: settings.initials },
      `Sous-projet : parent ${formatNumber({ ...n, subprojectId: null })}`,
    );
  }

  function rowDup() {
    if (!selRow) return;
    const target = snapshots[repYear]?.next?.number ?? snapshots[thisYear]?.next?.number;
    if (!target) return toast('Aucune ligne disponible pour dupliquer');
    const n = parseNumber(target);
    goCreate({ year: String(n.year), id: n.projectId, sub: '', des: selRow.text[4], soc: selRow.text[2], con: selRow.text[3], loc: '', ger: settings.initials }, `Dupliqué vers ${target}`);
  }

  async function rowLoad() {
    if (!selRow) return;
    const n = parseNumber(selRow.number);
    const base = { year: String(n.year), id: n.projectId, sub: n.subprojectId ?? '', des: selRow.text[4], soc: selRow.text[2], con: selRow.text[3], loc: '', ger: '' };
    goCreate(base, `Fiche ${selRow.number} chargée`);
    try {
      await loadFromFiche(n, base);
    } catch {
      // les colonnes du répertoire suffisent
    }
  }

  return {
    session, settings, setSettings, ctx, gw, me,
    tab, setTab, sheet, setSheet, toastText, toast, confirm, setConfirm, error, setError, logs, busy,
    years, snapshots, repYear, setRepYear, search, setSearch, repError, repLoading, loadSnapshot,
    f, setF, planner, setPlanner, sw, setSw, ac, setAc, buckets, members, plannerError, assignees, bucketName,
    nextNo, nextExcel, suggestions, moreActions,
    create, goCreate, openUrl, printFiche,
    selRow, setSelRow, draft, setDraft, tapRow, saveRow, rowSub, rowDup, rowLoad,
    progress, setProgress,
    so, setSo, loadSortie, runSortie, picker, setPicker, thumbs,
    doConfirm,
  };
}

export type PF = ReturnType<typeof useProjectFlow>;
