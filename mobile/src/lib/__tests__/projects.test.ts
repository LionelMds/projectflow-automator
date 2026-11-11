import { buildSnapshot, applyUpsertPlan, planUpsert, tableRowIndexForSheetRow, type Cell } from '@/lib/repertoire/rows';
import { demoDrive, demoFicheCells, demoPlanner, resetDemoProjects, setDemoLatency, DEMO_BUCKETS } from '@/lib/demoProjects';
import { checkRepertoireRow, clientSuggestions, createProject, loadProject, updateProject, type CreationDeps, type CreationForm } from '@/lib/projects/creation';
import { copyFinished, drivePathFromSetting, findChildFolder, folderKey } from '@/lib/projects/drive';
import {
  clearedAtelierDate,
  excelSerial,
  ficheCellsFromRange,
  ficheData,
  ficheWrites,
  infoWouldChange,
  sortFicheCandidates,
  standardFicheName,
  type FicheCells,
  type FicheProject,
} from '@/lib/projects/fiche';
import {
  canonicalProjectNumber,
  formatProjectNumber,
  isMainProjectNumber,
  isProjectNumber,
  nextProjectId,
  numberText,
  parseProjectNumber,
  projectFolderName,
  validateProjectId,
  validateSubprojectId,
  validateYear,
} from '@/lib/projects/numbers';
import {
  assignmentsPayload,
  chosenAssignees,
  plannerDueDate,
  plannerPlanIdFromText,
  plannerTaskTitle,
  taskMatchesProject,
  taskPatch,
} from '@/lib/projects/planner';
import { DEFAULT_SETTINGS, parseSettings, type Settings } from '@/lib/settings';

const n = parseProjectNumber;

describe('numéros de dossier (numero.py)', () => {
  it('validates the year, the project ID and the subproject', () => {
    expect(validateYear('2026')).toBe(2026);
    for (const value of ['26', '202', '20266', 'abcd', '20 6']) {
      expect(() => validateYear(value)).toThrow("L'année doit contenir exactement 4 chiffres.");
    }
    expect(validateProjectId('4995')).toBe('4995');
    expect(validateProjectId(123)).toBe('123');
    for (const value of ['12', 'abc', '12A', '']) {
      expect(() => validateProjectId(value)).toThrow("L'ID projet doit contenir au moins 3 chiffres.");
    }
    expect(validateSubprojectId(null)).toBeNull();
    expect(validateSubprojectId('')).toBeNull();
    expect(validateSubprojectId('  ')).toBeNull();
    expect(() => validateSubprojectId('0')).toThrow('Le sous-projet doit être supérieur à zéro.');
    for (const value of ['-1', 'A', '1.2']) {
      expect(() => validateSubprojectId(value)).toThrow('Le sous-projet doit contenir uniquement des chiffres.');
    }
  });

  it('formats and parses main projects and subprojects', () => {
    expect(formatProjectNumber('2026', '4995')).toBe('2026-4995');
    expect(formatProjectNumber('2026', '4995', '2')).toBe('2026-4995-2');
    expect(n('2026-4995')).toEqual({ year: 2026, projectId: '4995', subprojectId: null });
    const sub = n('2026-4995-2');
    expect(sub).toEqual({ year: 2026, projectId: '4995', subprojectId: '2' });
    expect(projectFolderName(sub)).toBe('2026-4995');
    for (const value of ['2026-12', '202-4995', '2026/4995', 'Total']) {
      expect(() => n(value)).toThrow('Le numéro de projet doit respecter le format YYYY-NNN ou YYYY-NNN-S.');
    }
    expect(isMainProjectNumber('2026-4995')).toBe(true);
    expect(isMainProjectNumber('2026-4995-2')).toBe(false);
  });

  it('finds the next ID among the main projects of the year', () => {
    expect(nextProjectId(['2026-4995', '2026-4995-2', '2025-8000', 'note'], 2026)).toBe('4996');
    expect(nextProjectId(['2025-8000'], 2026)).toBeNull();
  });

  it.each([
    ['2024-4493-A1', '2024-4493-A1'],
    ['2026-5102-REV03', '2026-5102-REV03'],
    ['2026-5102 REV03', '2026-5102-REV03'],
    ['2026-5102_rev03', '2026-5102-REV03'],
    ['2024 – 4493 a1', '2024-4493-A1'],
    [' 2026-118-02 ', '2026-118-2'],
    ['2026-4995-A', '2026-4995-A'],
  ])('reads « %s » as the dossier %s', (typed, number) => {
    expect(canonicalProjectNumber(typed)).toBe(number);
    expect(isProjectNumber(typed)).toBe(true);
    const parsed = n(typed);
    expect(numberText(parsed)).toBe(number);
    expect(projectFolderName(parsed)).toBe(number.split('-').slice(0, 2).join('-'));
  });

  it('leaves text that is no number as it is', () => {
    expect(canonicalProjectNumber('  Total  ')).toBe('Total');
    expect(isProjectNumber('202-3502-2')).toBe(false);
  });
});

