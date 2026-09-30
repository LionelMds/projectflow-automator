import { describe, expect, it } from 'vitest';
import { memoryGateways } from '../demo/memory';
import { seedDemo } from '../demo/seed';
import { clientRecords, suggestClients } from './clients';
import type { DriveItem } from './gateways';
import { readFiche } from './fiche';
import { makeNumber, nextSubprojectId, parseNumber } from './numero';
import { notificationHtml, notificationRows } from './notification';
import { renderFolderName } from './outlook';
import { buildDeleteSteps, buildProjectSteps, precheckCreate, type Ctx, type IntegrationChoice } from './project';
import { readSnapshot, updateEditableRow } from './repertoire';
import { buildSortieSteps, discover, outputFolderName } from './sortie';
import { runSteps } from './steps';

function setup() {
  const { store, settings } = seedDemo(0);
  const gw = memoryGateways(store);
  const ctx: Ctx = { gw, settings, me: store.me };
  const rep = () => gw.workbooks.open({ driveId: 'demo-drive', id: settings.repertoire!.id, name: '', isFolder: false, size: 0, lastModified: '', webUrl: '' });
  return { store, settings, gw, ctx, rep };
}

const noIntegrations: IntegrationChoice = {
  planner: { enabled: false, bucketId: '', bucketName: '', assignees: [], dueDays: null, notify: false },
  solidworks: false,
  autocad: false,
};

const form = (n: string, extra: Partial<{ designation: string; societe: string; contact: string }> = {}) => ({
  number: parseNumber(n),
  designation: extra.designation ?? 'Garde-corps passerelle',
  societe: extra.societe ?? 'Commune de Morges',
  contact: extra.contact ?? 'S. Bovet',
  localisation: 'Morges VD',
  gerePar: 'Lionel',
});

describe('numéros', () => {
  it('valide et formate', () => {
    expect(() => makeNumber('2026', '49')).toThrow();
    expect(parseNumber('2026-4995-2')).toEqual({ year: 2026, projectId: '4995', subprojectId: '2' });
    expect(nextSubprojectId(['2026-4995', '2026-4995-2', '2026-4995-3'], parseNumber('2026-4995'))).toBe('4');
    expect(nextSubprojectId(['2026-4995'], parseNumber('2026-4995'))).toBe('2');
  });
});

describe('répertoire', () => {
  it('trouve la prochaine ligne libre et ses lignes Excel', async () => {
    const { rep } = setup();
    const snap = await readSnapshot(rep(), 2026);
    expect(snap.rows).toHaveLength(11);
    expect(snap.next).toEqual({ number: '2026-4997', rowIndex: 1007 });
    expect(snap.rows[0].text[1]).toBe('09.09.2026');
  });

  it('refuse d’écraser une ligne modifiée ailleurs', async () => {
    const { rep } = setup();
    const snap = await readSnapshot(rep(), 2026);
    const row = snap.rows[0];
    await rep().updateRange('2026', `C${row.rowIndex + 1}`, [['Autre client']]);
    await expect(updateEditableRow(rep(), 2026, row, ['x', 'y', 'z', 'w'])).rejects.toThrow(/modifiée dans le fichier partagé/);
  });
});

