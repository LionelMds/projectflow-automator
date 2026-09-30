// Création, mise à jour et suppression d'un projet (repris de core/project_service.py),
// entièrement dans OneDrive/SharePoint via les passerelles.
import { applyCadTemplates } from './cad';
import { copyTreeNoOverwrite, ensurePath, findChild } from './drive';
import { fillFiche, listFicheCandidates, standardFicheName, standardizeFiche, type FicheInput } from './fiche';
import type { DriveItem, Gateways, PlannerMember, UserInfo } from './gateways';
import { formatNumber, isSubproject, parentOf, projectFolderName, type ProjectNumber } from './numero';
import { notificationHtml, notificationSubject, type NotificationInput } from './notification';
import { deleteFolderPath, ensureFolderPath, renderPaths } from './outlook';
import { deleteProjectTasks, ensureProjectTask } from './planner';
import { clearProjectRows, deletionGroup, infoColumnsEmpty, readSnapshot, upsertProject, validateDeletion, type RepertoireRow } from './repertoire';
import { outlookPaths, type Settings } from './settings';
import type { Step } from './steps';

export type ProjectForm = FicheInput;

export interface IntegrationChoice {
  planner: {
    enabled: boolean;
    bucketId: string;
    bucketName: string;
    assignees: PlannerMember[];
    dueDays: number | null;
    notify: boolean;
  };
  solidworks: boolean;
  autocad: boolean;
}

export interface Ctx {
  gw: Gateways;
  settings: Settings;
  me: UserInfo;
}

export interface ProjectOutput {
  projectDir?: DriveItem;
  fiche?: DriveItem;
  taskUrl?: string;
  notified: string[];
}

function need<T>(value: T | null | undefined, label: string): T {
  if (!value) throw new Error(`Paramètre manquant : ${label}. Ouvrez Paramètres pour le renseigner.`);
  return value;
}

export async function root(ctx: Ctx, key: 'racine' | 'reference' | 'repertoire'): Promise<DriveItem> {
  const labels = { racine: 'racine projets', reference: 'dossier de référence', repertoire: 'répertoire chantier' };
  const ref = need(ctx.settings[key], labels[key]);
  return { driveId: ref.driveId, id: ref.id, name: ref.name, isFolder: key !== 'repertoire', size: 0, lastModified: '', webUrl: ref.webUrl };
}

export function repertoireWorkbook(ctx: Ctx, item: DriveItem) {
  return ctx.gw.workbooks.open(item);
}

/** Dossier projet existant (racine/année/numéro principal), ou null. */
export async function findProjectDir(ctx: Ctx, number: ProjectNumber): Promise<DriveItem | null> {
  const racine = await root(ctx, 'racine');
  const year = await findChild(ctx.gw.drive, racine, String(number.year));
  return year ? findChild(ctx.gw.drive, year, projectFolderName(number)) : null;
}

export function folderPathLabel(ctx: Ctx, number: ProjectNumber): string {
  return `…\\${ctx.settings.racine?.name ?? 'Projets'}\\${number.year}\\${projectFolderName(number)}`;
}

/** Vérifications faites avant toute écriture, pour ne pas créer un dossier orphelin. */
export async function precheckCreate(ctx: Ctx, form: ProjectForm): Promise<void> {
  const rep = await root(ctx, 'repertoire');
  const snap = await readSnapshot(repertoireWorkbook(ctx, rep), form.number.year);
  const number = formatNumber(form.number);
  if (isSubproject(form.number)) {
    const parent = formatNumber(parentOf(form.number));
    if (!snap.rows.some((r) => r.number === parent)) throw new Error(`Projet parent introuvable dans le répertoire : ${parent}`);
    if (!(await findProjectDir(ctx, form.number))) throw new Error(`Dossier du projet parent introuvable : ${folderPathLabel(ctx, form.number)}`);
    return;
  }
  const row = snap.rows.find((r) => r.number === number);
  if (!row) throw new Error(`Projet introuvable dans le répertoire : ${number}`);
  if (!infoColumnsEmpty(row.values)) {
    throw new Error('Les colonnes B à E du répertoire contiennent déjà des informations. Utilisez « Mettre à jour » pour remplacer.');
  }
}