const EMPTY: FicheCells = { C3: '', D3: '', D4: '', D5: '', D6: '', C6: '', C9: '', B9: '', E2: '' };

function project(partial: Partial<FicheProject> = {}): FicheProject {
  return { number: n('2026-4995'), designation: '', societe: '', contact: '', localisation: '', gere_par: '', ...partial };
}

function applied(cells: FicheCells, writes: ReturnType<typeof ficheWrites>): FicheCells {
  const result = { ...cells };
  for (const write of writes) result[write.address] = write.value;
  return result;
}

describe('fiche dossier (fiche_service.py)', () => {
  const today = new Date(2026, 4, 6);

  it('fills a new fiche: number, prefixed fields, manager, initials, dated B9', () => {
    const writes = ficheWrites(
      EMPTY,
      project({ designation: 'Escalier', societe: 'Balz', contact: 'Lionel', localisation: 'Zurich', gere_par: 'Claire Martin' }),
      { initials: ' LM ', newFiche: true, today },
    );
    const cells = applied(EMPTY, writes);
    expect(cells.C3).toBe('2026-4995');
    expect(cells.D3).toBe('Societe : Balz');
    expect(cells.D6).toBe('Localisation : Zurich');
    expect(cells.C6).toBe('Claire Martin');
    expect(cells.C9).toBe('LM');
    expect(writes.find((write) => write.address === 'B9')).toEqual({ address: 'B9', value: 46148, numberFormat: 'DD.MM.YYYY' });
    expect(cells.E2).toBe('');
    expect(ficheData(cells)).toEqual({
      number: '2026-4995',
      societe: 'Balz',
      contact: 'Lionel',
      designation: 'Escalier',
      localisation: 'Zurich',
      gere_par: 'Claire Martin',
      user_initials: 'LM',
    });
  });

  it('keeps an existing creation date and workshop date when the fiche is not new', () => {
    const current = { ...EMPTY, B9: excelSerial(new Date(2025, 0, 2)), E2: "fiche d'atelier le 02.01.2025" };
    const writes = ficheWrites(current, project(), { initials: '', newFiche: false, today });
    expect(writes.map((write) => write.address)).toEqual(['C3']);
  });

  it.each([
    ["fiche d'atelier le 01.02.2020", "fiche d'atelier le"],
    ['Fiche d’atelier le : 01.02.2020', 'Fiche d’atelier le :'],
    ['Texte libre 01.02.2020', null],
  ])('a new fiche drops only a recognised inherited date: « %s »', (original, expected) => {
    expect(clearedAtelierDate(original)).toBe(expected);
    const cells = applied({ ...EMPTY, E2: original }, ficheWrites({ ...EMPTY, E2: original }, project(), { initials: '', newFiche: true, today }));
    expect(cells.E2).toBe(expected ?? original);
  });

  it('strips the prefixes whatever their case', () => {
    const data = ficheData({ ...EMPTY, C3: '2026-4995', D3: 'société : Balz', D4: 'CONTACT : Lionel', D5: 'Projet : Escalier', D6: 'Localisation : Zurich', C6: 'Claire Martin', C9: 'LM' });
    expect(data.societe).toBe('Balz');
    expect(data.contact).toBe('Lionel');
    expect(data.gere_par).toBe('Claire Martin');
    expect(data.user_initials).toBe('LM');
    expect(ficheData({ ...EMPTY, C9: 'LM' }).gere_par).toBe('');
  });

  it.each([
    ['', 'LM', 'Previous manager', 'LM'],
    ['Claire Martin', '', 'Claire Martin', 'OLD'],
    ['  ', '  ', 'Previous manager', 'OLD'],
  ])('keeps each identity cell when its input is empty (%s, %s)', (manager, initials, expectedManager, expectedInitials) => {
    const current = { ...EMPTY, C6: 'Previous manager', C9: 'OLD', B9: 43862, E2: "fiche d'atelier le 03.04.2021" };
    const cells = applied(current, ficheWrites(current, project({ gere_par: manager }), { initials, newFiche: false, today }));
    expect(cells.C6).toBe(expectedManager);
    expect(cells.C9).toBe(expectedInitials);
    expect(cells.B9).toBe(43862);
    expect(cells.E2).toBe("fiche d'atelier le 03.04.2021");
  });

  it('reads the cells of the range B2:E9', () => {
    const values: Cell[][] = Array.from({ length: 8 }, () => ['', '', '', '']);
    values[1][1] = 2026; // C3 as a number
    values[1][2] = 'Societe : Balz'; // D3
    values[0][3] = "fiche d'atelier le"; // E2
    values[7][0] = 46148; // B9
    const text = values.map((row) => row.map((value) => String(value)));
    text[1][1] = '2026';
    text[7][0] = '06.05.2026';
    const cells = ficheCellsFromRange(values, text);
    expect(cells.C3).toBe('2026');
    expect(cells.D3).toBe('Societe : Balz');
    expect(cells.E2).toBe("fiche d'atelier le");
    expect(cells.B9).toBe(46148);
  });

  it('orders the candidates as list_candidates does', () => {
    const candidate = (name: string, folder: string[] = []) => ({ name, folder, item: name });
    const sorted = sortFicheCandidates(
      [candidate('Ancien.xlsx'), candidate('modele fiche.xlsx'), candidate('2026-4995 - Fiche dossier clients.xlsx', ['2026-4995']), candidate('Budget.xlsx')],
      n('2026-4995'),
    );
    expect(sorted.map((entry) => entry.name)).toEqual([
      '2026-4995 - Fiche dossier clients.xlsx',
      'modele fiche.xlsx',
      'Ancien.xlsx',
      'Budget.xlsx',
    ]);
    expect(standardFicheName(n('2026-5093-2'))).toBe('2026-5093-2 - Fiche dossier clients.xlsx');
  });

  it('asks before replacing only when a typed field differs from the fiche', () => {
    const fiche = ficheData({ ...EMPTY, D5: 'Projet : Escalier', C6: 'LM' });
    expect(infoWouldChange(project({ designation: 'Escalier' }), fiche)).toBe(false);
    expect(infoWouldChange(project({ designation: '' }), fiche)).toBe(false);
    expect(infoWouldChange(project({ designation: 'Garde-corps' }), fiche)).toBe(true);
  });
});

