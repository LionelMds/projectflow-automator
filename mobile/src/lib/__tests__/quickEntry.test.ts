/** Port of `tests/unit/test_planning_quick_entry.py` (the « saisie rapide » grammar). */

import { WorkCalendar } from '@/lib/planning/calendar';
import { formatInstant, type Instant, minuteOfDay, parseInstant } from '@/lib/planning/instant';
import {
  type AbsenceReason,
  type DayPart,
  DEFAULT_TASK_TYPES,
  DEFAULT_TEAMS,
  type Destination,
  type EntryContext,
  type EntryResource,
  foldText,
  formatDays,
  formatDuration,
  formatSpan,
  type ParsedEntry,
  parseEntry,
  type ProjectInfo,
  UNREADABLE_WARNING,
} from '@/lib/planning/quickEntry';
import { dayNumber } from '@/lib/planning/walltime';

const TODAY = dayNumber(2026, 10, 22); // a Thursday
const RIGHT_QUOTE = String.fromCharCode(0x2019);
const COMBINING_ACUTE = String.fromCharCode(0x301);

const RESOURCES: EntryResource[] = [
  { id: 'lbi', name: 'Luca Bianchi', initials: 'LBI', team: 'montage' },
  { id: 'ych', name: 'Yann Chappuis', initials: 'YCH', team: 'montage' },
  { id: 'jmf', name: 'Jean-Marc Fasel', initials: 'JMF', team: 'montage' },
  { id: 'mdu2', name: 'Michel Duc', initials: 'MDU', team: 'montage' },
  { id: 'oti', name: 'Olivier Tissot', initials: 'OTI', team: 'montage', active: false },
  { id: 'kdu', name: 'Kevin Dupuis', initials: 'KDU', team: 'atelier' },
  { id: 'tfa', name: 'Thomas Favre', initials: 'TFA', team: 'atelier' },
  { id: 'jro', name: 'Jean Rochat', initials: 'JRO', team: 'atelier' },
  { id: 'mdu1', name: 'Marc Dupraz', initials: 'MDU', team: 'atelier' },
  { id: 'de', name: 'David Emery', initials: 'DE', team: 'atelier' },
  { id: 'le', name: 'Laurent Egger', initials: 'LE', team: 'atelier' },
  { id: 'nre', name: 'Noah Rey', initials: 'NRE', team: 'atelier', apprentice: true },
  { id: 'sbe', name: 'Sébastien Berset', initials: 'SBE', team: 'bureau' },
  { id: 'pm', name: 'Pierre Monnet', initials: 'PM', team: 'bureau' },
  {
    id: 'thl',
    name: 'Thermo-Lac SA',
    initials: 'THL',
    team: 'soustraitants',
    kind_of_resource: 'subcontractor',
  },
];
const MONTAGE_TEAM = ['lbi', 'ych', 'jmf', 'mdu2'];

const PROJECTS = new Map<string, ProjectInfo>(
  (
    [
      ['2026-121', 'Commune de Marsens', 'Marquise entrée école'],
      ['2026-124', 'Gérance Rhône SA', 'Portail coulissant'],
      ['2026-112', 'M. Perret', 'Escalier extérieur'],
      ['2026-129', 'PPE Les Cèdres', 'Garde-corps balcons'],
      ['2026-118', 'Ville de Nyon', 'Barrières parc'],
      ['2026-118-1', 'Ville de Nyon', 'Barrières parc, lot 2'],
      ['2026-109', 'Mme Rossier', 'Portail jardin'],
      ['2026-130', 'Hôpital de Morges', 'Pose main courante'],
      ['2026-5231', 'Implenia SA', 'Garde-corps immeuble'],
      ['2025-301', 'Coop', 'Auvent quai'],
    ] as const
  ).map(([number, client, designation]): [string, ProjectInfo] => [
    number,
    { number, client, contact: '', designation, managedBy: 'LMO' },
  ]),
);

const CONTEXT: EntryContext = {
  today: TODAY,
  weekStart: dayNumber(2026, 10, 19),
  calendar: new WorkCalendar({ cantons: ['GE'] }),
  resources: RESOURCES,
  teams: DEFAULT_TEAMS,
  taskTypes: DEFAULT_TASK_TYPES,
  findProject: (number) => PROJECTS.get(number) ?? null,
  defaultYear: 2026,
};

type Changes = Partial<EntryContext>;

function parse(text: string, changes: Changes = {}): ParsedEntry {
  return parseEntry(text, { ...CONTEXT, ...changes });
}

function at(text: string): Instant {
  const value = parseInstant(text);
  if (value === null) throw new Error(`Date et heure illisibles: ${text}`);
  return value;
}

function instants(entry: ParsedEntry): [string | null, string | null] {
  return [
    entry.start !== null ? formatInstant(entry.start) : null,
    entry.end !== null ? formatInstant(entry.end) : null,
  ];
}

function draft(
  resourceIds: string[],
  reason: AbsenceReason,
  firstDay: number,
  lastDay: number,
  part: DayPart = 'day',
  note = '',
) {
  return { resourceIds, reason, firstDay, lastDay, part, note };
}

// ------------------------------------------------------------ destination ----

describe('destination', () => {
  it.each<[string, Destination]>([
    ['2026-121 montage marquise jeu-ven LBI YCH', 'grid'],
    ['124 débit portail 1,5j KDU lun', 'grid'],
    ['livraison 112 ven matin', 'unassigned'],
    ['129 relevé de cotes', 'backlog'],
    ['thermolaquage RAL 7016 THL lun-mer', 'grid'],
    ['/vac LBI 26.10-30.10', 'absence'],
    ['5231 pose garde-corps +montage S45 ?', 'grid'],
    ['jeu 7h30-11h métré 118 SBE', 'grid'],
    ['rangement stock TFA lun', 'grid'],
    ['sav portail 109 demain à 10h 1h30 !', 'unassigned'],
    ['<06.11 129 fabrication 2j', 'backlog'],
    ['', 'invalid'],
    ['   ', 'invalid'],
    ['?', 'invalid'],
    ['! ?', 'invalid'],
    ['am', 'invalid'],
    ['/vac 26.10', 'invalid'],
    ['255 portail', 'backlog'],
    ['LBI', 'backlog'],
    ['pose portail', 'backlog'],
    ['S44 121', 'unassigned'],
    ['à 10h 121', 'unassigned'],
    ['matin 121 KDU', 'grid'],
    ['/ecole NRE ven', 'absence'],
    ['MDU soudure lun', 'unassigned'],
    ['lu LBI', 'backlog'],
    ['me je ve LBI', 'backlog'],
  ])('%j goes to %s', (text, destination) => {
    expect(parse(text).destination).toBe(destination);
  });
});

// ---------------------------------------------------------------- dossier ----

describe('dossier', () => {
  it.each<[string, string, string | null]>([
    ['2026-121 montage', '2026-121', 'Commune de Marsens'],
    ['121 montage', '2026-121', 'Commune de Marsens'],
    ['118-1 pose', '2026-118-1', 'Ville de Nyon'],
    ['2026-118-1 pose', '2026-118-1', 'Ville de Nyon'],
    ['5231 pose', '2026-5231', 'Implenia SA'],
    ['301 auvent', '2025-301', 'Coop'],
    ['255 portail', '2026-255', null],
    ['2025-118 portail', '2025-118', null],
    ['thermolaquage RAL 7016 THL', '', null],
    ['débit HEA 200 KDU', '', null],
    ['profil HEB 140 et IPE 120', '', null],
    ['inox 316 KDU', '', null],
    ['Ø 150 tube 121', '2026-121', 'Commune de Marsens'],
    ['M12 120 vis 124', '2026-124', 'Gérance Rhône SA'],
    ['150 pces débit 121', '2026-121', 'Commune de Marsens'],
    ['200 x 100 tube', '', null],
    ['121 et 124 portails', '2026-121', 'Commune de Marsens'],
    ['#118 portail', '', null],
    ['pose 2 portails 121', '2026-121', 'Commune de Marsens'],
    ['<06.11 129 fabrication', '2026-129', 'PPE Les Cèdres'],
    ['27.10 112 livraison', '2026-112', 'M. Perret'],
    ['12345 pièces', '', null],
  ])('%j is dossier %j', (text, number, client) => {
    const entry = parse(text);

    expect(entry.projectNumber).toBe(number);
    expect(entry.project !== null ? entry.project.client : null).toBe(client);
  });

  it('tries the previous year with normalised numbers', () => {
    const asked: string[] = [];
    const find = (number: string): ProjectInfo | null => {
      asked.push(number);
      return PROJECTS.get(number) ?? null;
    };

    parse('301 auvent 118-01', { findProject: find });

    expect(asked).toEqual(['2026-301', '2025-301']);
  });

  it('keeps an unknown dossier with a warning', () => {
    const entry = parse('255 portail');

    expect(entry.project).toBeNull();
    expect(entry.warnings).toEqual(['2026-255 introuvable dans le répertoire']);
    expect(entry.tokens.map((token) => [token.kind, token.ambiguous])).toEqual([['project', true]]);
  });
});

