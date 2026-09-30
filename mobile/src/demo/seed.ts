// Données de démonstration reprises de la maquette Claude Design.
import { excelSerial } from '../core/text';
import { DEFAULT_SETTINGS, type ItemRef, type Settings } from '../core/settings';
import { MemoryStore } from './memory';

const BASE = 'Balz Metal Sa/Entreprise';

const ROWS: [string, string, string, string, string][] = [
  ['2026-4990', '09.09.2026', 'Menuiserie Rochat SA', 'P. Rochat', 'Escalier hélicoïdal acier'],
  ['2026-4991', '11.09.2026', 'Commune de Morges', 'S. Bovet', 'Garde-corps passerelle'],
  ['2026-4992', '14.09.2026', 'Vuillemin Immobilier', 'C. Vuillemin', 'Balcons – immeuble B'],
  ['2026-4993', '16.09.2026', 'Cave des Coteaux', 'M. Jaquet', 'Support de cuves inox'],
  ['2026-4994', '21.09.2026', 'Atelier Duvanel', 'J. Duvanel', 'Porte coupe-feu EI30'],
  ['2026-4995', '24.09.2026', 'Hôtel du Lac', 'A. Perrin', 'Verrière cuisine'],
  ['2026-4995-2', '26.09.2026', 'Hôtel du Lac', 'A. Perrin', 'Verrière cuisine – lot 2'],
  ['2026-4996', '28.09.2026', 'Groupe Stettler', 'R. Stettler', 'Passerelle technique'],
  ['2026-4997', '', '', '', ''],
  ['2026-4998', '', '', '', ''],
  ['2026-4999', '', '', '', ''],
];

const serial = (swiss: string) => {
  const [d, m, y] = swiss.split('.').map(Number);
  return excelSerial(new Date(y, m - 1, d));
};

/** Fiche vide : grille B2:E9 (colonnes A..E, lignes 1..9). */
function ficheSheet(fill?: { n: string; soc: string; con: string; des: string; loc: string; ger: string; ini: string; date: string }) {
  const g: unknown[][] = Array.from({ length: 10 }, () => ['', '', '', '', '']);
  g[1][1] = 'FICHE DOSSIER CLIENT';
  g[1][4] = "fiche d'atelier le :";
  if (fill) {
    g[2][2] = fill.n;
    g[2][3] = `Societe : ${fill.soc}`;
    g[3][3] = `Contact : ${fill.con}`;
    g[4][3] = `Projet : ${fill.des}`;
    g[5][3] = `Localisation : ${fill.loc}`;
    g[5][2] = fill.ger;
    g[8][2] = fill.ini;
    g[8][1] = serial(fill.date);
  }
  return { Fiche: g };
}

