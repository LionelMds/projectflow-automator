/**
 * « Création projet », ported from `core/project_service.py` and the creation part of
 * `ui/controller.py`, done through Microsoft Graph instead of the synced folders:
 * the reference folder copied as `<racine>/<year>/<YYYY-NNNN>`, the fiche renamed and
 * filled, the répertoire row written, the Planner task created or brought up to date.
 * Each step reports to the journal (« + … », « ! … », « -> … ») as on the desktop.
 */

import { findChildFile, findChildFolder, type DriveGateway, type DriveItem } from '@/lib/projects/drive';
import {
  cellText,
  ficheData,
  ficheWrites,
  infoWouldChange,
  isWorkbookName,
  sortFicheCandidates,
  standardFicheName,
  type FicheCandidate,
  type FicheData,
  type FicheProject,
} from '@/lib/projects/fiche';
import { locateProject, REFERENCE_LABEL } from '@/lib/projects/location';
import {
  isSubproject,
  numberText,
  parentNumber,
  parseFormNumber,
  type ProjectNumber,
} from '@/lib/projects/numbers';
import type { PlannerGateway } from '@/lib/projects/planner';
import type { ProjectRowInput, RepertoireRow, RepertoireSnapshot, UpsertPlan } from '@/lib/repertoire/rows';
import type { Settings } from '@/lib/settings';

export type PlannerFormData = {
  enabled: boolean;
  bucketId: string;
  bucketName: string;
  assigneeIds: string[];
  assigneeNames: string[];
  dueEnabled: boolean;
  dueDays: number;
};

export type CreationForm = {
  year: string;
  projectId: string;
  subprojectId: string;
  designation: string;
  societe: string;
  contact: string;
  localisation: string;
  gerePar: string;
  planner: PlannerFormData;
};

/** Answer to « Ce projet existe déjà… » (Oui / Non / Annuler on the desktop). */
export type ExistingDecision = 'yes' | 'no' | 'cancel';

export type CreationDeps = {
  drive: DriveGateway;
  planner: PlannerGateway;
  settings: Settings;
  repertoire: {
    peek(year: number): Promise<RepertoireSnapshot>;
    upsert(year: number, project: ProjectRowInput, options: { forceOverwrite: boolean }): Promise<UpsertPlan>;
  };
  log: (line: string) => void;
  confirmExisting: () => Promise<ExistingDecision>;
  today?: () => Date;
};

export type CreationOutcome = {
  status: 'created' | 'ready' | 'updated' | 'cancelled';
  number: string;
  folder: DriveItem | null;
  folderPath: string;
  fiche: DriveItem | null;
  /** Steps that failed after the folder existed (fiche, répertoire, Planner). */
  failures: number;
};

export const EXISTING_QUESTION = {
  title: 'Projet existant',
  message: 'Ce projet existe déjà et les informations du formulaire diffèrent de la fiche existante.',
  yes: 'Oui : mettre à jour la fiche et le répertoire, puis relancer Planner.',
  no: 'Non : conserver les informations existantes et relancer seulement Planner.',
  cancel: 'Annuler : ne rien faire.',
};

const B_TO_E_FILLED =
  'Les colonnes B à E du répertoire contiennent déjà des informations. Utilisez « Mettre à jour » pour remplacer.';

export function errorMessage(exc: unknown): string {
  if (exc instanceof Error) return exc.message.trim() || exc.name;
  return String(exc);
}

export function ficheProject(number: ProjectNumber, form: CreationForm): FicheProject {
  return {
    number,
    designation: form.designation.trim(),
    societe: form.societe.trim(),
    contact: form.contact.trim(),
    localisation: form.localisation.trim(),
    gere_par: form.gerePar.trim(),
  };
}

export function rowInput(number: ProjectNumber, form: CreationForm): ProjectRowInput {
  return {
    number: numberText(number),
    parent: isSubproject(number) ? numberText(parentNumber(number)) : null,
    societe: form.societe,
    contact: form.contact,
    designation: form.designation,
  };
}

/**
 * Refuse before any copy what the répertoire write would refuse afterwards:
 * a number missing from the year's worksheet, or a row already filled.
 */