export function buildProjectSteps(
  ctx: Ctx,
  form: ProjectForm,
  choice: IntegrationChoice,
  mode: 'create' | 'update',
): { steps: Step[]; out: ProjectOutput } {
  const { gw, settings } = ctx;
  const number = formatNumber(form.number);
  const sub = isSubproject(form.number);
  const out: ProjectOutput = { notified: [] };
  const steps: Step[] = [];
  const dir = () => need(out.projectDir, 'dossier projet');
  let newlyAssigned: PlannerMember[] = [];

  if (mode === 'update') {
    steps.push({
      label: 'Dossier projet trouvé',
      run: async () => {
        out.projectDir = (await findProjectDir(ctx, form.number)) ?? undefined;
        if (!out.projectDir) throw new Error(`Dossier projet introuvable : ${folderPathLabel(ctx, form.number)}`);
      },
    });
    steps.push({
      label: sub ? 'Fiche du sous-projet mise à jour' : 'Fiche mise à jour',
      run: async () => {
        out.fiche = await (sub ? subprojectFiche(ctx, dir(), form) : standardizeFiche(gw.drive, dir(), form.number));
        await fillFiche(gw.workbooks.open(out.fiche), form, { newFiche: false, initials: settings.initials });
      },
    });
  } else if (sub) {
    steps.push({
      label: 'Fiche du sous-projet créée',
      run: async () => {
        out.projectDir = (await findProjectDir(ctx, form.number)) ?? undefined;
        if (!out.projectDir) throw new Error(`Dossier du projet parent introuvable : ${folderPathLabel(ctx, form.number)}`);
        const existing = await findSubprojectFiche(ctx, out.projectDir, form.number);
        out.fiche = existing ?? (await subprojectFiche(ctx, out.projectDir, form));
        await fillFiche(gw.workbooks.open(out.fiche), form, { newFiche: !existing, initials: settings.initials });
        if (existing) return 'Fiche du sous-projet mise à jour';
      },
    });
  } else {
    let existingFiches = new Set<string>();
    steps.push({
      label: 'Dossier projet créé',
      run: async () => {
        const racine = await root(ctx, 'racine');
        const year = await ensurePath(gw.drive, racine, [String(form.number.year)]);
        const found = await findChild(gw.drive, year, projectFolderName(form.number));
        out.projectDir = found ?? (await gw.drive.ensureFolder(year, projectFolderName(form.number)));
        existingFiches = new Set((await listFicheCandidates(gw.drive, out.projectDir, form.number)).map((f) => f.id));
        if (found) return 'Dossier projet existant réutilisé';
      },
    });
    steps.push({
      label: 'Dossier de référence copié, sans écrasement',
      run: async () => {
        const { copied, skipped } = await copyTreeNoOverwrite(gw.drive, await root(ctx, 'reference'), dir());
        return `Dossier de référence copié · ${copied.length} fichier(s)${skipped ? `, ${skipped} déjà présent(s)` : ''}`;
      },
    });
    steps.push({
      label: 'Fiche renseignée · date de création en B9',
      run: async () => {
        const [located] = await listFicheCandidates(gw.drive, dir(), form.number);
        out.fiche = await standardizeFiche(gw.drive, dir(), form.number);
        const newFiche = !located || !existingFiches.has(located.id);
        await fillFiche(gw.workbooks.open(out.fiche), form, { newFiche, initials: settings.initials });
      },
    });
  }

  steps.push({
    label: sub && mode === 'create' ? 'Ligne insérée dans le groupe du parent' : 'Répertoire chantier mis à jour (OneDrive)',
    run: async () => {
      const rep = await root(ctx, 'repertoire');
      await upsertProject(repertoireWorkbook(ctx, rep), form, { forceOverwrite: mode === 'update' });
    },
  });

  if (settings.outlook.enabled && !sub) {
    steps.push({
      label: 'Arborescence Outlook créée',
      isolated: true,
      run: async () => {
        for (const path of renderPaths(outlookPaths(settings.outlook.arborescence), form.number, form.designation)) {
          await ensureFolderPath(gw.mail, path);
        }
      },
    });
  }

  const pl = choice.planner;
  if (pl.enabled && settings.planner.enabled) {
    steps.push({
      label: `Tâche Planner « ${number} » · ${pl.bucketName || settings.planner.bucketName}`,
      isolated: true,
      run: async () => {
        const assignees = pl.assignees.map((a) => a.id);
        const before = new Set((await gw.planner.listTasks(settings.planner.planId)).flatMap((t) => (t.title.trim() === number || t.title.startsWith(number + ' ') ? t.assignments : [])));
        const result = await ensureProjectTask(
          gw.planner,
          { planId: settings.planner.planId, bucketId: pl.bucketId || settings.planner.bucketId, number, designation: form.designation, assigneeIds: assignees, dueDays: pl.dueDays },
          async () => ctx.me.id,
        );
        out.taskUrl = gw.planner.taskUrl(result.task, settings.planner.planId);
        newlyAssigned = pl.assignees.filter((a) => !before.has(a.id));
        if (result.created) return;
        return result.updated ? `Tâche Planner « ${number} » mise à jour` : `Tâche Planner « ${number} » déjà à jour`;
      },
    });
    if (pl.notify && pl.assignees.length) {
      steps.push({
        label: `E-mail de notification envoyé à ${pl.assignees.map((a) => a.displayName || a.email).join(', ')}`,
        isolated: true,
        run: async () => {
          const recipients = newlyAssigned.filter((a) => a.email);
          if (!recipients.length) return 'Aucune nouvelle assignation : pas d’e-mail envoyé';
          const input = notificationInput(ctx, form, choice, out);
          await gw.mail.send({
            to: recipients.map((a) => ({ name: a.displayName, email: a.email })),
            subject: notificationSubject(number, form.designation),
            html: notificationHtml(input),
          });
          out.notified = recipients.map((a) => a.displayName || a.email);
          return `E-mail de notification envoyé à ${out.notified.join(', ')}`;
        },
      });
    }
  }

  if (choice.solidworks) {
    steps.push({
      label: 'Arborescence SolidWorks copiée',
      isolated: true,
      run: async () => {
        const r = await applyCadTemplates(ctx, dir(), form.number, 'solidworks');
        return { warn: `${r.created} fichier(s) copié(s), ${r.skipped} déjà présent(s). Références d’assemblage et propriétés à relier depuis ProjectFlow sur le poste (SolidWorks Document Manager).` };
      },
    });
  }
  if (choice.autocad) {
    steps.push({
      label: 'Modèle AutoCAD copié',
      isolated: true,
      run: async () => {
        const r = await applyCadTemplates(ctx, dir(), form.number, 'autocad');
        return r.created ? `Modèle AutoCAD copié · ${r.created} fichier(s)` : 'Modèle AutoCAD déjà présent';
      },
    });
  }
  return { steps, out };
}