describe('création de projet', () => {
  it('crée le dossier, copie la référence, remplit la fiche et le répertoire', async () => {
    const { ctx, gw, rep, store } = setup();
    const f = form('2026-4997');
    await precheckCreate(ctx, f);
    const { steps, out } = buildProjectSteps(ctx, f, noIntegrations, 'create');
    const result = await runSteps(steps, () => {});
    expect(result.failed).toBe(false);
    expect(out.fiche?.name).toBe('2026-4997 - Fiche dossier clients.xlsx');
    const data = await readFiche(gw.workbooks.open(out.fiche!));
    expect(data).toMatchObject({ number: '2026-4997', societe: 'Commune de Morges', designation: 'Garde-corps passerelle', gerePar: 'Lionel', initials: 'LM' });
    expect(store.byPath('Balz Metal Sa/Entreprise/Projets/2026/2026-4997/Correspondance/Modèle offre.docx')).not.toBeNull();
    const snap = await readSnapshot(rep(), 2026);
    expect(snap.rows.find((r) => r.number === '2026-4997')?.text[2]).toBe('Commune de Morges');
    expect(snap.next?.number).toBe('2026-4998');
    await expect(precheckCreate(ctx, f)).rejects.toThrow(/contiennent déjà/);
  });

  it('insère un sous-projet après le groupe du parent', async () => {
    const { ctx, rep } = setup();
    const f = form('2026-4995-3', { designation: 'Verrière – lot 3' });
    await precheckCreate(ctx, f);
    const { steps } = buildProjectSteps(ctx, f, noIntegrations, 'create');
    expect((await runSteps(steps, () => {})).failed).toBe(false);
    const numbers = (await readSnapshot(rep(), 2026)).rows.map((r) => r.number);
    expect(numbers.slice(5, 9)).toEqual(['2026-4995', '2026-4995-2', '2026-4995-3', '2026-4996']);
  });

  it('crée la tâche Planner, notifie les nouveaux assignés et copie la CAO', async () => {
    const { ctx, store } = setup();
    const choice: IntegrationChoice = {
      planner: { enabled: true, bucketId: 'b-etude', bucketName: 'Étude', assignees: store.members.slice(0, 2), dueDays: 14, notify: true },
      solidworks: true,
      autocad: true,
    };
    const { steps, out } = buildProjectSteps(ctx, form('2026-4997'), choice, 'create');
    const result = await runSteps(steps, () => {});
    expect(result.failed).toBe(false);
    expect(result.states.find((s) => s.label.startsWith('Arborescence SolidWorks'))?.status).toBe('warn');
    expect(store.tasks.find((t) => t.title === '2026-4997 - Garde-corps passerelle')?.assignments).toEqual(['u-lm', 'u-jb']);
    expect(store.sentMail).toHaveLength(1);
    expect(store.sentMail[0].to.map((t) => t.email)).toEqual(['lionel.martin@balzmetal.ch', 'julien.bovet@balzmetal.ch']);
    expect(store.sentMail[0].subject).toBe('[ProjectFlow] Vous êtes assigné(e) au projet 2026-4997 – Garde-corps passerelle');
    expect(out.notified).toEqual(['Lionel Martin', 'Julien Bovet']);
    expect(store.byPath('Balz Metal Sa/Entreprise/Projets/2026/2026-4997/Plans/Plan d’exécution/2026-4997-ENS-100.SLDASM')).not.toBeNull();
    expect(store.byPath('Balz Metal Sa/Entreprise/Projets/2026/2026-4997/Plans/Plan d’exécution/2026-4997-ENS-100.dwg')).not.toBeNull();
    expect(store.mailFolders.map((f) => f.displayName)).toEqual(['2026', '2026-4997 (Garde-corps passerelle)']);

    // Mise à jour : la tâche existe déjà, seuls les nouveaux assignés reçoivent l'e-mail.
    const again: IntegrationChoice = { ...choice, solidworks: false, autocad: false, planner: { ...choice.planner, assignees: store.members.slice(0, 3) } };
    const update = buildProjectSteps(ctx, form('2026-4997'), again, 'update');
    expect((await runSteps(update.steps, () => {})).failed).toBe(false);
    expect(store.tasks.filter((t) => t.title.startsWith('2026-4997'))).toHaveLength(1);
    expect(store.sentMail[1].to.map((t) => t.name)).toEqual(['Claire Rochat']);
  });

  it('supprime le projet et libère son numéro', async () => {
    const { ctx, rep, store } = setup();
    const snap = await readSnapshot(rep(), 2026);
    const result = await runSteps(buildDeleteSteps(ctx, parseNumber('2026-4995'), snap.rows), () => {});
    expect(result.failed).toBe(false);
    expect(store.byPath('Balz Metal Sa/Entreprise/Projets/2026/2026-4995')).toBeNull();
    expect(store.tasks.some((t) => t.title.startsWith('2026-4995'))).toBe(false);
    const after = await readSnapshot(rep(), 2026);
    expect(after.rows.find((r) => r.number === '2026-4995')?.text.slice(1).join('')).toBe('');
    expect(after.next?.number).toBe('2026-4995');
  });
});