export function checkRepertoireRow(snapshot: RepertoireSnapshot, number: ProjectNumber, forceOverwrite: boolean): void {
  const text = numberText(number);
  const row = snapshot.rows.find((candidate) => candidate.number === text);
  if (isSubproject(number)) {
    if (row) return;
    const parent = numberText(parentNumber(number));
    if (!snapshot.rows.some((candidate) => candidate.number === parent)) {
      throw new Error(`Projet parent introuvable dans le répertoire : ${parent}`);
    }
    return;
  }
  if (!row) throw new Error(`Projet introuvable dans le répertoire : ${text}`);
  if (!forceOverwrite && row.values.slice(1, 5).some((value) => cellText(value) !== '')) throw new Error(B_TO_E_FILLED);
}

type Candidates = {
  candidates: FicheCandidate<DriveItem>[];
  /** Folder listing each candidate is in, for the standard name check. */
  siblings: Map<string, DriveItem[]>;
};

/** `list_candidates`: workbooks of the folder and of its `<number>` subfolder, in the desktop's order. */
async function ficheCandidates(drive: DriveGateway, folder: DriveItem, number: ProjectNumber | null): Promise<Candidates> {
  const rootChildren = await drive.children(folder);
  const siblings = new Map<string, DriveItem[]>();
  const candidates: FicheCandidate<DriveItem>[] = [];
  const add = (children: DriveItem[], path: string[]) => {
    for (const item of children) {
      if (item.folder || !isWorkbookName(item.name)) continue;
      candidates.push({ name: item.name, folder: path, item });
      siblings.set(item.id, children);
    }
  };
  add(rootChildren, []);
  const nested = number ? findChildFolder(rootChildren, numberText(number)) : null;
  if (nested) add(await drive.children(nested), [nested.name]);
  return { candidates: sortFicheCandidates(candidates, number), siblings };
}

/** `standardize_fiche_name`: renamed « <number> - Fiche dossier clients.xlsx », unless that name is taken. */
async function standardize(drive: DriveGateway, found: Candidates, candidate: FicheCandidate<DriveItem>, number: ProjectNumber) {
  const standard = standardFicheName(number);
  if (candidate.item.name === standard) return candidate.item;
  const existing = findChildFile(found.siblings.get(candidate.item.id) ?? [], standard);
  if (existing) return existing;
  return drive.rename(candidate.item, standard);
}

async function writeFiche(
  deps: CreationDeps,
  item: DriveItem,
  project: FicheProject,
  newFiche: boolean,
): Promise<void> {
  const today = (deps.today ?? (() => new Date()))();
  const cells = await deps.drive.readFiche(item);
  await deps.drive.writeCells(item, ficheWrites(cells, project, { initials: deps.settings.initials, newFiche, today }));
}

/** `fill_subproject_fiche`: the subproject's fiche, copied from the parent's when missing, then filled. */
async function fillSubprojectFiche(
  deps: CreationDeps,
  folder: DriveItem,
  folderPath: string,
  number: ProjectNumber,
  project: FicheProject,
): Promise<{ item: DriveItem; created: boolean }> {
  const { drive } = deps;
  const standard = standardFicheName(number);
  const rootChildren = await drive.children(folder);
  const nested = findChildFolder(rootChildren, numberText(number));
  const nestedChildren = nested ? await drive.children(nested) : [];
  let target = findChildFile(rootChildren, standard) ?? findChildFile(nestedChildren, standard);
  let created = false;
  if (!target) {
    const parent = parentNumber(number);
    const parentStandard = standardFicheName(parent);
    const parentNested = findChildFolder(rootChildren, numberText(parent));
    const parentChildren = parentNested ? await drive.children(parentNested) : [];
    let source = findChildFile(rootChildren, parentStandard) ?? findChildFile(parentChildren, parentStandard);
    if (!source) {
      const workbooks = (children: DriveItem[], path: string[]) =>
        children.filter((item) => !item.folder && isWorkbookName(item.name)).map((item) => ({ name: item.name, folder: path, item }));
      const sorted = sortFicheCandidates(
        [...workbooks(rootChildren, []), ...workbooks(parentChildren, parentNested ? [parentNested.name] : [])],
        parent,
      );
      if (!sorted[0]) throw new Error(`Aucune fiche Excel trouvée pour ${numberText(parent)} dans ${folderPath}`);
      source = sorted[0].item;
    }
    target = await drive.copy(source, nested ?? folder, standard, 'la fiche du projet parent');
    created = true;
  }
  await writeFiche(deps, target, project, created);
  return { item: target, created };
}