export function notificationInput(ctx: Ctx, form: ProjectForm, choice: IntegrationChoice, out: ProjectOutput): NotificationInput {
  const days = choice.planner.dueDays;
  return {
    number: formatNumber(form.number),
    designation: form.designation,
    societe: form.societe,
    contact: form.contact,
    localisation: form.localisation,
    gerePar: form.gerePar,
    initials: ctx.settings.initials,
    planName: ctx.settings.planner.planName,
    bucketName: choice.planner.bucketName || ctx.settings.planner.bucketName,
    due: days ? new Date(Date.now() + days * 86_400_000) : null,
    folderPath: folderPathLabel(ctx, form.number),
    folderUrl: out.projectDir?.webUrl ?? ctx.settings.racine?.webUrl ?? '',
    taskUrl: out.taskUrl ?? '',
    senderName: ctx.me.displayName,
  };
}

async function findSubprojectFiche(ctx: Ctx, projectDir: DriveItem, number: ProjectNumber): Promise<DriveItem | null> {
  const name = standardFicheName(number).toLowerCase();
  const nested = await findChild(ctx.gw.drive, projectDir, formatNumber(number));
  for (const d of nested ? [projectDir, nested] : [projectDir]) {
    const hit = (await ctx.gw.drive.listChildren(d)).find((c) => !c.isFolder && c.name.toLowerCase() === name);
    if (hit) return hit;
  }
  return null;
}