// ----------------------------------------------------------------- people ----

describe('people', () => {
  it.each<[string, string[]]>([
    ['pose LBI YCH jeu', ['lbi', 'ych']],
    ['pose lbi ych', ['lbi', 'ych']],
    ['LBI, YCH pose', ['lbi', 'ych']],
    ['LBI+YCH pose', ['lbi', 'ych']],
    ['pose @lbi', ['lbi']],
    ['pose Luca', ['lbi']],
    ['pose luca bianchi', ['lbi']],
    ['relevé Sébastien', ['sbe']],
    ['relevé sebastien', ['sbe']],
    ['relevé seb', ['sbe']],
    ['soudure jean', ['jro']],
    ['pose jean-marc', ['jmf']],
    ['pose +montage', MONTAGE_TEAM],
    ['+bureau relevé', ['sbe', 'pm']],
    ['+mont pose', MONTAGE_TEAM],
    ['galva THL', ['thl']],
    ['galva thermo-lac sa', ['thl']],
    ['relevé de cotes', []],
    ['relevé DE cotes', ['de']],
    ['pose le portail LE', ['le']],
    ['LE pose la marquise', ['le']],
    ['OTI pose', []],
    ['pose LBI LBI', ['lbi']],
    ['PM relevé', ['pm']],
    ['lun pm KDU', ['kdu']],
    ['+lbi +le', ['lbi', 'le']],
    ['pose +montage LBI', MONTAGE_TEAM],
    ['thermolaquage 129', []],
  ])('%j names %j', (text, people) => {
    expect(parse(text).resourceIds).toEqual(people);
  });

  it.each([
    ['MDU soudure', 'person', 'mdu2,mdu1', '« MDU » : plusieurs personnes possibles (MDU Michel Duc, MDU Marc Dupraz)'],
    ['jea pose', 'person', 'jmf,jro', '« jea » : plusieurs personnes possibles (JMF Jean-Marc Fasel, JRO Jean Rochat)'],
    ['+zzz pose', 'person', '', '« +zzz » : personne ou équipe inconnue'],
    ['255 portail', 'project', '2026-255', '2026-255 introuvable dans le répertoire'],
    ['am 118', 'part', '', '« am » est ambigu : écrire « matin » ou « aprem »'],
    [
      '/sou 118',
      'type',
      'soudure,soustraitance',
      '« /sou » : plusieurs types possibles (Soudure / assemblage, Sous-traitance)',
    ],
    ['/xyz 118', 'type', '', 'Commande « /xyz » inconnue'],
    ['31.02 LBI', 'date', '', 'Date invalide : 31.02'],
    ['S60 LBI', 'week', '', 'Semaine invalide : S60'],
    ['25h LBI lun', 'time', '', 'Heure invalide : 25h'],
    ['0j 121', 'duration', '', 'Durée nulle : 0j'],
    ['<31.02 121', 'deadline', '', 'Date invalide : <31.02'],
    ['OTI pose lun', 'person', 'oti', '« OTI » : personne inactive (OTI Olivier Tissot)'],
    ['+oti pose', 'person', 'oti', '« +oti » : personne inactive (OTI Olivier Tissot)'],
    ['lun 3000j LBI', 'duration', '', 'Durée trop longue : 3000j'],
    ['27.10.2035 LBI', 'date', '', 'Date invalide : 27.10.2035'],
    ['27.10-03.10 LBI', 'date', '', 'Fin avant le début : 27.10-03.10'],
    ['S45-S44 LBI', 'week', '', 'Fin avant le début : S45-S44'],
    ['lun 0h LBI', 'duration', '', 'Durée nulle : 0h'],
  ])('%j has one ambiguous %s token', (text, kind, value, message) => {
    const entry = parse(text);
    const ambiguous = entry.tokens.filter((token) => token.ambiguous);

    expect(ambiguous.map((token) => [token.kind, token.value, token.message])).toEqual([[kind, value, message]]);
    expect(entry.warnings).toContain(message);
  });

  it('does not assign ambiguous people', () => {
    const entry = parse('MDU jea soudure lun');

    expect(entry.resourceIds).toEqual([]);
    expect(entry.title).toBe('soudure');
    expect(entry.warnings).toHaveLength(2);
  });

  it('warns for a team without active member', () => {
    const teams = [...DEFAULT_TEAMS, { key: 'peinture', name: 'Peinture', alias: 'peinture', order: 5 }];

    const entry = parse('+peinture lun', { teams });

    expect(entry.resourceIds).toEqual([]);
    expect(entry.warnings).toEqual(["L'équipe Peinture n'a aucun membre actif"]);
  });
});

// --------------------------------------------------------------- schedule ----