describe('dossiers OneDrive (folder_names.py)', () => {
  it('compares folder names without accents, case or outer spaces', () => {
    expect(folderKey(" Plan d'Exécution ")).toBe(folderKey("plan d'execution"));
    expect(folderKey('Plans')).not.toBe(folderKey('Plan'));
  });

  it('finds a child folder, never a file of that name', () => {
    const children = [
      { name: "plan d'execution.pdf", folder: false },
      { name: "Plan d'exécution", folder: true },
    ];
    expect(findChildFolder(children, "plan d'execution")?.name).toBe("Plan d'exécution");
    expect(findChildFolder(children, 'photos')).toBeNull();
  });

  it('reads a path copied from the poste', () => {
    expect(drivePathFromSetting('C:\\Users\\lm\\OneDrive - Balz Métal SA\\Projets')).toBe('Projets');
    expect(drivePathFromSetting('Documents/Projets/')).toBe('Documents/Projets');
  });

  it('reads the copy monitor', () => {
    expect(copyFinished({ status: 'inProgress' })).toBe('running');
    expect(copyFinished({ status: 'completed', resourceId: 'x' })).toBe('done');
    expect(copyFinished({ status: 'failed' })).toBe('failed');
  });
});

describe('répertoire : création et sous-projets (repertoire_service.py)', () => {
  const today = new Date(2026, 4, 6);
  const range = (values: Cell[][]) => ({ address: `'2026'!A1:F${values.length}`, values });
  const main = { number: '2026-4995', parent: null, societe: 'Balz', contact: 'Lionel', designation: 'Escalier' };

  it('fills the empty pre-numbered row of a main project', () => {
    const plan = planUpsert(range([['2026-4995', '', '', '', '', 'Ne pas toucher']]), main, { today, forceOverwrite: false });
    expect(plan).toEqual({ kind: 'update', sheetRow: 1, values: ['2026-4995', '06.05.2026', 'Balz', 'Lionel', 'Escalier'] });
  });

  it('refuses a filled row unless « Mettre à jour »', () => {
    expect(() => planUpsert(range([['2026-4995', '', '', '', 'Occupe', '']]), main, { today, forceOverwrite: false })).toThrow(
      'Les colonnes B à E du répertoire contiennent déjà des informations.',
    );
    expect(() => planUpsert(range([['2026-4995', '', 'Contact existant', '', '', '']]), main, { today, forceOverwrite: false })).toThrow(
      'Utilisez « Mettre à jour » pour remplacer.',
    );
    const kept = planUpsert(range([['2026-4995', 'x', 'Ancien', 'Ancien contact', 'Occupe']]), { ...main, contact: '' }, { today, forceOverwrite: true });
    expect(kept.values).toEqual(['2026-4995', '06.05.2026', 'Balz', 'Ancien contact', 'Escalier']);
    expect(() => planUpsert(range([['2026-5000', '', '', '', '']]), main, { today, forceOverwrite: false })).toThrow(
      'Projet introuvable dans le répertoire : 2026-4995',
    );
  });

  it('inserts a subproject after its parent group, with the format of the first free row', () => {
    const sub = { number: '2026-4995-2', parent: '2026-4995', societe: '', contact: '', designation: 'Variante' };
    const plan = planUpsert(
      range([
        ['2026-4995', 'Balz', '', '', 'Escalier', 'LM'],
        ['2026-5000', '', '', '', '', ''],
      ]),
      sub,
      { today, forceOverwrite: true },
    );
    expect(plan).toEqual({ kind: 'insert', sheetRow: 2, formatSourceRow: 2, values: ['2026-4995-2', '06.05.2026', '', '', 'Variante'] });
    const after = planUpsert(
      range([
        ['2026-4995', 'Balz', '', '', 'Main'],
        ['2026-4995-1', 'Balz', '', '', 'Sub 1'],
        ['2026-5000', '', '', '', ''],
      ]),
      sub,
      { today, forceOverwrite: true },
    );
    expect(after.sheetRow).toBe(3);
  });

  it('updates an existing subproject row without inserting', () => {
    const plan = planUpsert(
      range([
        ['2026-4995', 'Balz', '', '', 'Escalier', 'Code interne'],
        ['2026-4995-2', 'Balz', '', '', 'Ancienne variante', 'Sous-code'],
        ['2026-5000', '', '', '', '', ''],
      ]),
      { number: '2026-4995-2', parent: '2026-4995', societe: '', contact: '', designation: 'Variante' },
      { today, forceOverwrite: false },
    );
    expect(plan).toEqual({ kind: 'update', sheetRow: 2, values: ['2026-4995-2', '06.05.2026', '', '', 'Variante'] });
  });

  it('refuses a subproject without its parent', () => {
    expect(() =>
      planUpsert(range([]), { number: '2026-4995-2', parent: '2026-4995', societe: '', contact: '', designation: '' }, { today, forceOverwrite: true }),
    ).toThrow('Projet parent introuvable dans le répertoire : 2026-4995');
  });

  it('places the row in an Excel table body', () => {
    const table = { id: 't', rowIndex: 0, rowCount: 11, columnIndex: 0, columnCount: 12, showHeaders: true, showTotals: false };
    // Header on row 1, body rows 2 to 11; row 12 appends.
    expect(tableRowIndexForSheetRow(table, 1)).toBeNull();
    expect(tableRowIndexForSheetRow(table, 2)).toBe(0);
    expect(tableRowIndexForSheetRow(table, 12)).toBe(10);
    expect(tableRowIndexForSheetRow(table, 13)).toBeNull();
  });

  it('applies an insert to the in-memory worksheet', () => {
    const sheet = { address: "'2026'!A1:E2", values: [['2026-4995', '', 'Balz', '', 'Escalier'], ['2026-5000', '', '', '', '']] as Cell[][], text: [['2026-4995', '', 'Balz', '', 'Escalier'], ['2026-5000', '', '', '', '']] };
    const next = applyUpsertPlan(sheet, { kind: 'insert', sheetRow: 2, formatSourceRow: 2, values: ['2026-4995-2', '06.05.2026', '', '', 'Variante'] });
    expect(next.address).toBe("'2026'!A1:E3");
    expect(buildSnapshot(2026, next).rows.map((row) => row.number)).toEqual(['2026-4995', '2026-4995-2', '2026-5000']);
  });

  it('checks the répertoire row before copying anything', () => {
    const snapshot = buildSnapshot(2026, range([['2026-4995', '', 'Balz', '', ''], ['2026-5000', '', '', '', '']]));
    expect(() => checkRepertoireRow(snapshot, n('2026-4995'), false)).toThrow('Les colonnes B à E');
    expect(() => checkRepertoireRow(snapshot, n('2026-5000'), false)).not.toThrow();
    expect(() => checkRepertoireRow(snapshot, n('2026-5001'), false)).toThrow('Projet introuvable dans le répertoire : 2026-5001');
    expect(() => checkRepertoireRow(snapshot, n('2026-4995-2'), true)).not.toThrow();
    expect(() => checkRepertoireRow(snapshot, n('2026-4996-2'), true)).toThrow('Projet parent introuvable');
  });

  it('suggests clients and their contacts from the répertoire', () => {
    const snapshot = buildSnapshot(2026, range([['2026-4995', '', 'Régie Favre', 'M. Dupasquier', 'A'], ['2026-4996', '', 'Garage du Lac', 'P. Rochat', 'B']]));
    expect(clientSuggestions(snapshot.rows, 'societe', 'regie')).toEqual(['Régie Favre']);
    expect(clientSuggestions(snapshot.rows, 'contact', '', 'Garage du Lac')).toEqual(['P. Rochat']);
  });
});