async function applyRepertoire(deps: CreationDeps, number: ProjectNumber, form: CreationForm, forceOverwrite: boolean) {
  try {
    const plan = await deps.repertoire.upsert(number.year, rowInput(number, form), { forceOverwrite });
    deps.log(
      plan.kind === 'insert'
        ? `+ Répertoire mis à jour : ligne ${plan.sheetRow} insérée après ${numberText(parentNumber(number))}`
        : `+ Répertoire mis à jour : ligne ${plan.sheetRow}`,
    );
    return 0;
  } catch (exc) {
    deps.log(`! Répertoire non mis à jour : ${errorMessage(exc)}`);
    return 1;
  }
}

/** `_apply_planner` and its journal lines (`_log_creation_integrations`). */
async function applyPlanner(deps: CreationDeps, number: ProjectNumber, form: CreationForm): Promise<number> {
  const { settings, log } = deps;
  if (!settings.plannerEnabled) return 0;
  if (!form.planner.enabled) {
    log("! Planner actif mais aucune tâche n'a été associée à ce projet");
    return 0;
  }
  try {
    const result = await deps.planner.ensureProjectTask({
      planId: settings.plannerPlanId,
      bucketId: form.planner.bucketId.trim() || settings.plannerBucketId,
      assigneeIds: form.planner.assigneeIds,
      defaultAssigneeIds: settings.plannerAssigneeIds,
      dueDays: form.planner.dueEnabled ? form.planner.dueDays : null,
      number: numberText(number),
      designation: form.designation,
    });
    log(result.created ? '+ Tâche Planner créée' : result.updated ? '+ Tâche Planner mise à jour' : '+ Tâche Planner déjà existante');
    return 0;
  } catch (exc) {
    log(`! Tâche Planner non créée : ${errorMessage(exc)}`);
    return 1;
  }
}

function logPosteOnly(deps: CreationDeps) {
  deps.log('-> Fichiers CAO et dossier Outlook : à ajouter depuis le poste');
}

/** `_existing_project_update_decision`: ask only when the form would change the fiche. */
async function existingDecision(
  deps: CreationDeps,
  folder: DriveItem,
  number: ProjectNumber,
  project: FicheProject,
): Promise<ExistingDecision> {
  let data: FicheData | null = null;
  try {
    const found = await ficheCandidates(deps.drive, folder, number);
    if (found.candidates[0]) data = ficheData(await deps.drive.readFiche(found.candidates[0].item));
  } catch {
    data = null;
  }
  if (!data || !infoWouldChange(project, data)) return 'no';
  return deps.confirmExisting();
}

/** « Créer » (`create_project`; a subproject goes to `create_subproject`). */
export async function createProject(deps: CreationDeps, form: CreationForm): Promise<CreationOutcome> {
  const { drive, settings, log } = deps;
  const number = parseFormNumber(form.year, form.projectId, form.subprojectId);
  const project = ficheProject(number, form);
  if (isSubproject(number)) return createSubproject(deps, number, project, form);
  const text = numberText(number);

  const location = await locateProject(drive, settings.racine, number);
  const folderExisted = location.projectFolder !== null;
  let update = true;
  if (location.projectFolder) {
    const decision = await existingDecision(deps, location.projectFolder, number, project);
    if (decision === 'cancel') {
      log('! Création annulée');
      return { status: 'cancelled', number: text, folder: location.projectFolder, folderPath: location.projectPath, fiche: null, failures: 0 };
    }
    update = decision === 'yes';
  }

  let folder = location.projectFolder;
  if (!folder) {
    const reference = await drive.resolve(settings.reference, REFERENCE_LABEL);
    checkRepertoireRow(await deps.repertoire.peek(number.year), number, false);
    let yearFolder = location.yearFolder;
    if (!yearFolder) {
      yearFolder = await drive.createFolder(location.racine, String(number.year));
      log(`+ Dossier créé : ${location.yearPath}`);
    }
    log('-> Copie du dossier de référence…');
    folder = await drive.copy(reference, yearFolder, location.folderName, 'du dossier de référence');
    log(`+ Projet créé : ${location.projectPath}`);
  } else {
    log(`+ Projet existant réappliqué : ${location.projectPath}`);
    log('-> Dossier existant conservé : le dossier de référence n’est pas recopié');
  }

  let failures = 0;
  let fiche: DriveItem | null = null;
  if (!folderExisted || update) {
    try {
      let found = await ficheCandidates(drive, folder, number);
      let newFiche = !folderExisted;
      if (!found.candidates[0]) {
        // `copy_reference_tree` on an existing folder brings a missing fiche back.
        const reference = await drive.resolve(settings.reference, REFERENCE_LABEL);
        const model = (await ficheCandidates(drive, reference, null)).candidates[0];
        if (!model) throw new Error(`Aucune fiche Excel trouvée pour ${text} dans ${location.projectPath}`);
        await drive.copy(model.item, folder, standardFicheName(number), 'la fiche de référence');
        log('+ Fiche copiée depuis le dossier de référence');
        found = await ficheCandidates(drive, folder, number);
        newFiche = true;
      }
      const chosen = found.candidates[0];
      if (!chosen) throw new Error(`Aucune fiche Excel trouvée pour ${text} dans ${location.projectPath}`);
      fiche = await standardize(drive, found, chosen, number);
      await writeFiche(deps, fiche, project, newFiche);
      log(`+ Fiche remplie : ${fiche.name}`);
    } catch (exc) {
      failures += 1;
      log(`! Fiche non remplie : ${errorMessage(exc)}`);
    }
    failures += await applyRepertoire(deps, number, form, folderExisted && update);
  } else {
    try {
      fiche = (await ficheCandidates(drive, folder, number)).candidates[0]?.item ?? null;
    } catch {
      fiche = null;
    }
    log('+ Informations existantes conservées');
  }

  logPosteOnly(deps);
  failures += await applyPlanner(deps, number, form);
  return {
    status: folderExisted ? 'ready' : 'created',
    number: text,
    folder,
    folderPath: location.projectPath,
    fiche,
    failures,
  };
}