describe('sortie dossier', () => {
  it('inventorie puis copie dans un dossier horodaté', async () => {
    const { ctx, gw, store } = setup();
    const n = parseNumber('2026-4995');
    const dir = await gw.drive.resolve('Balz Metal Sa/Entreprise/Projets/2026/2026-4995');
    const inv = await discover(gw, dir, n);
    expect(inv.fiches.map((f) => f.name)).toEqual(['2026-4995 - Fiche dossier clients.xlsx', '2026-4995-2 - Fiche dossier clients.xlsx']);
    expect(inv.mesures.map((f) => f.name)).toEqual(['Prise de cote initiale.pdf']);
    expect(inv.photos).toHaveLength(6);
    expect(inv.plans).toHaveLength(4);
    const now = new Date(2026, 8, 30, 14, 12, 5);
    const out: { folder?: DriveItem } = {};
    const steps = buildSortieSteps(gw, inv, n, { fiche: inv.fiches[0], mesure: inv.mesures[0], photos: inv.photos.slice(0, 2), plans: inv.plans }, out, now);
    expect((await runSteps(steps, () => {})).failed).toBe(false);
    expect(out.folder?.name).toBe(outputFolderName(n, now));
    const base = `Balz Metal Sa/Entreprise/Projets/2026/2026-4995/Sorties dossier/2026-4995 - Sortie dossier - 20260930-141205`;
    const copied = store.byPath(`${base}/01 - Fiche dossier/2026-4995 - Fiche dossier clients.xlsx`)!;
    const fiche = await gw.workbooks.open({ ...store.item(copied) }).readRange('Fiche', 'E2');
    expect(String(fiche.values[0][0])).toMatch(/^fiche d'atelier le \d{2}\.\d{2}\.\d{4}$/);
    const source = await gw.workbooks.open(inv.fiches[0]).readRange('Fiche', 'E2');
    expect(source.values[0][0]).toBe("fiche d'atelier le :");
    expect(store.children(store.byPath(`${base}/04 - Plans`)!.id)).toHaveLength(4);
    void ctx;
  });
});

describe('divers', () => {
  it('suggère les clients du répertoire', async () => {
    const { rep } = setup();
    const snap = await readSnapshot(rep(), 2026);
    const s = suggestClients(clientRecords(snap.rows), 'roch');
    expect(s.map((c) => c.societe)).toEqual(['Menuiserie Rochat SA']);
    expect(suggestClients(clientRecords(snap.rows), 'hotel')[0]).toEqual({ societe: 'Hôtel du Lac', contact: 'A. Perrin' });
  });

  it('nomme les dossiers Outlook comme le bureau', () => {
    const n = parseNumber('2026-4995');
    expect(renderFolderName('[YYYY]', n, 'Verrière')).toBe('2026');
    expect(renderFolderName('[PROJECT_FOLDER]', n, '  Verrière   cuisine ')).toBe('2026-4995 (Verrière cuisine)');
    expect(renderFolderName('[YYYY]-[XXXX]', n, '')).toBe('2026-4995');
  });

  it('échappe le contenu de l’e-mail', () => {
    const input = {
      number: '2026-4997', designation: '<script>', societe: 'A & B', contact: '', localisation: '', gerePar: 'LM', initials: 'LM',
      planName: 'Projets atelier', bucketName: 'À faire', due: new Date(2026, 9, 14), folderPath: '…\\Projets\\2026\\2026-4997', folderUrl: 'https://x/?a="b"', taskUrl: '', senderName: 'Lionel',
    };
    const html = notificationHtml(input);
    expect(html).not.toContain('<script>');
    expect(html).toContain('A &amp; B');
    expect(notificationRows(input).find((r) => r.k === 'Échéance')?.v).toBe('14.10.2026');
    expect(notificationRows(input).find((r) => r.k === 'Contact')?.v).toBe('—');
  });
});