describe('Microsoft Planner (planner.py)', () => {
  it.each([
    ['  plan-id  ', 'plan-id'],
    ['https://planner.cloud.microsoft/webui/plan/xqQg5FS2LkCp935s-FIFm2QAFkHM/view/board?tid=t', 'xqQg5FS2LkCp935s-FIFm2QAFkHM'],
    ['https://tasks.office.com/contoso.com/fr-FR/Home/Planner/#/plantaskboard?groupId=g&planId=abc_DEF-123', 'abc_DEF-123'],
    ['https://tasks.office.com/contoso.com/Home/PlanViews/abcDEF123?Type=PlanLink', 'abcDEF123'],
    [
      'https://teams.microsoft.com/l/entity/com.microsoft.teamspace.tab.planner/tt.c_x?context=%7B%22subEntityId%22%3A%7B%22planId%22%3A%22teamsPlan_1%22%7D%7D',
      'teamsPlan_1',
    ],
  ])('reads the plan id of %s', (text, expected) => {
    expect(plannerPlanIdFromText(text)).toBe(expected);
  });

  it('refuses an unknown link', () => {
    expect(() => plannerPlanIdFromText('https://example.test/nothing-here')).toThrow('Lien Planner non reconnu.');
  });

  it('titles the task with the number and the designation', () => {
    expect(plannerTaskTitle('2026-4995', '  Escalier  ')).toBe('2026-4995 - Escalier');
    expect(plannerTaskTitle('2026-4995', '')).toBe('2026-4995');
    expect(taskMatchesProject('2026-4995 - Escalier', '2026-4995')).toBe(true);
    expect(taskMatchesProject('2026-4995-2 - Variante', '2026-4995')).toBe(false);
  });

  it('brings an existing task up to date: title, bucket, missing members, due date', () => {
    const patch = taskPatch(
      { id: 't', title: '2026-4995 - Ancien', bucketId: 'old', etag: 'e', assignments: ['a'], dueDateTime: '' },
      { title: '2026-4995 - Escalier', bucketId: 'new', assigneeIds: ['a', 'b'], dueDate: '' },
    );
    expect(patch).toEqual({ title: '2026-4995 - Escalier', bucketId: 'new', assignments: assignmentsPayload(['b']) });
    expect(
      taskPatch(
        { id: 't', title: 'x', bucketId: 'b', etag: 'e', assignments: ['a'], dueDateTime: '' },
        { title: 'x', bucketId: 'b', assigneeIds: ['a'], dueDate: '' },
      ),
    ).toBeNull();
  });

  it('gives a task without chosen members to the default members, not the shared account', () => {
    expect(chosenAssignees({ assigneeIds: [], defaultAssigneeIds: ['romain-id'] })).toEqual(['romain-id']);
    expect(chosenAssignees({ assigneeIds: ['a'], defaultAssigneeIds: ['romain-id'] })).toEqual(['a']);
  });

  it('dates the task in UTC, to the second', () => {
    expect(plannerDueDate(7, new Date(Date.UTC(2026, 10, 7, 9, 30, 0, 456)))).toBe('2026-11-14T09:30:00Z');
    expect(plannerDueDate(0)).toBe('');
    expect(plannerDueDate(null)).toBe('');
  });
});

