import { dossierSuggestions, hasToken, replaceTypedWord, toggleToken, typedWord } from '@/lib/planning/entryShortcuts';
import { WorkCalendar } from '@/lib/planning/calendar';
import { DEFAULT_TASK_TYPES, DEFAULT_TEAMS } from '@/lib/planning/models';
import { formatDays, parseEntry, type EntryContext } from '@/lib/planning/quickEntry';
import { dayNumber } from '@/lib/planning/walltime';

const projects = [
  { number: '2026-4996', client: 'Garage du Lac', contact: 'P. Rochat', designation: 'Structure auvent parking' },
  { number: '2026-4997', client: 'Commune de Bulle', contact: 'S. Clerc', designation: 'Main courante école' },
  { number: '2026-5000', client: 'Transports Morel', contact: 'J. Morel', designation: 'Portail coulissant dépôt' },
];

describe('raccourcis de la saisie rapide', () => {
  it('adds a person, then removes it with the same touch', () => {
    expect(toggleToken('4996 soudure', 'KDU', 'person')).toBe('4996 soudure KDU ');
    expect(toggleToken('4996 soudure KDU ', 'KDU', 'person')).toBe('4996 soudure ');
    expect(toggleToken('', 'KDU', 'person')).toBe('KDU ');
    expect(hasToken('4996 kdu', 'KDU')).toBe(true);
  });

  it('keeps one day and one duration, as the grammar reads them', () => {
    expect(toggleToken('4996 soudure lun', 'jeu', 'day')).toBe('4996 soudure jeu ');
    expect(toggleToken('4996 soudure jeu-ven KDU', 'mar', 'day')).toBe('4996 soudure KDU mar ');
    expect(toggleToken('4996 soudure 2j', '½j', 'duration')).toBe('4996 soudure ½j ');
    expect(toggleToken('4996 soudure 1,5j', '3j', 'duration')).toBe('4996 soudure 3j ');
    // A day typed in full is not a shortcut: it stays.
    expect(toggleToken('4996 soudure lundi', 'jeu', 'day')).toBe('4996 soudure lundi jeu ');
  });

  it('finds the word being typed and replaces it', () => {
    expect(typedWord('soudure gara')).toBe('gara');
    expect(typedWord('soudure ')).toBe('');
    expect(replaceTypedWord('soudure gara', '2026-4996')).toBe('soudure 2026-4996 ');
  });

  it('proposes dossiers by number or by client, contact and designation', () => {
    expect(dossierSuggestions('soudure 49', projects).map((p) => p.number)).toEqual(['2026-4997', '2026-4996']);
    expect(dossierSuggestions('pose ecole', projects).map((p) => p.number)).toEqual(['2026-4997']);
    expect(dossierSuggestions('pose morel', projects).map((p) => p.number)).toEqual(['2026-5000']);
    // Two letters are not enough to search the texts; a finished word proposes nothing.
    expect(dossierSuggestions('pose ga', projects)).toEqual([]);
    expect(dossierSuggestions('pose garage ', projects)).toEqual([]);
  });
});

describe('les raccourcis restent de la saisie rapide', () => {
  const context: EntryContext = {
    today: dayNumber(2026, 10, 22),
    weekStart: dayNumber(2026, 10, 19),
    calendar: new WorkCalendar({ cantons: ['GE'] }),
    resources: [
      { id: 'kdu', name: 'Kevin Dupuis', initials: 'KDU', team: 'atelier' },
      { id: 'lbi', name: 'Luca Bianchi', initials: 'LBI', team: 'montage' },
    ],
    teams: DEFAULT_TEAMS,
    taskTypes: DEFAULT_TASK_TYPES,
    findProject: () => null,
    defaultYear: 2026,
  };

  it('reads the people, the day and the duration the shortcuts write', () => {
    let text = 'soudure';
    text = toggleToken(text, 'KDU', 'person');
    text = toggleToken(text, 'LBI', 'person');
    text = toggleToken(text, 'mar', 'day');
    text = toggleToken(text, '2j', 'duration');
    const entry = parseEntry(text, context);
    expect(entry.resourceIds).toEqual(['kdu', 'lbi']);
    expect(entry.start).not.toBeNull();
    expect(formatDays(entry.estimateDays ?? 0)).toBe('2 j');
  });
});