async function createSubproject(
  deps: CreationDeps,
  number: ProjectNumber,
  project: FicheProject,
  form: CreationForm,
): Promise<CreationOutcome> {
  const { drive, settings, log } = deps;
  const location = await locateProject(drive, settings.racine, number);
  if (!location.projectFolder) throw new Error(`Dossier du projet parent introuvable : ${location.projectPath}`);
  checkRepertoireRow(await deps.repertoire.peek(number.year), number, true);
  const { item, created } = await fillSubprojectFiche(deps, location.projectFolder, location.projectPath, number, project);
  log(created ? `+ Fiche du sous-projet créée : ${item.name}` : `+ Fiche remplie : ${item.name}`);
  let failures = await applyRepertoire(deps, number, form, true);
  logPosteOnly(deps);
  failures += await applyPlanner(deps, number, form);
  return {
    status: created ? 'created' : 'ready',
    number: numberText(number),
    folder: location.projectFolder,
    folderPath: location.projectPath,
    fiche: item,
    failures,
  };
}

/** « Mettre à jour » (`update_project`): fiche, répertoire (B:E replaced), Planner. */
export async function updateProject(deps: CreationDeps, form: CreationForm): Promise<CreationOutcome> {
  const { drive, settings, log } = deps;
  const number = parseFormNumber(form.year, form.projectId, form.subprojectId);
  const project = ficheProject(number, form);
  const location = await locateProject(drive, settings.racine, number);
  if (!location.projectFolder) throw new Error(`Dossier projet introuvable : ${location.projectPath}`);

  let fiche: DriveItem;
  if (isSubproject(number)) {
    fiche = (await fillSubprojectFiche(deps, location.projectFolder, location.projectPath, number, project)).item;
  } else {
    const found = await ficheCandidates(drive, location.projectFolder, number);
    const chosen = found.candidates[0];
    if (!chosen) throw new Error(`Aucune fiche Excel trouvée pour ${numberText(number)} dans ${location.projectPath}`);
    fiche = await standardize(drive, found, chosen, number);
    await writeFiche(deps, fiche, project, false);
  }
  log(`+ Fiche remplie : ${fiche.name}`);
  let failures = await applyRepertoire(deps, number, form, true);
  failures += await applyPlanner(deps, number, form);
  log(`+ Projet mis à jour : ${fiche.name}`);
  return {
    status: 'updated',
    number: numberText(number),
    folder: location.projectFolder,
    folderPath: location.projectPath,
    fiche,
    failures,
  };
}