describe('paramètres', () => {
  it('accepts settings saved by the first version', () => {
    const settings = parseSettings(JSON.stringify({ repertoire: 'https://x', planningFolder: 'P', initials: 'LM', demo: true }));
    expect(settings).toEqual({ ...DEFAULT_SETTINGS, repertoire: 'https://x', planningFolder: 'P', initials: 'LM', demo: true });
    expect(parseSettings('{"plannerDueDays": 400, "plannerAssigneeIds": ["a", 3]}')).toMatchObject({
      plannerDueDays: 7,
      plannerAssigneeIds: ['a'],
    });
  });
});

describe('mode démo : création', () => {
  const year = String(new Date().getFullYear());
  beforeEach(() => {
    resetDemoProjects();
    setDemoLatency(0);
  });

  function deps(log: string[], repertoire: { values: Cell[][] }, settings: Partial<Settings> = {}): CreationDeps {
    const sheet = () => ({ address: `'${year}'!A1:E${repertoire.values.length}`, values: repertoire.values });
    return {
      drive: demoDrive(),
      planner: demoPlanner(),
      settings: { ...DEFAULT_SETTINGS, demo: true, initials: 'LM', plannerEnabled: true, plannerPlanId: 'demo-plan', plannerBucketId: DEMO_BUCKETS[0].id, ...settings },
      repertoire: {
        peek: async () => buildSnapshot(Number(year), sheet()),
        upsert: async (_year, row, options) => {
          const plan = planUpsert(sheet(), row, { today: new Date(), forceOverwrite: options.forceOverwrite });
          if (plan.kind === 'insert') repertoire.values.splice(plan.sheetRow - 1, 0, plan.values);
          else repertoire.values[plan.sheetRow - 1] = plan.values;
          return plan;
        },
      },
      log: (line) => log.push(line),
      confirmExisting: async () => 'yes',
      today: () => new Date(2026, 4, 6),
    };
  }

  const form = (partial: Partial<CreationForm>): CreationForm => ({
    year,
    projectId: '5001',
    subprojectId: '',
    designation: 'Escalier atelier',
    societe: 'Balz',
    contact: 'Lionel',
    localisation: 'Bulle',
    gerePar: 'LM',
    planner: { enabled: true, bucketId: '', bucketName: '', assigneeIds: [], assigneeNames: [], dueEnabled: false, dueDays: 7 },
    ...partial,
  });

  it('copies the reference folder, renames and fills the fiche, writes the répertoire and Planner', async () => {
    const log: string[] = [];
    const repertoire = { values: [['No.', 'Date', 'Client', 'Contact', 'Désignation'], [`${year}-5001`, '', '', '', '']] as Cell[][] };
    const outcome = await createProject(deps(log, repertoire), form({}));
    expect(outcome.status).toBe('created');
    expect(outcome.failures).toBe(0);
    expect(outcome.fiche?.name).toBe(`${year}-5001 - Fiche dossier clients.xlsx`);
    const cells = demoFicheCells(outcome.fiche!)!;
    expect(cells.C3).toBe(`${year}-5001`);
    expect(cells.D5).toBe('Projet : Escalier atelier');
    expect(cells.B9).toBe(46148);
    expect(cells.E2).toBe("fiche d'atelier le");
    expect(repertoire.values[1].slice(2)).toEqual(['Balz', 'Lionel', 'Escalier atelier']);
    expect(log).toContain('+ Tâche Planner créée');

    // Créer again: the folder is kept, the répertoire row is replaced after « Oui ».
    const again: string[] = [];
    const second = await createProject(deps(again, repertoire), form({ designation: 'Escalier extérieur' }));
    expect(second.status).toBe('ready');
    expect(again.some((line) => line.startsWith('+ Projet existant réappliqué'))).toBe(true);
    expect(repertoire.values[1][4]).toBe('Escalier extérieur');
    expect(again).toContain('+ Tâche Planner mise à jour');
  });

  it('refuses a filled répertoire row before copying anything', async () => {
    const log: string[] = [];
    const repertoire = { values: [[`${year}-5001`, '', 'Déjà', '', '']] as Cell[][] };
    await expect(createProject(deps(log, repertoire), form({}))).rejects.toThrow('Les colonnes B à E');
  });

  it('creates a subproject from the parent fiche and inserts its row', async () => {
    const log: string[] = [];
    const parent = `${year}-4996`;
    const repertoire = { values: [[parent, '', 'Garage du Lac', 'P. Rochat', 'Structure'], [`${year}-5001`, '', '', '', '']] as Cell[][] };
    const outcome = await createProject(deps(log, repertoire), form({ projectId: '4996', subprojectId: '2', designation: 'Variante' }));
    expect(outcome.fiche?.name).toBe(`${parent}-2 - Fiche dossier clients.xlsx`);
    expect(demoFicheCells(outcome.fiche!)?.C3).toBe(`${parent}-2`);
    expect(repertoire.values.map((row) => row[0])).toEqual([parent, `${parent}-2`, `${year}-5001`]);

    const loaded = await loadProject(deps([], repertoire), year, '4996', '2');
    expect(loaded.designation).toBe('Variante');
    const updated = await updateProject(deps([], repertoire), form({ projectId: '4996', subprojectId: '2', designation: 'Variante 2' }));
    expect(updated.status).toBe('updated');
  });
});