export function seedDemo(latencyMs = 140): { store: MemoryStore; settings: Settings } {
  const store = new MemoryStore(latencyMs);
  store.members = [
    { id: store.me.id, displayName: store.me.displayName, email: store.me.email },
    { id: 'u-jb', displayName: 'Julien Bovet', email: 'julien.bovet@balzmetal.ch' },
    { id: 'u-cr', displayName: 'Claire Rochat', email: 'claire.rochat@balzmetal.ch' },
    { id: 'u-as', displayName: 'Alain Stettler', email: 'alain.stettler@balzmetal.ch' },
  ];

  const rep2026: unknown[][] = Array.from({ length: 998 }, () => []);
  rep2026.push(['N°', 'Date', 'Client', 'Contact', 'Désignation', 'Devis', 'Commande', 'Facture', 'Montant', 'Acompte', 'Solde', 'Remarque']);
  for (const [n, date, c, k, d] of ROWS) rep2026.push([n, date ? serial(date) : '', c, k, d, date ? 'D-' + n.slice(5) : '', '', '', '', '', '', '']);
  const rep2027: unknown[][] = [
    ['N°', 'Date', 'Client', 'Contact', 'Désignation'],
    ...Array.from({ length: 10 }, (_, i) => [`2027-${5000 + i}`, '', '', '', '']),
  ];
  store.file(`${BASE}/Répertoire chantier.xlsx`, 212_000, undefined, { '2026': rep2026, '2027': rep2027 });

  store.file(`${BASE}/00-Référence/10-Racine/Fiche dossier clients.xlsx`, 47_600, undefined, ficheSheet());
  store.folder(`${BASE}/00-Référence/10-Racine/Photos`);
  store.folder(`${BASE}/00-Référence/10-Racine/Plans/Plan d’exécution`);
  store.folder(`${BASE}/00-Référence/10-Racine/Correspondance`);
  store.file(`${BASE}/00-Référence/10-Racine/Correspondance/Modèle offre.docx`, 31_200);

  for (const n of ['ENS-100.SLDASM', 'PRT-100.SLDPRT', 'PRT-200.SLDPRT', 'ENV-100.SLDPRT']) store.file(`${BASE}/00-Référence/CAO/SolidWorks/20XX-XXXX-${n}`, 180_000);
  store.file(`${BASE}/00-Référence/CAO/AutoCAD/20XX-XXXX-ENS-100.dwg`, 96_000);

  for (const [n, date, c, k, d] of ROWS.filter((r) => r[1] && !r[0].slice(5).includes('-'))) {
    const dir = `${BASE}/Projets/2026/${n}`;
    store.file(`${dir}/${n} - Fiche dossier clients.xlsx`, 48_200, '2026-09-24T14:12:00', ficheSheet({ n, soc: c, con: k, des: d, loc: 'Morges VD', ger: 'LM', ini: 'LM', date }));
    store.folder(`${dir}/Photos`);
    store.folder(`${dir}/Plans/Plan d’exécution`);
  }
  const p = `${BASE}/Projets/2026/2026-4995`;
  store.file(`${p}/2026-4995-2 - Fiche dossier clients.xlsx`, 47_900, '2026-09-26T09:40:00', ficheSheet({ n: '2026-4995-2', soc: 'Hôtel du Lac', con: 'A. Perrin', des: 'Verrière cuisine – lot 2', loc: 'Morges VD', ger: 'LM', ini: 'LM', date: '26.09.2026' }));
  store.file(`${p}/Prise de cote initiale.pdf`, 1_233_500, '2026-09-22T16:05:00');
  ['IMG_0412.jpg', 'IMG_0413.jpg', 'IMG_0414.jpg', 'IMG_0421.jpg', 'IMG_0422.jpg', 'IMG_0430.jpg'].forEach((n, i) =>
    store.file(`${p}/Photos/${n}`, Math.round((2.1 + i * 0.37) * 1024 * 1024), `2026-09-22T15:${10 + i * 4}:00`),
  );
  ['ENS-100', 'PRT-100', 'PRT-200', 'ENV-100'].forEach((n, i) => store.file(`${p}/Plans/Plan d’exécution/2026-4995-${n}.pdf`, (310 + i * 84) * 1024, `2026-09-25T11:2${i}:00`));
  store.tasks.push({ id: 't-4995', title: '2026-4995 - Verrière cuisine', bucketId: 'b-atelier', etag: 'W/"1"', assignments: ['u-lm'], dueDateTime: '' });

  const ref = (path: string): ItemRef => {
    const n = store.byPath(path)!;
    return { reference: path, driveId: 'demo-drive', id: n.id, name: n.name, webUrl: `demo:${path}` };
  };
  const settings: Settings = {
    ...DEFAULT_SETTINGS,
    initials: 'LM',
    racine: ref(`${BASE}/Projets`),
    reference: ref(`${BASE}/00-Référence/10-Racine`),
    repertoire: ref(`${BASE}/Répertoire chantier.xlsx`),
    outlook: { enabled: true, arborescence: '[YYYY]/[PROJECT_FOLDER]' },
    planner: { enabled: true, planId: 'plan-atelier', planName: 'Projets atelier', bucketId: 'b-todo', bucketName: 'À faire', dueDays: 14 },
    cad: { solidworks: ref(`${BASE}/00-Référence/CAO/SolidWorks`), autocad: ref(`${BASE}/00-Référence/CAO/AutoCAD`), subfolder: 'Plans/Plan d’exécution' },
  };
  return { store, settings };
}