export type LoadedProject = {
  number: ProjectNumber;
  designation: string;
  societe: string;
  contact: string;
  localisation: string;
  gerePar: string;
  folder: DriveItem | null;
  folderPath: string;
  fiche: DriveItem | null;
};

/**
 * « Charger » (`_load_project_number`): the fiche of the dossier, completed from its
 * répertoire row when a field is empty there (as `_sortie_card_details` does).
 */
export async function loadProject(
  deps: Pick<CreationDeps, 'drive' | 'settings' | 'repertoire' | 'log'>,
  year: string,
  projectId: string,
  subprojectId: string,
): Promise<LoadedProject> {
  const { drive, settings, log } = deps;
  const number = parseFormNumber(year, projectId, subprojectId);
  const text = numberText(number);

  let row: RepertoireRow | undefined;
  try {
    row = (await deps.repertoire.peek(number.year)).rows.find((candidate) => candidate.number === text);
  } catch (exc) {
    log(`! Répertoire non lu : ${errorMessage(exc)}`);
  }

  let folder: DriveItem | null = null;
  let folderPath = '';
  let fiche: DriveItem | null = null;
  let data: FicheData | null = null;
  try {
    const location = await locateProject(drive, settings.racine, number);
    folder = location.projectFolder;
    folderPath = location.projectPath;
    if (!folder) throw new Error(`Dossier projet introuvable : ${location.projectPath}`);
    const found = await ficheCandidates(drive, folder, number);
    const chosen = found.candidates[0];
    if (!chosen) throw new Error(`Aucune fiche Excel trouvée pour ${text} dans ${location.projectPath}`);
    if (found.candidates.length > 1) log(`-> ${found.candidates.length} fiches trouvées : ${chosen.name} retenue`);
    fiche = chosen.item;
    data = ficheData(await drive.readFiche(chosen.item));
  } catch (exc) {
    if (!row) throw exc;
    log(`! ${errorMessage(exc)}`);
  }

  const repertoire = row ? row.texts : [];
  const hasRow = repertoire.slice(2, 5).some((value) => value.trim() !== '');
  if (!data && !hasRow) throw new Error(`Le numéro ${text} est disponible et ne contient aucun projet à charger.`);

  if (data) {
    if (data.number && data.number !== text) log(`! C3 contient ${data.number}, attendu ${text}`);
    log(`+ Fiche chargée : ${fiche?.name ?? ''}`);
  } else {
    log(`+ Ligne du répertoire chargée : ${text}`);
  }
  return {
    number,
    designation: data?.designation || repertoire[4] || '',
    societe: data?.societe || repertoire[2] || '',
    contact: data?.contact || repertoire[3] || '',
    localisation: data?.localisation ?? '',
    gerePar: data?.gere_par ?? '',
    folder,
    folderPath,
    fiche,
  };
}

/**
 * Folder and fiche of a dossier, as « Ouvrir dossier » and « Ouvrir fiche » find them:
 * the folder under the racine, its first fiche in the desktop's order (a subproject's first).
 */
export async function findDossier(
  drive: DriveGateway,
  racine: string,
  number: ProjectNumber,
): Promise<{ folder: DriveItem | null; fiche: DriveItem | null; folderPath: string }> {
  const location = await locateProject(drive, racine, number);
  if (!location.projectFolder) return { folder: null, fiche: null, folderPath: location.projectPath };
  const found = await ficheCandidates(drive, location.projectFolder, number);
  return { folder: location.projectFolder, fiche: found.candidates[0]?.item ?? null, folderPath: location.projectPath };
}

/** Distinct clients and contacts of the répertoire rows, for the suggestions under the fields. */
export function clientSuggestions(
  rows: readonly RepertoireRow[],
  field: 'societe' | 'contact',
  typed: string,
  societe = '',
): string[] {
  const fold = (value: string) =>
    value
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .trim();
  const wanted = fold(typed);
  const company = fold(societe);
  const seen = new Map<string, string>();
  for (const row of [...rows].reverse()) {
    const client = row.texts[2]?.trim() ?? '';
    const contact = row.texts[3]?.trim() ?? '';
    const value = field === 'societe' ? client : contact;
    if (!value) continue;
    if (field === 'contact' && company && fold(client) !== company) continue;
    const key = fold(value);
    if (wanted && (!key.includes(wanted) || key === wanted)) continue;
    if (!seen.has(key)) seen.set(key, value);
  }
  return [...seen.values()].slice(0, 4);
}