/** Fiche du sous-projet : l'existante, sinon une copie de la fiche du parent. */
async function subprojectFiche(ctx: Ctx, projectDir: DriveItem, form: ProjectForm): Promise<DriveItem> {
  const existing = await findSubprojectFiche(ctx, projectDir, form.number);
  if (existing) return existing;
  const parent = parentOf(form.number);
  const [source] = await listFicheCandidates(ctx.gw.drive, projectDir, parent);
  if (!source) throw new Error(`Aucune fiche du projet parent ${formatNumber(parent)} à dupliquer.`);
  const nested = await findChild(ctx.gw.drive, projectDir, formatNumber(form.number));
  return ctx.gw.drive.copyFile(source, nested ?? projectDir, standardFicheName(form.number));
}

export function buildDeleteSteps(ctx: Ctx, number: ProjectNumber, rows: RepertoireRow[]): Step[] {
  const { gw, settings } = ctx;
  const group = deletionGroup(rows, number);
  const n = formatNumber(number);
  const steps: Step[] = [
    {
      label: 'Répertoire vérifié',
      run: async () => validateDeletion(repertoireWorkbook(ctx, await root(ctx, 'repertoire')), number, group),
    },
  ];
  if (settings.planner.enabled && settings.planner.planId) {
    steps.push({
      label: 'Tâches Planner supprimées',
      isolated: true,
      run: async () => {
        let count = 0;
        for (const r of group) count += await deleteProjectTasks(gw.planner, settings.planner.planId, r.number);
        return `${count} tâche(s) Planner supprimée(s)`;
      },
    });
  }
  if (settings.outlook.enabled && !isSubproject(number)) {
    steps.push({
      label: 'Dossiers Outlook supprimés',
      isolated: true,
      run: async () => {
        let count = 0;
        for (const p of renderPaths(outlookPaths(settings.outlook.arborescence), number, '')) {
          if (await deleteFolderPath(gw.mail, p)) count++;
        }
        const designation = group.find((r) => r.number === n)?.text[4] ?? '';
        if (designation) {
          for (const p of renderPaths(outlookPaths(settings.outlook.arborescence), number, designation)) {
            if (await deleteFolderPath(gw.mail, p)) count++;
          }
        }
        return `${count} dossier(s) Outlook supprimé(s)`;
      },
    });
  }
  steps.push({
    label: isSubproject(number) ? 'Fiche du sous-projet mise à la corbeille' : 'Dossier projet mis à la corbeille',
    run: async () => {
      const dir = await findProjectDir(ctx, number);
      if (!dir) return 'Dossier projet déjà absent';
      if (!isSubproject(number)) return void (await gw.drive.remove(dir));
      const nested = await findChild(gw.drive, dir, n);
      if (nested) return void (await gw.drive.remove(nested));
      const fiche = await findSubprojectFiche(ctx, dir, number);
      if (fiche) await gw.drive.remove(fiche);
      else return 'Fiche du sous-projet déjà absente';
    },
  });
  steps.push({
    label: 'Numéro libéré dans le répertoire',
    run: async () => {
      const rep = await root(ctx, 'repertoire');
      await clearProjectRows(repertoireWorkbook(ctx, rep), number, group);
    },
  });
  return steps;
}