describe('schedule', () => {
  it.each<[string, string | null, string | null]>([
    ['2026-121 montage marquise jeu-ven LBI YCH', '2026-10-22T06:30', '2026-10-23T12:00'],
    ['124 débit portail 1,5j KDU lun', '2026-10-26T06:30', '2026-10-27T12:00'],
    ['livraison 112 ven matin', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['thermolaquage RAL 7016 THL lun-mer', '2026-10-26T06:30', '2026-10-28T16:30'],
    ['5231 pose garde-corps +montage S45 ?', '2026-11-02T06:30', '2026-11-06T12:00'],
    ['jeu 7h30-11h métré 118 SBE', '2026-10-22T07:30', '2026-10-22T11:00'],
    ['rangement stock TFA lun', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['sav portail 109 demain à 10h 1h30 !', '2026-10-23T10:00', '2026-10-23T11:30'],
    ['auj LBI', '2026-10-22T06:30', '2026-10-22T16:30'],
    ["aujourd'hui LBI", '2026-10-22T06:30', '2026-10-22T16:30'],
    ['demain LBI', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['après-demain LBI', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['lun LBI', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['mar LBI', '2026-10-27T06:30', '2026-10-27T16:30'],
    ['mercredi LBI', '2026-10-28T06:30', '2026-10-28T16:30'],
    ['jeudi LBI', '2026-10-22T06:30', '2026-10-22T16:30'],
    ['Vendredi LBI', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['jeu-mar LBI', '2026-10-22T06:30', '2026-10-27T16:30'],
    ['lun à mer LBI', '2026-10-26T06:30', '2026-10-28T16:30'],
    ['lun au mer LBI', '2026-10-26T06:30', '2026-10-28T16:30'],
    ['du lun au mer LBI', '2026-10-26T06:30', '2026-10-28T16:30'],
    ['lun prochain LBI', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['lun-dim LBI', '2026-10-26T06:30', '2026-10-30T12:00'],
    ['27.10 LBI', '2026-10-27T06:30', '2026-10-27T16:30'],
    ['27.10.2026 LBI', '2026-10-27T06:30', '2026-10-27T16:30'],
    ['27-29.10 LBI', '2026-10-27T06:30', '2026-10-29T16:30'],
    ['27.10-03.11 LBI', '2026-10-27T06:30', '2026-11-03T16:30'],
    ['du 27.10 au 30.10 LBI', '2026-10-27T06:30', '2026-10-30T12:00'],
    ['S44 LBI', '2026-10-26T06:30', '2026-10-30T12:00'],
    ['S44-S45 LBI', '2026-10-26T06:30', '2026-11-06T12:00'],
    ['S45 lun-mer LBI', '2026-11-02T06:30', '2026-11-04T16:30'],
    ['S45 jeu LBI', '2026-11-05T06:30', '2026-11-05T16:30'],
    ['S2 LBI', '2027-01-11T06:30', '2027-01-15T12:00'],
    ['lun matin LBI', '2026-10-26T06:30', '2026-10-26T12:00'],
    ['lun mat LBI', '2026-10-26T06:30', '2026-10-26T12:00'],
    ['lun aprem LBI', '2026-10-26T13:00', '2026-10-26T16:30'],
    ['lun apm LBI', '2026-10-26T13:00', '2026-10-26T16:30'],
    ['lun après-midi LBI', '2026-10-26T13:00', '2026-10-26T16:30'],
    ['lun pm KDU', '2026-10-26T13:00', '2026-10-26T16:30'],
    ['lun-mer matin LBI', '2026-10-26T06:30', '2026-10-28T12:00'],
    ['lun-mer aprem LBI', '2026-10-26T13:00', '2026-10-28T16:30'],
    ['ven aprem LBI', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['lun 13:00-15:30 LBI', '2026-10-26T13:00', '2026-10-26T15:30'],
    ['lun de 13h à 15h LBI', '2026-10-26T13:00', '2026-10-26T15:00'],
    ['lun 7h LBI', '2026-10-26T07:00', '2026-10-26T07:30'],
    ['lun à 10h LBI', '2026-10-26T10:00', '2026-10-26T10:30'],
    ['lun dès 7h 2h LBI', '2026-10-26T07:00', '2026-10-26T09:00'],
    ['lun à 10h 1j LBI', '2026-10-26T10:00', '2026-10-26T16:30'],
    ['lun matin 2h KDU', '2026-10-26T06:30', '2026-10-26T08:30'],
    ['lun 12h30 LBI', '2026-10-26T13:00', '2026-10-26T13:30'],
    ['ven à 12h LBI', '2026-10-26T06:30', '2026-10-26T07:00'],
    ['jeu 16h30 LBI', '2026-10-23T06:30', '2026-10-23T07:00'],
    ['25.12 LBI', '2026-12-28T06:30', '2026-12-28T16:30'],
    ['31.12 LBI', '2027-01-04T06:30', '2027-01-04T16:30'],
    ['sam LBI', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['dim LBI', '2026-10-26T06:30', '2026-10-26T16:30'],
    ['jeu 1j LBI', '2026-10-22T06:30', '2026-10-22T16:30'],
    ['ven 1j LBI', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['jeu 2j LBI', '2026-10-22T06:30', '2026-10-23T12:00'],
    ['jeu 3j LBI', '2026-10-22T06:30', '2026-10-26T16:30'],
    ['ven 2j LBI', '2026-10-23T06:30', '2026-10-26T16:30'],
    ['lun 5j LBI', '2026-10-26T06:30', '2026-10-30T12:00'],
    ['jeu 1j½ LBI', '2026-10-22T06:30', '2026-10-23T12:00'],
    ['27-29.10 2j LBI', '2026-10-27T06:30', '2026-10-29T16:30'],
    ['lun 3j LBI', '2026-10-26T06:30', '2026-10-28T16:30'],
    ['lun 2,5j LBI', '2026-10-26T06:30', '2026-10-28T12:00'],
    ['lun 0,5j LBI', '2026-10-26T06:30', '2026-10-26T12:00'],
    ['lun ½j LBI', '2026-10-26T06:30', '2026-10-26T12:00'],
    ['lun 1j½ LBI', '2026-10-26T06:30', '2026-10-27T12:00'],
    ['lun matin ½j LBI', '2026-10-26T06:30', '2026-10-26T12:00'],
    ['lun aprem ½j LBI', '2026-10-26T13:00', '2026-10-26T16:30'],
    ['lun à 10h ½j LBI', '2026-10-26T10:00', '2026-10-26T12:00'],
    ['lun à 14h ½j LBI', '2026-10-26T14:00', '2026-10-26T16:30'],
    ['ven aprem ½j LBI', '2026-10-23T06:30', '2026-10-23T12:00'],
    ['lun 1j 3h LBI', '2026-10-26T06:30', '2026-10-27T09:30'],
    ['jeu 1j 3h LBI', '2026-10-22T06:30', '2026-10-23T09:30'],
    ['lun 4h LBI', '2026-10-26T06:30', '2026-10-26T10:30'],
    ['lun 1h30 LBI', '2026-10-26T06:30', '2026-10-26T08:00'],
    ['lun 90min LBI', '2026-10-26T06:30', '2026-10-26T08:00'],
    ['lun 1j 4h LBI', '2026-10-26T06:30', '2026-10-27T10:30'],
    ['lun 11h-7h LBI', '2026-10-26T11:00', '2026-10-26T11:30'],
    ['pose portail 3j', null, null],
  ])('%j runs from %s to %s', (text, start, end) => {
    expect(instants(parse(text))).toEqual([start, end]);
  });

  it.each<[string, Changes, string, string]>([
    ['lun LBI', { weekStart: dayNumber(2026, 11, 2) }, '2026-11-02T06:30', '2026-11-02T16:30'],
    ['jeu LBI', { weekStart: dayNumber(2026, 11, 2) }, '2026-11-05T06:30', '2026-11-05T16:30'],
    ['lun LBI', { weekStart: dayNumber(2026, 10, 12) }, '2026-10-12T06:30', '2026-10-12T16:30'],
    ['demain LBI', { today: dayNumber(2026, 10, 23) }, '2026-10-26T06:30', '2026-10-26T16:30'],
    ['31.12 LBI', { calendar: new WorkCalendar({ cantons: ['VD'] }) }, '2026-12-31T06:30', '2026-12-31T16:30'],
    ['02.01 LBI', { calendar: new WorkCalendar({ cantons: ['VD'] }) }, '2027-01-04T06:30', '2027-01-04T16:30'],
  ])('%j depends on the context %#', (text, changes, start, end) => {
    expect(instants(parse(text, changes))).toEqual([start, end]);
  });
});

// ------------------------------------------------------------------ cells ----

const DAY_CELL: Changes = {
  cellStart: at('2026-10-26T06:30'),
  cellEnd: at('2026-10-26T16:30'),
  cellResourceIds: ['kdu'],
};
const RANGE_CELL: Changes = {
  cellStart: at('2026-10-26T06:30'),
  cellEnd: at('2026-10-28T16:30'),
  cellResourceIds: ['lbi', 'ych'],
};
const HALF_HOUR_CELL: Changes = {
  cellStart: at('2026-10-26T09:00'),
  cellEnd: at('2026-10-26T09:30'),
  cellResourceIds: ['sbe'],
};

describe('cells', () => {
  it.each<[string, Changes, string[], string, string]>([
    ['124 débit', DAY_CELL, ['kdu'], '2026-10-26T06:30', '2026-10-26T16:30'],
    ['124 débit 1,5j', DAY_CELL, ['kdu'], '2026-10-26T06:30', '2026-10-27T12:00'],
    ['118 métré ½j', HALF_HOUR_CELL, ['sbe'], '2026-10-26T09:00', '2026-10-26T12:00'],
    ['124 débit LBI', DAY_CELL, ['lbi'], '2026-10-26T06:30', '2026-10-26T16:30'],
    ['124 débit matin', DAY_CELL, ['kdu'], '2026-10-26T06:30', '2026-10-26T12:00'],
    ['124 débit 7h30-11h', DAY_CELL, ['kdu'], '2026-10-26T07:30', '2026-10-26T11:00'],
    ['124 débit jeu', DAY_CELL, ['kdu'], '2026-10-22T06:30', '2026-10-22T16:30'],
    ['124 débit 2h', DAY_CELL, ['kdu'], '2026-10-26T06:30', '2026-10-26T08:30'],
    ['124 débit à 10h', DAY_CELL, ['kdu'], '2026-10-26T10:00', '2026-10-26T16:30'],
    ['124 débit 7h', DAY_CELL, ['kdu'], '2026-10-26T06:30', '2026-10-26T14:30'],
    ['118 métré 7h', HALF_HOUR_CELL, ['sbe'], '2026-10-26T09:00', '2026-10-27T07:00'],
    ['121 pose', RANGE_CELL, ['lbi', 'ych'], '2026-10-26T06:30', '2026-10-28T16:30'],
    ['118 métré', HALF_HOUR_CELL, ['sbe'], '2026-10-26T09:00', '2026-10-26T09:30'],
  ])('%j typed on a cell', (text, cell, people, start, end) => {
    const entry = parse(text, cell);

    expect(entry.destination).toBe('grid');
    expect(entry.resourceIds).toEqual(people);
    expect(instants(entry)).toEqual([start, end]);
  });

  it('takes everyone on a range over several rows', () => {
    const entry = parse('121 pose', RANGE_CELL);

    expect(entry.resourceIds).toEqual(['lbi', 'ych']);
    expect(entry.estimateMinutes).toBe(1620);
  });

  it.each<[Changes, number, number, string[]]>([
    [DAY_CELL, dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), ['kdu']],
    [RANGE_CELL, dayNumber(2026, 10, 26), dayNumber(2026, 10, 28), ['lbi', 'ych']],
  ])('drafts an absence on a cell %#', (cell, first, last, people) => {
    const entry = parse('/vac', cell);

    expect(entry.destination).toBe('absence');
    expect(entry.absence).toEqual(draft(people, 'vacances', first, last, 'day'));
  });
});

// -------------------------------------------------------------- durations ----

const GENEVA = new WorkCalendar({ cantons: ['GE'] });
const VAUD = new WorkCalendar({ cantons: ['VD'] });

describe('durations', () => {
  it.each<[string, number, number, boolean]>([
    ['3j 121', 1620, 3, false],
    ['2,5j 121', 1350, 2.5, false],
    ['0,5j 121', 270, 0.5, false],
    ['½j 121', 270, 0.5, false],
    ['1j½ 121', 810, 1.5, false],
    // The app's own format, typed back.
    ['1 j ½ 121', 810, 1.5, false],
    ['2 j ½ 121', 1350, 2.5, false],
    ['1j ½ 121', 810, 1.5, false],
    ['1 ½ j 121', 810, 1.5, false],
    ['1 1/2 j 121', 810, 1.5, false],
    ['1 1/2j 121', 810, 1.5, false],
    ['1j1/2 121', 810, 1.5, false],
    ['1 jour et demi 121', 810, 1.5, false],
    ['2 journées et demie 121', 1350, 2.5, false],
    ['1/2j 121', 270, 0.5, false],
    ['demi-journée 121', 270, 0.5, false],
    ['2 jours 121', 1080, 2, false],
    ['2j 1j 121', 1620, 3, false],
    ['4h 121', 240, 0, false],
    ['1h30 121', 90, 0, false],
    ['90min 121', 90, 0, false],
    ['1,5h 121', 90, 0, false],
    ['4 heures 121', 240, 0, false],
    ['1j 4h 121', 780, 0, false],
    ['soudure 8h', 480, 0, false],
    ['100min 121', 90, 0, false],
    ['1h15 121', 90, 0, false],
    ['2,25j 121', 1350, 2.5, false],
    ['0,2j 121', 270, 0.5, false],
    ['27-29.10 2j LBI', 1620, 3, true],
    ['jeu-ven 3h LBI', 870, 2, true],
    ['lun 7h30-11h 2h LBI', 210, 0, true],
    ['lun LBI', 540, 1, false],
    ['ven LBI', 330, 1, false],
    ['lun matin LBI', 330, 0.5, false],
    ['S44 LBI', 2490, 5, false],
    ['jeu 2j LBI', 870, 2, false],
    ['lun 5j LBI', 2490, 5, false],
    ['lun aprem ½j LBI', 210, 0.5, false],
    // Typed days stay as typed once dated, even where wholeDays reads otherwise.
    ['lun à 10h 1j LBI', 330, 1, false],
    ['ven ½j LBI', 330, 0.5, false],
    ['jeu 1j½ LBI', 870, 1.5, false],
    // An afternoon start is half of its day: 1 j ends on Tuesday at noon.
    ['lun aprem 1j LBI', 540, 1, false],
    ['jeu aprem 2j LBI', 870, 2, false],
    ['lun 1j 4h LBI', 780, 0, false],
    ['lun 4h LBI', 240, 0, false],
    ['pose portail', 0, 0, false],
    ['/vac LBI lun 2j', 0, 0, false],
  ])('%j estimates %i minutes, %d days', (text, estimate, days, ignored) => {
    const entry = parse(text);

    expect(entry.estimateMinutes).toBe(estimate);
    expect(entry.estimateDays).toBe(days);
    expect(entry.ignoredDuration).toBe(ignored);
  });

  it.each<[string, WorkCalendar, string, string, number]>([
    // Jeûne genevois: Thursday 9 September 2027 in Geneva only.
    ['08.09 2j LBI', GENEVA, '2027-09-08T06:30', '2027-09-10T12:00', 2],
    ['08.09 2j LBI', VAUD, '2027-09-08T06:30', '2027-09-09T16:30', 2],
    ['09.09 1j LBI', GENEVA, '2027-09-10T06:30', '2027-09-10T12:00', 1],
    // Lundi du Jeûne: Monday 20 September 2027 in Vaud only.
    ['17.09 2j LBI', VAUD, '2027-09-17T06:30', '2027-09-21T16:30', 2],
    ['17.09 2j LBI', GENEVA, '2027-09-17T06:30', '2027-09-20T16:30', 2],
    ['20.09 ½j LBI', VAUD, '2027-09-21T06:30', '2027-09-21T12:00', 0.5],
    ['24.12 2j LBI', GENEVA, '2026-12-24T06:30', '2026-12-28T16:30', 2],
  ])('%j skips the holidays of the canton %#', (text, calendar, start, end, days) => {
    const entry = parse(text, { calendar });

    expect(instants(entry)).toEqual([start, end]);
    expect(entry.estimateDays).toBe(days);
  });

  it.each<[string, WorkCalendar, string]>([
    ['09.09 1j LBI', GENEVA, "jeu 09.09.2027 n'est pas travaillé (Jeûne genevois) : début le ven 10.09.2027"],
    ['20.09 ½j LBI', VAUD, "lun 20.09.2027 n'est pas travaillé (Lundi du Jeûne) : début le mar 21.09.2027"],
  ])('%j warns of a start on a cantonal holiday', (text, calendar, warning) => {
    expect(parse(text, { calendar }).warnings).toEqual([warning]);
  });

  it('follows the canton of the first person', () => {
    const geneva = new WorkCalendar({ cantons: ['GE'] });
    const vaud = new WorkCalendar({ cantons: ['VD'] });
    const calendarFor = (resourceId: string | null) => (resourceId === 'lbi' ? vaud : geneva);

    const inVaud = parse('08.09 2j LBI KDU', { calendarFor });
    const inGeneva = parse('08.09 2j KDU LBI', { calendarFor });

    expect(instants(inVaud)[1]).toBe('2027-09-09T16:30');
    expect(instants(inGeneva)[1]).toBe('2027-09-10T12:00');
    expect(inGeneva.interpretation.endsWith('mer 08.09.2027 06:30 → ven 10.09.2027 12:00 · 2 j')).toBe(true);
  });

  it('gives its days to a day duration token', () => {
    const entry = parse('lun 1j½ LBI 2h');

    expect(entry.tokens.map((token) => [token.kind, token.value])).toEqual([
      ['day', '2026-10-26'],
      ['duration', '1.5j'],
      ['person', 'lbi'],
      ['duration', '120'],
    ]);
    expect(instants(entry)).toEqual(['2026-10-26T06:30', '2026-10-27T15:00']);
  });

  it.each([
    'pose 1 j ½ LBI',
    'pose 1j ½ LBI',
    'pose 1 ½ j LBI',
    'pose 1 1/2j LBI',
    'pose 1 j 1/2 LBI',
    'pose 1 jour et demi LBI',
    'pose 1 journée et demie LBI',
  ])('gives a spaced half day to the duration in %j', (text) => {
    const entry = parse(text);

    expect([entry.title, entry.estimateDays, entry.estimateMinutes]).toEqual(['pose', 1.5, 810]);
    expect(entry.tokens.map((token) => token.kind)).toEqual(['duration', 'person']);
    expect(entry.tokens[0].value).toBe('1.5j');
    expect(entry.warnings).toEqual([]);
  });

  it('marks the ignored duration token', () => {
    const entry = parse('27-29.10 121 LBI 2j');
    const duration = entry.tokens.find((token) => token.kind === 'duration');

    expect(duration?.message).toBe('Durée ignorée : la plage tapée fixe la fin');
    expect(entry.warnings).toEqual(['Durée ignorée : la plage tapée fixe la fin']);
  });
});

// ------------------------------------------------------------------- type ----

describe('task type', () => {
  it.each([
    ['/montage 118', 'montage'],
    ['/atelier 118', 'debit'],
    ['/releve 118', 'releve'],
    ['/dessin 118', 'dessin'],
    ['/debit 118', 'debit'],
    ['/soudure 118', 'soudure'],
    ['/finition 118', 'finition'],
    ['/livraison 118', 'livraison'],
    ['/sav 118', 'sav'],
    ['/soustraitance 118', 'soustraitance'],
    ['/interne', 'interne'],
    ['/liv 118', 'livraison'],
    ['/bureau 118', 'releve'],
    ['/Dessin pose 121', 'dessin'],
    ['galva 129', 'soustraitance'],
    ['plans 121', 'dessin'],
    ['meulage 124', 'finition'],
    ['transport 112', 'livraison'],
    ['livraisons 112', 'livraison'],
    ['soudures 124', 'soudure'],
    ['nettoyage atelier', 'interne'],
    ['métré 118', 'releve'],
    ['Relevé de cotes', 'releve'],
    ['LBI jeu', 'montage'],
    ['SBE lun', 'releve'],
    ['KDU lun', 'debit'],
    ['THL lun', 'soustraitance'],
    ['121', 'soudure'],
    ['130 KDU lun', 'montage'],
    ['portail coulissant', 'soudure'],
    ['pose portail KDU', 'montage'],
  ])('%j is of type %s', (text, taskType) => {
    expect(parse(text).taskType).toBe(taskType);
  });
});

// ------------------------------------------------------------------ title ----

describe('title', () => {
  it.each([
    ['2026-121 montage marquise jeu-ven LBI YCH', 'montage marquise'],
    ['124 débit portail 1,5j KDU lun', 'débit portail'],
    ['thermolaquage RAL 7016 THL lun-mer', 'thermolaquage RAL 7016'],
    ['5231 pose garde-corps +montage S45 ?', 'pose garde-corps'],
    ['sav portail 109 demain à 10h 1h30 !', 'sav portail'],
    ['pose garde-corps, côté lac LBI jeu', 'pose garde-corps, côté lac'],
    ['LBI, YCH montage', 'montage'],
    ['121 LBI jeu', 'Marquise entrée école'],
    ['LBI jeu', 'Montage chantier'],
    ['  pose   portail  ', 'pose portail'],
    ['PORTAIL coulissant 124', 'PORTAIL coulissant'],
    ['#118 portail', '#118 portail'],
    ['relevé de cotes 118', 'relevé de cotes'],
    ['relevé DE cotes 118', 'relevé cotes'],
    ['"appeler avant" pose 121', 'pose'],
    ['portail! 124', 'portail'],
    ['/vac LBI lun', ''],
    ['débit HEA 200 KDU', 'débit HEA 200'],
    ['Ø 150 tube 121', 'Ø 150 tube'],
    ['121 et 124 portails', 'et 124 portails'],
    ['pose LBI - YCH 121', 'pose'],
    ['?', ''],
    ['S2 LBI soudure', 'soudure'],
    ['pose le portail LE', 'pose le portail'],
    ['pose : portail ; LBI', 'pose : portail'],
    ['LBI, YCH, KDU pose', 'pose'],
    ['pose LBI, portail', 'pose, portail'],
  ])('%j is titled %j', (text, title) => {
    expect(parse(text).title).toBe(title);
  });
});

// ---------------------------------------------------------- flags, notes ----

describe('flags and notes', () => {
  it.each([
    ['121 ?', true, false],
    ['121 !', false, true],
    ['121 ?!', true, true],
    ['portail! 124', false, true],
    ['121 urgent', false, true],
    ['121 provisoire', true, false],
    ['121 URGENT', false, true],
    ['121', false, false],
    ['S45? 121', true, false],
  ])('%j is provisional %s, urgent %s', (text, provisional, urgent) => {
    const entry = parse(text);

    expect([entry.provisional, entry.urgent]).toEqual([provisional, urgent]);
  });

  it.each<[string, number | null]>([
    ['<06.11 129', dayNumber(2026, 11, 6)],
    ['< 06.11 129', dayNumber(2026, 11, 6)],
    ['avant 06.11 129', dayNumber(2026, 11, 6)],
    ['avant le 06.11 129', dayNumber(2026, 11, 6)],
    ['avant ven 129', dayNumber(2026, 10, 23)],
    ['<05.01 129', dayNumber(2027, 1, 5)],
    ['<20.10 129', dayNumber(2026, 10, 20)],
    ['<06.11.2027 129', dayNumber(2027, 11, 6)],
    ['129', null],
  ])('%j has the deadline %s', (text, deadline) => {
    expect(parse(text).deadline).toBe(deadline);
  });

  // No « Requis » any more: « 2p » is part of the title, the days around it still read.
  it.each<[string, string, string | null]>([
    ['129 2p', '2p', null],
    ['129 3 personnes', '3 personnes', null],
    ['LBI pose 121 3p jeu 2j', 'pose 3p', '2026-10-22T06:30'],
  ])('%j keeps a number of people as plain title text', (text, title, start) => {
    const entry = parse(text);

    expect(entry.title).toBe(title);
    expect(instants(entry)[0]).toBe(start);
    expect(entry.warnings).toEqual([]);
  });

  it.each([
    ['"appeler avant" 121', 'appeler avant'],
    ['« attention vitrage » pose', 'attention vitrage'],
    ['"a" "b" pose', 'a\nb'],
    ['pose "non fermé, lun', 'non fermé, lun'],
    ['pose 121', ''],
  ])('%j notes %j', (text, notes) => {
    expect(parse(text).notes).toBe(notes);
  });

  it('does not parse a quoted note', () => {
    const entry = parse('pose "LBI lun 2j" 121');

    expect(entry.resourceIds).toEqual([]);
    expect(entry.start).toBeNull();
    expect(entry.estimateMinutes).toBe(0);
  });
});

// --------------------------------------------------------------- absences ----

describe('absences', () => {
  it.each<[string, AbsenceReason, string[], number, number, DayPart]>([
    ['/vac LBI 26.10-30.10', 'vacances', ['lbi'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 30), 'day'],
    ['/absent LBI', 'absent', ['lbi'], TODAY, TODAY, 'day'],
    ['/ecole NRE ven matin', 'ecole', ['nre'], dayNumber(2026, 10, 23), dayNumber(2026, 10, 23), 'morning'],
    ['/cie NRE S45', 'cie', ['nre'], dayNumber(2026, 11, 2), dayNumber(2026, 11, 6), 'day'],
    ['/militaire YCH 02.11-13.11', 'militaire', ['ych'], dayNumber(2026, 11, 2), dayNumber(2026, 11, 13), 'day'],
    ['/formation SBE lun-mer', 'formation', ['sbe'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 28), 'day'],
    [
      '/recup LBI demain aprem',
      'recuperation',
      ['lbi'],
      dayNumber(2026, 10, 23),
      dayNumber(2026, 10, 23),
      'afternoon',
    ],
    ['/conge KDU lun', 'conge', ['kdu'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), 'day'],
    ['/vac LBI YCH S44', 'vacances', ['lbi', 'ych'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 30), 'day'],
    ['/absent LBI lun 3j', 'absent', ['lbi'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 28), 'day'],
    ['/malade TFA', 'absent', ['tfa'], TODAY, TODAY, 'day'],
    ['/vacances LBI lun', 'vacances', ['lbi'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), 'day'],
    ['/mil YCH lun', 'militaire', ['ych'], dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), 'day'],
    ['/École NRE ven', 'ecole', ['nre'], dayNumber(2026, 10, 23), dayNumber(2026, 10, 23), 'day'],
  ])('%j is an absence', (text, reason, people, first, last, part) => {
    const entry = parse(text);

    expect(entry.destination).toBe('absence');
    expect(entry.absence).toEqual(draft(people, reason, first, last, part));
    expect([entry.start, entry.end, entry.estimateMinutes]).toEqual([null, null, 0]);
  });

  it('drafts an absence without person but calls it invalid', () => {
    const entry = parse('/vac 26.10');

    expect(entry.destination).toBe('invalid');
    expect(entry.absence).toEqual(draft([], 'vacances', dayNumber(2026, 10, 26), dayNumber(2026, 10, 26)));
    expect(entry.warnings).toEqual(['Indiquer qui est absent']);
  });

  it('ignores a typed duration on an absence range', () => {
    const entry = parse('/vac LBI 26.10-30.10 3j');

    expect(entry.ignoredDuration).toBe(true);
    expect(entry.absence).not.toBeNull();
    expect(entry.absence?.lastDay).toBe(dayNumber(2026, 10, 30));
  });
});

// --------------------------------------------------------------- warnings ----

describe('warnings', () => {
  it.each([
    ['ven aprem livraison 112 LBI', "Pas d'après-midi travaillé le vendredi : matin retenu"],
    ['25.12 LBI montage', "ven 25.12 n'est pas travaillé (Noël) : début le lun 28.12"],
    ['31.12 LBI', "jeu 31.12 n'est pas travaillé (Restauration de la République) : début le lun 04.01.2027"],
    ['sam LBI', "sam 24.10 n'est pas travaillé : début le lun 26.10"],
    ['jeu à 7h15 LBI', 'à 7h15 arrondi à 07:30'],
    ['jeu 7h10-11h LBI', '7h10-11h arrondi à 07:00-11:00'],
    ['jeu 7h20 LBI', '7h20 arrondi à 07:30'],
    ['100min 121', '100min arrondi à 1 h 30'],
    ['2,25j 121', '2,25j arrondi à 2 j ½'],
    ['0,2j 121', '0,2j arrondi à ½ j'],
    ['lun 1,4j LBI', '1,4j arrondi à 1 j ½'],
    ['27-29.10 121 LBI 2j', 'Durée ignorée : la plage tapée fixe la fin'],
    ['26.10 28.10 LBI', 'Plusieurs dates : la première est retenue'],
    ['lun mar LBI', 'Plusieurs jours : le premier est retenu'],
    ['ven 27.10 LBI', '« ven » ne correspond pas au mar 27.10 : la date est retenue'],
    ['jeu à 7h à 9h LBI', 'Plusieurs heures de début : la première est retenue'],
    ['ven à 12h LBI', 'Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30'],
    ['/vac 26.10', 'Indiquer qui est absent'],
    ['/absent LBI lun 7h-11h', 'Les heures ne comptent pas pour une absence : seuls les jours sont retenus'],
    ['lun 11h-7h LBI', 'La fin précède le début : 30 min retenues'],
    ['/recup LBI demain aprem', "Pas d'après-midi travaillé le vendredi"],
  ])('%j warns %j', (text, warning) => {
    expect(parse(text).warnings).toContain(warning);
  });

  it.each([
    '2026-121 montage marquise jeu-ven LBI YCH',
    '124 débit portail 1,5j KDU lun',
    'jeu 22.10 LBI',
    'lun 26.10 LBI',
    'S45 lun LBI',
    '/vac LBI 26.10-30.10',
  ])('%j is consistent, without warning', (text) => {
    expect(parse(text).warnings).toEqual([]);
  });
});

// --------------------------------------------------------- interpretation ----

describe('interpretation', () => {
  it.each([
    [
      '2026-121 montage marquise jeu-ven LBI YCH',
      '2026-121 Commune de Marsens · Montage marquise · LBI, YCH · jeu 22.10 06:30 → ven 23.10 12:00 · 2 j',
    ],
    ['129 relevé de cotes', '2026-129 PPE Les Cèdres · Relevé de cotes · À planifier'],
    [
      '<06.11 129 fabrication 2j',
      '2026-129 PPE Les Cèdres · Fabrication · À planifier · 2 j · avant le ven 06.11',
    ],
    [
      'livraison 112 ven matin',
      '2026-112 M. Perret · Livraison · Non affecté · ven 23.10 06:30 → ven 23.10 12:00 · 1 j',
    ],
    [
      'sav portail 109 demain à 10h 1h30 !',
      '2026-109 Mme Rossier · Sav portail · Non affecté · ven 23.10 10:00 → ven 23.10 11:30 · 1 h 30 · urgent',
    ],
    [
      '5231 pose garde-corps +montage S45 ?',
      '2026-5231 Implenia SA · Pose garde-corps · LBI, YCH, JMF, MDU · ' +
        'lun 02.11 06:30 → ven 06.11 12:00 · 5 j · provisoire',
    ],
    ['/vac LBI 26.10-30.10', 'Vacances · LBI · lun 26.10 → ven 30.10'],
    ['/ecole NRE ven matin', 'École professionnelle · NRE · ven 23.10 matin'],
    ['/vac 26.10', 'Vacances · qui ? · lun 26.10'],
    ['S2 LBI', 'Montage chantier · LBI · lun 11.01.2027 06:30 → ven 15.01.2027 12:00 · 5 j'],
    ['', 'Saisie vide'],
    ['?', 'Saisie incomplète'],
    ['255 portail LBI', '2026-255 · Portail · LBI · À planifier'],
    [
      'LBI pose 121 jeu',
      '2026-121 Commune de Marsens · Pose · LBI · jeu 22.10 06:30 → jeu 22.10 16:30 · 1 j',
    ],
    [
      'jeu 2j 121 LBI',
      '2026-121 Commune de Marsens · Marquise entrée école · LBI · jeu 22.10 06:30 → ven 23.10 12:00 · 2 j',
    ],
    [
      'lun 5j 121 LBI',
      '2026-121 Commune de Marsens · Marquise entrée école · LBI · lun 26.10 06:30 → ven 30.10 12:00 · 5 j',
    ],
    ['lun aprem ½j LBI', 'Montage chantier · LBI · lun 26.10 13:00 → lun 26.10 16:30 · ½ j'],
    // The length its bar will show: not whole half days, so in hours.
    ['lun à 10h 1j LBI', 'Montage chantier · LBI · lun 26.10 10:00 → lun 26.10 16:30 · 5 h 30'],
    ['lun à 10h 5h30 LBI', 'Montage chantier · LBI · lun 26.10 10:00 → lun 26.10 16:30 · 5 h 30'],
    // Both end on Friday at noon, a whole day: the confirmation says what the bar shows.
    ['jeu 1j½ LBI', 'Montage chantier · LBI · jeu 22.10 06:30 → ven 23.10 12:00 · 2 j'],
    ['ven ½j LBI', 'Montage chantier · LBI · ven 23.10 06:30 → ven 23.10 12:00 · 1 j'],
    // An afternoon start is half of its day: 2 j from Thursday 13:00 end Monday noon.
    ['jeu apm 2j LBI', 'Montage chantier · LBI · jeu 22.10 13:00 → lun 26.10 12:00 · 2 j'],
    ['lun apm 2j LBI', 'Montage chantier · LBI · lun 26.10 13:00 → mer 28.10 12:00 · 2 j'],
    ['ven matin LBI', 'Montage chantier · LBI · ven 23.10 06:30 → ven 23.10 12:00 · 1 j'],
    ['pose 1 j ½ LBI', 'Pose · LBI · À planifier · 1 j ½'],
    ['lun 2 j ½ LBI', 'Montage chantier · LBI · lun 26.10 06:30 → mer 28.10 12:00 · 2 j ½'],
    ['lun 7h30-11h LBI', 'Montage chantier · LBI · lun 26.10 07:30 → lun 26.10 11:00 · 3 h 30'],
    ['124 portail 1j½', '2026-124 Gérance Rhône SA · Portail · À planifier · 1 j ½'],
    ['124 portail 1j 4h', '2026-124 Gérance Rhône SA · Portail · À planifier · 1 j 4 h'],
    ['124 portail 4h30', '2026-124 Gérance Rhône SA · Portail · À planifier · 4 h 30'],
  ])('%j reads %j', (text, sentence) => {
    expect(parse(text).interpretation).toBe(sentence);
  });
});

// ----------------------------------------------------------------- tokens ----

describe('tokens', () => {
  it('carries offsets in the typed text', () => {
    const entry = parse('2026-121 montage marquise jeu-ven LBI YCH');

    expect(entry.tokens.map((token) => [token.kind, token.text, token.start, token.end])).toEqual([
      ['project', '2026-121', 0, 8],
      ['day', 'jeu-ven', 26, 33],
      ['person', 'LBI', 34, 37],
      ['person', 'YCH', 38, 41],
    ]);
    expect(entry.tokens.map((token) => token.value)).toEqual(['2026-121', '2026-10-22/2026-10-23', 'lbi', 'ych']);
  });

  it('follows accents and combining marks in its offsets', () => {
    const composed = parse('Sébastien lun');
    const decomposed = parse(`Se${COMBINING_ACUTE}bastien lun`);

    expect([composed.tokens[0].text, composed.tokens[0].end]).toEqual(['Sébastien', 9]);
    expect(decomposed.tokens[0].text).toBe(`Se${COMBINING_ACUTE}bastien`);
    expect(decomposed.tokens[1].start).toBe(11);
    expect(decomposed.resourceIds).toEqual(['sbe']);
  });

  it('understands the typographic apostrophe', () => {
    const entry = parse(`aujourd${RIGHT_QUOTE}hui LBI`);

    expect(entry.tokens[0].text).toBe(`aujourd${RIGHT_QUOTE}hui`);
    expect(instants(entry)).toEqual(['2026-10-22T06:30', '2026-10-22T16:30']);
  });

  it('makes a flag attached to a word its own token', () => {
    const entry = parse('S45? 121');

    expect(entry.tokens.map((token) => [token.kind, token.text])).toEqual([
      ['week', 'S45'],
      ['provisional', '?'],
      ['project', '121'],
    ]);
  });

  it('turns a bare hour token into a time or a duration', () => {
    const timed = parse('lun 7h30 LBI');
    const counted = parse('pose 7h30');

    expect(timed.tokens.map((token) => [token.kind, token.value])[1]).toEqual(['time', '07:30']);
    expect(counted.tokens.map((token) => [token.kind, token.value])).toEqual([['duration', '450']]);
  });

  it.each([
    '»',
    '+',
    '@',
    '/',
    '<',
    '---',
    '...',
    '1/2',
    '00:00-24:00',
    'S53',
    '29.02',
    '99.99',
    'lun-',
    '«',
    '"',
    '½',
    'à',
    '+ +',
    'LBI+',
    'x 200',
    'avant',
    'du',
    'de 7h',
    '2026-',
    '-118',
    'S44-',
    '1,5',
    'ßtraße 12',
    'lun 3000j LBI',
    '/vac LBI 9999j',
    '31.12.9999 LBI',
    '31.12.9999 - 25.12',
    '<31.12.9999 121',
    'S52-S53',
    '200j 200j',
    '23h59 lun',
    'tube 2"',
  ])('never throws on the odd input %j', (text) => {
    const entry = parse(text);

    expect(['grid', 'unassigned', 'backlog', 'absence', 'invalid']).toContain(entry.destination);
  });
});

// ---------------------------------------------------------------- helpers ----

describe('helpers', () => {
  it.each([
    [870, '14 h 30'],
    [540, '9 h'],
    [30, '30 min'],
    [90, '1 h 30'],
    [240, '4 h'],
    [570, '9 h 30'],
    [2490, '41 h 30'],
    [0, '0 min'],
    [-30, '0 min'],
  ])('formats the duration %i as %j', (minutes, text) => {
    expect(formatDuration(minutes)).toBe(text);
  });

  it.each([
    [2, '2 j'],
    [1, '1 j'],
    [1.5, '1 j ½'],
    [0.5, '½ j'],
    [12.5, '12 j ½'],
    [0, '0 j'],
  ])('formats %d days as %j', (days, text) => {
    expect(formatDays(days)).toBe(text);
  });

  it.each([
    ['2026-10-26T06:30', '2026-10-30T12:00', '5 j'],
    ['2026-10-22T06:30', '2026-10-23T12:00', '2 j'],
    ['2026-10-23T06:30', '2026-10-23T12:00', '1 j'],
    ['2026-10-26T06:30', '2026-10-26T12:00', '½ j'],
    ['2026-10-26T13:00', '2026-10-27T16:30', '1 j ½'],
    ['2026-10-26T10:00', '2026-10-26T16:30', '5 h 30'],
    ['2026-10-26T06:30', '2026-10-26T07:00', '30 min'],
    ['2026-12-24T06:30', '2026-12-28T16:30', '2 j'],
  ])('formats the span %s to %s as %j', (start, end, text) => {
    expect(formatSpan(CONTEXT.calendar, at(start), at(end))).toBe(text);
  });

  it.each([
    ['Après-midi', 'apres-midi'],
    [`AUJOURD${RIGHT_QUOTE}HUI`, "aujourd'hui"],
    ['Ø 150', 'ø 150'],
    ['1j½', '1j½'],
    ['Gérance Rhône', 'gerance rhone'],
  ])('folds %j to %j', (text, folded) => {
    expect(foldText(text)).toBe(folded);
  });
});

// ------------------------------------------------------ review regressions ----

const LUNCH_CELL: Changes = {
  cellStart: at('2026-10-26T12:00'),
  cellEnd: at('2026-10-26T12:30'),
  cellResourceIds: ['kdu'],
};
const CHRISTMAS_CELL: Changes = {
  cellStart: at('2026-12-25T06:30'),
  cellEnd: at('2026-12-25T16:30'),
  cellResourceIds: ['kdu'],
};

describe('review regressions', () => {
  it.each<[string, string, string | null, number]>([
    ['124 débit 1.5 jours KDU lun', 'débit', '2026-10-26T06:30', 870],
    ['124 débit 1.5 j KDU', 'débit', null, 810],
    ['soudure 2.5 h lun', 'soudure', '2026-10-26T06:30', 150],
    ['0.5 j 121', 'Marquise entrée école', null, 270],
    ['pliage tôle 1.5 mm 124 KDU lun', 'pliage tôle 1.5 mm', '2026-10-26T06:30', 540],
    ['débit barres 6.5 m 121 KDU', 'débit barres 6.5 m', null, 0],
    ['main courante tube Ø 26.9 121 LBI', 'main courante tube Ø 26.9', null, 0],
  ])('reads the decimal with a dot in %j as a measure, not a date', (text, title, start, estimate) => {
    const entry = parse(text);

    expect(entry.title).toBe(title);
    expect(instants(entry)[0]).toBe(start);
    expect(entry.estimateMinutes).toBe(estimate);
    expect(entry.warnings).toEqual([]);
    expect(entry.tokens.every((token) => token.kind !== 'date')).toBe(true);
  });

  it('gives an invalid entry for a calendar without working day', () => {
    const closures: number[] = [];
    for (let offset = 0; offset < 4000; offset += 1) closures.push(TODAY + offset);
    const closed = new WorkCalendar({ closures });

    const entry = parse('demain LBI', { calendar: closed });

    expect(entry.destination).toBe('invalid');
    expect(entry.warnings).toEqual([UNREADABLE_WARNING]);
  });

  it.each<[string, string | null, string | null]>([
    ['29-02.11 LBI', '2026-10-29T06:30', '2026-11-02T16:30'],
    ['28-05.01 LBI', '2026-12-28T06:30', '2027-01-05T16:30'],
    ['28.12-05.01 LBI', '2026-12-28T06:30', '2027-01-05T16:30'],
    ['S53-S1 LBI', '2026-12-28T06:30', '2027-01-08T12:00'],
    ['27.10-03.10 LBI', null, null],
    ['S45-S44 LBI', null, null],
  ])('wraps the range %j typed backwards only when short', (text, start, end) => {
    expect(instants(parse(text))).toEqual([start, end]);
  });

  it('takes the month before for a short range absence', () => {
    const entry = parse('/vac LBI 29-02.11');

    expect(entry.absence).toEqual(draft(['lbi'], 'vacances', dayNumber(2026, 10, 29), dayNumber(2026, 11, 2)));
  });

  it.each<[string, Changes, number, number, DayPart, string[]]>([
    ['/vac LBI lun 5j', {}, dayNumber(2026, 10, 26), dayNumber(2026, 10, 30), 'day', []],
    ['/vac LBI ven 2j', {}, dayNumber(2026, 10, 23), dayNumber(2026, 10, 26), 'day', []],
    ['/absent LBI 1j', { today: dayNumber(2026, 10, 23) }, dayNumber(2026, 10, 23), dayNumber(2026, 10, 23), 'day', []],
    ['/vac LBI lun ½j', {}, dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), 'morning', []],
    ['/vac LBI lun aprem ½j', {}, dayNumber(2026, 10, 26), dayNumber(2026, 10, 26), 'afternoon', []],
    [
      '/vac LBI lun 4h',
      {},
      dayNumber(2026, 10, 26),
      dayNumber(2026, 10, 26),
      'morning',
      ['Absence comptée en demi-journée'],
    ],
    [
      '/vac LBI lun 1,5j',
      {},
      dayNumber(2026, 10, 26),
      dayNumber(2026, 10, 27),
      'day',
      ['Absence comptée en jours entiers : 2 j'],
    ],
    [
      '/vac LBI sam 2j',
      {},
      dayNumber(2026, 10, 26),
      dayNumber(2026, 10, 27),
      'day',
      ["sam 24.10 n'est pas travaillé : début le lun 26.10"],
    ],
    ['/vac 5j', DAY_CELL, dayNumber(2026, 10, 26), dayNumber(2026, 10, 30), 'day', []],
  ])('counts working days for the absence %j', (text, changes, first, last, part, warnings) => {
    const entry = parse(text, changes);

    expect(entry.absence).not.toBeNull();
    expect([entry.absence?.firstDay, entry.absence?.lastDay, entry.absence?.part]).toEqual([first, last, part]);
    expect(entry.warnings).toEqual(warnings);
  });

  it('does not take an inch mark for a note', () => {
    const entry = parse('débit tube 2" galva 121 KDU lun');

    expect(entry.title).toBe('débit tube 2" galva');
    expect(entry.notes).toBe('');
    expect(entry.projectNumber).toBe('2026-121');
    expect(entry.resourceIds).toEqual(['kdu']);
    expect(instants(entry)).toEqual(['2026-10-26T06:30', '2026-10-26T16:30']);
  });

  it('warns of an unclosed quote', () => {
    expect(parse('pose "non fermé, lun').warnings).toEqual(['Guillemet non fermé : la suite est une note']);
  });

  it.each([
    ['thermolaquage RAL 7016 mat THL lun-mer', 'thermolaquage RAL 7016 mat', '2026-10-28T16:30'],
    ['thermolaquage RAL 9010 mat THL lun', 'thermolaquage RAL 9010 mat', '2026-10-26T16:30'],
    ['thermolaquage RAL 9010 THL lun mat', 'thermolaquage RAL 9010', '2026-10-26T12:00'],
  ])('reads mat after a colour in %j as a finish', (text, title, end) => {
    const entry = parse(text);

    expect(entry.title).toBe(title);
    expect(instants(entry)[1]).toBe(end);
  });

  it.each<[string, string, string, string[]]>([
    [
      'ven 14h-15h LBI',
      '2026-10-26T06:30',
      '2026-10-26T07:30',
      [
        'Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30',
        '14:00-15:00 hors des heures de travail : durée de 1 h gardée',
      ],
    ],
    [
      'lun 12:00-13:00 LBI',
      '2026-10-26T13:00',
      '2026-10-26T14:00',
      ['12:00-13:00 hors des heures de travail : durée de 1 h gardée'],
    ],
    [
      'ven 13h LBI',
      '2026-10-26T06:30',
      '2026-10-26T07:00',
      ['Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30'],
    ],
    ['lun 17h LBI', '2026-10-26T06:30', '2026-10-27T15:30', ['« 17h » compris comme une durée de 17 h']],
    ['soudure 2-3h lun', '2026-10-26T06:30', '2026-10-26T09:30', ['« 2-3h » compris comme une durée de 3 h']],
    [
      'lun-mer matin LBI',
      '2026-10-26T06:30',
      '2026-10-28T12:00',
      ['Plage continue du lun 26.10 06:30 au mer 28.10 12:00'],
    ],
  ])('explains the hours outside working time in %j', (text, start, end, warnings) => {
    const entry = parse(text);

    expect(instants(entry)).toEqual([start, end]);
    expect(entry.warnings).toEqual(warnings);
  });

  it('reads an hour range without unit as an estimate', () => {
    const entry = parse('soudure 2 à 3h');

    expect([entry.start, entry.estimateMinutes, entry.destination]).toEqual([null, 180, 'backlog']);
    expect(entry.tokens.map((token) => token.kind)).toEqual(['duration']);
  });

  it.each(['MDU soudure', '+zzz soudure', 'OTI soudure'])(
    'does not fall back to the cell row for the named but unknown person in %j',
    (text) => {
      const entry = parse(text, DAY_CELL);

      expect(entry.resourceIds).toEqual([]);
      expect(entry.destination).toBe('unassigned');
    },
  );

  it.each<[Changes, string, string]>([
    [LUNCH_CELL, '2026-10-26T13:00', 'Début reporté au lun 26.10 13:00'],
    [CHRISTMAS_CELL, '2026-12-28T06:30', 'Début reporté au lun 28.12 06:30 (Noël)'],
  ])('warns when the cell start moves %#', (cell, start, warning) => {
    const entry = parse('124 débit', cell);

    expect(instants(entry)[0]).toBe(start);
    expect(entry.warnings).toEqual([warning]);
  });

  it.each([
    ['/vac LBI lun ski', 'ski', 'Vacances · LBI · lun 26.10 · ski'],
    ['/vac LBI lun "chalet"', 'chalet', 'Vacances · LBI · lun 26.10 · chalet'],
    ['/vac LBI lun', '', 'Vacances · LBI · lun 26.10'],
  ])('keeps the note of the absence %j', (text, note, sentence) => {
    const entry = parse(text);

    expect(entry.absence).not.toBeNull();
    expect(entry.absence?.note).toBe(note);
    expect(entry.interpretation).toBe(sentence);
  });

  it.each([
    ['/vac LBI sam', "sam 24.10 n'est pas travaillé"],
    ['/vac LBI 25.12', "ven 25.12 n'est pas travaillé (Noël)"],
    ['/vac LBI sam-dim', 'Aucun jour travaillé du sam 24.10 au dim 25.10'],
    ['/vac LBI 20.10', 'Date passée : mar 20.10'],
    ['20.10 LBI', 'Date passée : mar 20.10'],
    ['S40 LBI', 'Date passée : lun 28.09'],
  ])('warns of the odd days in %j', (text, warning) => {
    expect(parse(text).warnings).toContain(warning);
  });

  it.each([
    ['01.05 LBI', '2027-05-03T06:30'],
    ['15.09 LBI', '2027-09-15T06:30'],
    ['23.09 LBI', '2026-09-23T06:30'],
    ['S30 LBI', '2027-07-26T06:30'],
    ['S40 LBI', '2026-09-28T06:30'],
  ])('reads the long past date %j as next year', (text, start) => {
    expect(instants(parse(text))[0]).toBe(start);
  });

  it('sets the holidays from the calendar of the first person', () => {
    const geneva = new WorkCalendar({ cantons: ['GE'] });
    const vaud = new WorkCalendar({ cantons: ['VD'] });
    const calendarFor = (resourceId: string | null) => (resourceId === 'lbi' ? vaud : geneva);

    const inVaud = parse('31.12 LBI KDU', { calendarFor });
    const inGeneva = parse('31.12 KDU LBI', { calendarFor });

    expect(instants(inVaud)[0]).toBe('2026-12-31T06:30');
    expect(instants(inGeneva)[0]).toBe('2027-01-04T06:30');
  });

  const FUZZ_FRAGMENTS = [
    'lun',
    'ven',
    'sam',
    'demain',
    'jeu-mar',
    '27.10',
    '25.12',
    '29-02.11',
    '27.10-03.10',
    'S44',
    'S45-S44',
    'matin',
    'aprem',
    'mat',
    '7h',
    '16h30',
    '17h',
    '7h30-11h',
    '14h-15h',
    '12:00-13:00',
    'à 10h',
    '2 à 3h',
    '3j',
    '½j',
    '1.5 j',
    '4h',
    '3000j',
    '0h',
    '2p',
    '/vac',
    '/sou',
    '?',
    '<06.11',
    '"note',
    'tube 2"',
    'LBI',
    'MDU',
    'OTI',
    '+montage',
    '121',
    'RAL 7016',
    '1.5 mm',
    'pose',
    '31.12.9999',
  ];
  const FUZZ_CELLS: Changes[] = [{}, DAY_CELL, RANGE_CELL, HALF_HOUR_CELL, LUNCH_CELL];

  function fuzzTexts(): string[] {
    const count = FUZZ_FRAGMENTS.length;
    const pairs = FUZZ_FRAGMENTS.flatMap((first) => FUZZ_FRAGMENTS.map((second) => `${first} ${second}`));
    const triples: string[] = [];
    for (let index = 0; index < count * 10; index += 1) {
      triples.push([1, 7, 13].map((step) => FUZZ_FRAGMENTS[(index * step) % count]).join(' '));
    }
    return [...pairs, ...triples];
  }

  it('keeps fuzzed entries consistent', () => {
    const calendar = CONTEXT.calendar;
    const problems: string[] = [];
    fuzzTexts().forEach((text, index) => {
      const cell = FUZZ_CELLS[index % FUZZ_CELLS.length];
      const entry = parse(text, cell);
      const checks: Record<string, boolean> = {
        unreadable: !entry.warnings.includes(UNREADABLE_WARNING),
        'grid without people': entry.destination !== 'grid' || entry.resourceIds.length > 0,
      };
      if (entry.start !== null && entry.end !== null) {
        checks['end before start'] = entry.end > entry.start;
        checks['start not worked'] = calendar.isWorked(entry.start);
        checks['end not worked'] = calendar.isWorked(entry.end - 1);
        checks['off the half hour'] = !(minuteOfDay(entry.start) % 30 || entry.end % 30);
        checks.estimate = entry.estimateMinutes > 0 && entry.estimateMinutes % 30 === 0;
      }
      if (entry.absence !== null) checks['absence days'] = entry.absence.firstDay <= entry.absence.lastDay;
      for (const [name, ok] of Object.entries(checks)) {
        if (!ok) problems.push(`${name}: ${JSON.stringify(text)} ${JSON.stringify(cell)}`);
      }
    });

    expect(problems).toEqual([]);
  });

  it('finds the dossier of a hand-written suffix', () => {
    const projects = new Map<string, ProjectInfo>([
      ['2024-4493-A1', { number: '2024-4493-A1', client: 'AIG', designation: 'Habillage lot A1' }],
      ['2026-5102-REV03', { number: '2026-5102-REV03', client: 'Coop', designation: 'Auvent rév. 3' }],
    ]);
    const findProject = (number: string) => projects.get(number) ?? null;

    const found = parse('2024-4493-a1 pose', { findProject });
    expect(found.projectNumber).toBe('2024-4493-A1');
    expect(found.project).not.toBeNull();
    const revised = parse('5102-rev03 pose', { findProject });
    expect(revised.projectNumber).toBe('2026-5102-REV03');
    expect(parse('118-2 pose').projectNumber).toBe('2026-118-2');
  });
});
