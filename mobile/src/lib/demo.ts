/**
 * « Mode démo », as on the desktop (`projectflow/demo.py`): sample data held in memory,
 * nothing read from or written to Microsoft. Lets the screens be tried before the
 * mobile sign-in is allowed in Microsoft Entra.
 */

import type { PlanningData } from '@/lib/planning/models';
import { dateParts, mondayOf, todayNumber, weekday } from '@/lib/planning/walltime';
import type { Cell } from '@/lib/repertoire/rows';

const DEMO_FIRST_PROJECT_ID = 4996;

const PROJECTS: [string, string, string][] = [
  ['Garage du Lac', 'P. Rochat', 'Structure auvent parking'],
  ['Commune de Bulle', 'S. Clerc', 'Main courante école'],
  ['Régie Favre', 'M. Dupasquier', 'Garde-corps escalier extérieur'],
  ['Privé', 'A. Morel', 'Escalier hélicoïdal villa'],
  ['Transports Morel', 'J. Morel', 'Portail coulissant dépôt'],
];

/** Used range of the year's worksheet: filled rows, then free pre-numbered rows. */
export function demoUsedRange(year: number): { address: string; values: Cell[][]; text: string[][] } {
  const values: Cell[][] = [['No.', 'Date', 'Client', 'Contact', 'Désignation']];
  const text: string[][] = [['No.', 'Date', 'Client', 'Contact', 'Désignation']];
  PROJECTS.forEach(([client, contact, designation], index) => {
    const number = `${year}-${DEMO_FIRST_PROJECT_ID + index}`;
    const date = `${String(5 + index * 4).padStart(2, '0')}.10.${year}`;
    values.push([number, date, client, contact, designation]);
    text.push([number, date, client, contact, designation]);
  });
  for (let index = PROJECTS.length; index < PROJECTS.length + 3; index += 1) {
    const number = `${year}-${DEMO_FIRST_PROJECT_ID + index}`;
    values.push([number, '', '', '', '']);
    text.push([number, '', '', '', '']);
  }
  return { address: `'${year}'!A1:E${values.length}`, values, text };
}

function iso(day: number, time?: string): string {
  const { year, month, date } = dateParts(day);
  const text = `${year}-${String(month).padStart(2, '0')}-${String(date).padStart(2, '0')}`;
  return time ? `${text}T${time}` : text;
}

/** A week of the board around today, with every kind of bar the desktop draws. */
export function demoPlanning(today: number = todayNumber()): PlanningData {
  // On a weekend the board opens on the next Monday, as on the desktop (`shown_day`).
  const monday = mondayOf(today) + (weekday(today) > 4 ? 7 : 0);
  const year = dateParts(today).year;
  const number = (index: number) => `${year}-${DEMO_FIRST_PROJECT_ID + index}`;
  const cache = (index: number) => ({
    client: PROJECTS[index][0],
    contact: PROJECTS[index][1],
    designation: PROJECTS[index][2],
    managed_by: 'LM',
  });
  return {
    meta: null,
    resources: [
      { id: 'kdu', name: 'Karim', initials: 'KDU', team: 'atelier', order: 1 },
      { id: 'lbi', name: 'Luca', initials: 'LBI', team: 'atelier', order: 2 },
      {
        id: 'ych',
        name: 'Yann',
        initials: 'YCH',
        team: 'atelier',
        order: 3,
        recurring_off: [{ weekday: 2, part: 'day', reason: 'ecole' }],
      },
      { id: 'mro', name: 'Marc', initials: 'MRO', team: 'montage', order: 1 },
      { id: 'jpe', name: 'Julien', initials: 'JPE', team: 'montage', order: 2 },
      { id: 'galva', name: 'Galvaswiss AG', initials: 'Galva', team: 'soustraitants', kind_of_resource: 'subcontractor' },
    ],
    absences: [
      { id: 'vac', resource_id: 'lbi', reason: 'vacances', first_day: iso(monday + 3), last_day: iso(monday + 8) },
    ],
    tasks: [
      {
        id: 'd1',
        project_number: number(0),
        title: 'Soudure',
        task_type: 'soudure',
        start: iso(monday, '06:30'),
        end: iso(monday + 1, '16:30'),
        assignments: { kdu: {}, ych: {} },
        required_people: 2,
        project_cache: cache(0),
      },
      {
        id: 'd2',
        project_number: number(2),
        title: 'Soudure',
        task_type: 'soudure',
        status: 'provisional',
        start: iso(monday + 2, '06:30'),
        end: iso(monday + 3, '16:30'),
        assignments: { kdu: {} },
        project_cache: cache(2),
      },
      {
        id: 'd3',
        project_number: number(1),
        title: 'Débit',
        task_type: 'debit',
        start: iso(monday, '06:30'),
        end: iso(monday + 2, '16:30'),
        assignments: { lbi: {} },
        project_cache: cache(1),
      },
      {
        id: 'd4',
        project_number: number(3),
        title: 'Finition',
        task_type: 'finition',
        start: iso(monday + 3, '06:30'),
        end: iso(monday + 4, '12:00'),
        assignments: { ych: {} },
        project_cache: cache(3),
      },
      {
        id: 'd5',
        project_number: number(4),
        title: 'Montage',
        task_type: 'montage',
        start: iso(monday + 1, '06:30'),
        end: iso(monday + 2, '16:30'),
        assignments: { mro: {}, jpe: {} },
        required_people: 2,
        project_cache: cache(4),
      },
      {
        id: 'd6',
        project_number: number(2),
        title: 'Montage',
        task_type: 'montage',
        start: iso(monday + 3, '06:30'),
        end: iso(monday + 3, '16:30'),
        assignments: { mro: {} },
        required_people: 2,
        project_cache: cache(2),
      },
      {
        id: 'd7',
        project_number: number(4),
        title: 'Livraison',
        task_type: 'livraison',
        start: iso(monday + 4, '06:30'),
        end: iso(monday + 4, '12:00'),
        assignments: { jpe: {} },
        project_cache: cache(4),
      },
      {
        id: 'd8',
        project_number: number(2),
        title: 'Galvanisation',
        task_type: 'soustraitance',
        start: iso(monday, '06:30'),
        end: iso(monday + 4, '12:00'),
        assignments: { galva: {} },
        project_cache: cache(2),
      },
      {
        id: 'd9',
        project_number: number(1),
        title: 'Relevé de cotes',
        task_type: 'releve',
        start: iso(monday + 2, '06:30'),
        end: iso(monday + 2, '12:00'),
        assignments: {},
        project_cache: cache(1),
      },
      {
        id: 'b1',
        project_number: number(1),
        title: 'Fabrication – Main courante école',
        task_type: 'soudure',
        backlog_rank: 'a',
        project_cache: cache(1),
      },
      {
        id: 'b2',
        project_number: number(3),
        title: 'Montage escalier',
        task_type: 'montage',
        required_people: 2,
        backlog_rank: 'b',
        project_cache: cache(3),
      },
    ],
  };
}
