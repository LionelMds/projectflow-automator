import type { PlanningData, PlanningTask, Resource } from '@/lib/planning/models';
import { dayNumber } from '@/lib/planning/walltime';
import { MAX_RECENT, parseRecent, recentWidgetProps, withDirectory, withRecent } from '@/lib/recent/recent';
import {
  planningMessage,
  planningTimeline,
  planningWidgetProps,
  tasksOn,
  TIMELINE_DAYS,
  timelineNumbers,
  updatedText,
  widgetChoiceOptions,
  widgetChoices,
  widgetDayLabel,
} from '@/lib/widgets/planningWidget';
import { storable } from '@/lib/widgets/storable';
import type { WidgetDay } from '@/widgets/types';

const lm: Resource = { id: 'r-lm', name: 'Lionel', initials: 'LM', team: 'bureau' };
const jd: Resource = { id: 'r-jd', name: 'Jean', initials: 'JD', team: 'atelier' };

function task(id: string, start: string, end: string, fields: Partial<PlanningTask> = {}): PlanningTask {
  return { id, start, end, status: 'confirmed', task_type: 'soudure', assignments: { [lm.id]: {} }, ...fields };
}

function planning(tasks: PlanningTask[], absences: PlanningData['absences'] = []): PlanningData {
  return { tasks, resources: [lm, jd], absences, meta: null };
}

// Monday 9 November 2026.
const MONDAY = dayNumber(2026, 11, 9);
const READ = new Date(2026, 10, 9, 14, 32);

describe('widget « Planning »', () => {
  it('lists the person’s tasks of the day in order, worded as on the planning', () => {
    const data = planning([
      task('pm', '2026-11-09T13:00', '2026-11-09T16:30', { project_number: '2026-5012', title: 'Garde-corps' }),
      task('am', '2026-11-09T06:30', '2026-11-09T12:00', {
        project_number: '2026-4998',
        project_cache: { designation: 'Escalier hélicoïdal' },
        status: 'provisional',
        urgent: true,
      }),
      task('long', '2026-11-09T06:30', '2026-11-11T16:30', { project_number: '2026-5100', title: 'Montage', status: 'done' }),
      task('hours', '2026-11-09T08:00', '2026-11-09T10:00', { task_type: 'releve' }),
      task('other', '2026-11-09T06:30', '2026-11-09T16:30', { assignments: { [jd.id]: {} }, title: 'Pas à moi' }),
      task('gone', '2026-11-09T06:30', '2026-11-09T16:30', { deleted_at: '2026-11-08T10:00:00Z' }),
      task('tuesday', '2026-11-10T06:30', '2026-11-10T16:30'),
    ]);
    const tasks = tasksOn(data, lm, MONDAY);
    expect(tasks.map((item) => item.id)).toEqual(['am', 'long', 'hours', 'pm']);
    expect(tasks[0]).toMatchObject({
      number: '4998',
      // No title typed: the dossier's designation, as on the desktop.
      title: 'Escalier hélicoïdal',
      when: 'Matin',
      provisional: true,
      urgent: true,
      url: 'projectflow://tache?id=am',
    });
    expect(tasks[1]).toMatchObject({ title: 'Montage', when: 'Journée · 1/3', done: true });
    // Neither a title nor a dossier: the task type.
    expect(tasks[2]).toMatchObject({ number: '', title: 'Relevé de cotes', when: '08:00–10:00', color: '#009494' });
    expect(tasks[3]).toMatchObject({ number: '5012', when: 'Après-midi' });
    expect(tasksOn(data, lm, MONDAY + 2).map((item) => [item.id, item.when])).toEqual([['long', 'Journée · 3/3']]);
  });

  const settings = (initials: string, selection: string[] = []) => ({ initials, selection });
  const personOf = (day: WidgetDay | undefined, id: string) => day?.people.find((person) => person.id === id);

  it('opens on today, then the next working day, with every person of the planning', () => {
    const props = planningWidgetProps(planning([]), settings('lm'), MONDAY, READ);
    expect(props.message).toBe('');
    expect(props.choices.moi).toEqual({ title: 'LM', ids: [lm.id], empty: expect.any(String) });
    expect(props.day).toMatchObject({ kicker: "Aujourd'hui", label: 'Lun 9 nov.' });
    // Team by team, as the planning's rows: Bureau technique, then Atelier.
    expect(props.day?.people.map((person) => person.id)).toEqual([lm.id, jd.id]);
    expect(personOf(props.day, lm.id)).toEqual({ id: lm.id, who: 'LM', note: '', tasks: [] });
    expect(props.day?.url).toBe('projectflow://planning?jour=2026-11-09');
    expect(props.next).toMatchObject({ kicker: 'Demain', label: 'Mar 10 nov.' });
    expect(props.updated).toBe('14:32');
  });

  it('shows Monday on a weekend, as the planning views do', () => {
    const saturday = MONDAY + 5;
    const props = planningWidgetProps(planning([]), settings('LM'), saturday, READ);
    expect(props.day).toMatchObject({ kicker: 'Lundi', label: 'Lun 16 nov.' });
    expect(props.next).toMatchObject({ kicker: 'Mardi', label: 'Mar 17 nov.' });
    expect(props.updated).toBe('9 nov. 14:32');
  });

  it('gives each person their own tasks, absence or holiday', () => {
    const data = planning(
      [
        task('mine', '2026-11-16T06:30', '2026-11-16T12:00', { project_number: '2026-4998', title: 'Escalier' }),
        task('his', '2026-11-16T13:00', '2026-11-16T16:30', { assignments: { [jd.id]: {} }, title: 'Garde-corps' }),
        task('both', '2026-11-16T06:30', '2026-11-16T16:30', { assignments: { [lm.id]: {}, [jd.id]: {} }, title: 'Montage' }),
      ],
      [
        { id: 'a', resource_id: lm.id, reason: 'vacances', first_day: '2026-11-09', last_day: '2026-11-13' },
        { id: 'b', resource_id: lm.id, reason: 'formation', first_day: '2026-11-17', last_day: '2026-11-17', part: 'morning' },
        { id: 'c', resource_id: jd.id, reason: 'absent', first_day: '2026-11-17', last_day: '2026-11-17' },
      ],
    );
    const note = (day: number, id: string) => personOf(planningWidgetProps(data, settings('LM'), day, READ).day, id)?.note;
    expect(note(MONDAY, lm.id)).toBe('Vacances');
    expect(note(MONDAY, jd.id)).toBe('');
    expect(note(MONDAY + 8, lm.id)).toBe('Formation (matin)');
    expect(note(MONDAY + 8, jd.id)).toBe('Absent');
    expect(note(dayNumber(2026, 12, 25), jd.id)).toBe('Noël');
    const day = planningWidgetProps(data, settings('LM'), MONDAY + 7, READ).day;
    // Same start: the task without a dossier number first, as on the planning.
    expect(personOf(day, lm.id)?.tasks.map((item) => item.id)).toEqual(['both', 'mine']);
    expect(personOf(day, jd.id)?.tasks.map((item) => item.id)).toEqual(['both', 'his']);
    const school = planning([]);
    school.resources = [{ ...lm, recurring_off: [{ weekday: 1, reason: 'ecole' }] }, jd];
    expect(personOf(planningWidgetProps(school, settings('LM'), MONDAY, READ).next, lm.id)?.note).toBe('École professionnelle');
  });

  it('offers me, my selection, everybody, each team and each person', () => {
    const data = planning([]);
    data.resources = [
      lm,
      jd,
      { id: 'r-yc', name: 'Yves', initials: 'ych', team: 'atelier', order: -1 },
      { id: 'r-old', name: 'Parti', initials: 'PA', team: 'atelier', active: false },
      { id: 'r-gone', name: 'Supprimé', initials: 'SU', team: 'atelier', deleted_at: '2026-11-01T00:00:00Z' },
      { id: 'r-st', name: 'Sous-traitant SA', team: 'externe' },
    ];
    const choices = widgetChoices(data, settings('lm', ['r-st', jd.id, 'r-old', 'nobody']));
    expect(Object.keys(choices)).toEqual([
      'moi',
      'selection',
      'tous',
      'equipe:bureau',
      'equipe:atelier',
      'equipe:externe',
      `personne:${lm.id}`,
      'personne:r-yc',
      `personne:${jd.id}`,
      'personne:r-st',
    ]);
    // Planning order, whatever the order of the checks; people no longer on the planning left out.
    expect(choices.selection.ids).toEqual([jd.id, 'r-st']);
    expect(choices.tous.ids).toEqual([lm.id, 'r-yc', jd.id, 'r-st']);
    expect(choices['equipe:atelier']).toEqual({ title: 'Atelier', ids: ['r-yc', jd.id], empty: '' });
    expect(choices['personne:r-st']).toEqual({ title: 'Sous-traitant SA', ids: ['r-st'], empty: '' });

    expect(widgetChoiceOptions(data, settings('lm', [jd.id]))).toEqual([
      { name: 'Moi', value: 'moi', subtitle: 'LM' },
      { name: 'Ma sélection', value: 'selection', subtitle: 'JD' },
      { name: 'Tout le monde', value: 'tous' },
      { name: 'Bureau technique', value: 'equipe:bureau', subtitle: 'LM' },
      { name: 'Atelier', value: 'equipe:atelier', subtitle: 'YCH, JD' },
      { name: 'externe', value: 'equipe:externe', subtitle: 'Sous-traitant SA' },
      { name: 'Lionel', value: `personne:${lm.id}`, subtitle: 'LM' },
      { name: 'Yves', value: 'personne:r-yc', subtitle: 'YCH' },
      { name: 'Jean', value: `personne:${jd.id}`, subtitle: 'JD' },
      { name: 'Sous-traitant SA', value: 'personne:r-st' },
    ]);
  });

  it('says what is missing when a choice has nobody', () => {
    expect(widgetChoices(planning([]), settings('')).moi).toEqual({ title: '', ids: [], empty: 'Renseignez vos initiales dans Paramètres.' });
    expect(widgetChoices(planning([]), settings('xy')).moi).toMatchObject({ ids: [], empty: '« XY » n\'est pas dans le planning.' });
    expect(widgetChoices(planning([]), settings('LM')).selection).toMatchObject({ ids: [], empty: expect.stringContaining('Paramètres') });
    expect(widgetChoiceOptions(planning([]), settings(''))[0]).toEqual({
      name: 'Moi',
      value: 'moi',
      subtitle: 'Initiales à renseigner dans Paramètres',
    });
  });

  it('never sends null: iOS would close the app', () => {
    const hasNull = (value: unknown): boolean =>
      value === null || (typeof value === 'object' && Object.values(value as object).some(hasNull));
    const data = planning([task('a', '2026-11-09T06:30', '2026-11-09T12:00', { project_number: '2026-4998' })]);
    for (const initials of ['LM', 'JD', 'XY', '']) {
      expect(hasNull(planningTimeline(data, settings(initials, [jd.id]), READ, READ).map((entry) => entry.props))).toBe(false);
      expect(hasNull(widgetChoiceOptions(data, settings(initials)))).toBe(false);
    }
    expect(hasNull(planningMessage('Connectez-vous à Microsoft dans ProjectFlow.'))).toBe(false);
    expect(hasNull(recentWidgetProps([]))).toBe(false);
  });

  it('leaves null out of what reaches the widgets', () => {
    expect(storable({ a: null, b: [null, { c: null, d: 'x' }], e: 0, f: '' })).toEqual({ b: [{ d: 'x' }], e: 0, f: '' });
  });

  it('writes two weeks ahead, each entry from the phone’s midnight', () => {
    const now = new Date(2026, 10, 8, 20, 15);
    const data = planning([task('late', '2026-11-20T06:30', '2026-11-20T16:30', { title: 'Vendredi' })]);
    const entries = planningTimeline(data, settings('LM'), READ, now);
    expect(entries).toHaveLength(TIMELINE_DAYS);
    expect(entries[0].date).toBe(now);
    expect(entries[1].date).toEqual(new Date(2026, 10, 9));
    // Sunday 8 shows tomorrow, Monday 9; from Monday 9 on, each day itself.
    expect(entries.slice(0, 3).map((entry) => [entry.props.day?.kicker, entry.props.day?.label])).toEqual([
      ['Demain', 'Lun 9 nov.'],
      ["Aujourd'hui", 'Lun 9 nov.'],
      ["Aujourd'hui", 'Mar 10 nov.'],
    ]);
    // Read once for the whole timeline, the same as one day's content.
    expect(personOf(entries[12].props.day, lm.id)?.tasks.map((item) => item.id)).toEqual(['late']);
    expect(entries[12].props).toEqual(planningWidgetProps(data, settings('LM'), MONDAY + 11, READ));
  });

  it('wants the designations of the dossiers it may show', () => {
    const data = planning([
      task('soon', '2026-11-10T06:30', '2026-11-10T12:00', { project_number: '2026-5012' }),
      task('later', '2026-12-20T06:30', '2026-12-20T12:00', { project_number: '2026-6000' }),
      task('before', '2026-10-01T06:30', '2026-10-01T12:00', { project_number: '2026-4000' }),
      task('twice', '2026-11-12T06:30', '2026-11-12T12:00', { project_number: '2026-5012', assignments: { [jd.id]: {} } }),
    ]);
    expect(timelineNumbers(data, READ)).toEqual(['2026-5012']);
  });

  it('words days and times as the widget shows them', () => {
    expect(widgetDayLabel(dayNumber(2026, 12, 1))).toBe('Mar 1 déc.');
    expect(updatedText(new Date(2026, 10, 9, 7, 5), MONDAY)).toBe('07:05');
  });
});

describe('dossiers récents', () => {
  const a = { number: '2026-5245', designation: 'Escalier', client: 'Garage du Lac' };
  const b = { number: '2026-5012', designation: 'Garde-corps', client: 'Commune' };

  it('puts the last opened first, once, keeping what it already knew', () => {
    let list = withRecent([], a);
    list = withRecent(list, b);
    list = withRecent(list, { number: '2026-5245', designation: '', client: '' });
    expect(list).toEqual([a, b]);
    const many = Array.from({ length: MAX_RECENT + 3 }, (_, index) => ({ number: `2026-${5000 + index}`, designation: '', client: '' }));
    const full = many.reduce((acc, item) => withRecent(acc, item), [] as typeof list);
    expect(full).toHaveLength(MAX_RECENT);
    expect(full[0].number).toBe(`2026-${5000 + MAX_RECENT + 2}`);
    expect(withRecent(list, { number: ' ', designation: 'x', client: '' })).toEqual(list);
  });

  it('reads what was stored, and nothing from a damaged value', () => {
    expect(parseRecent(JSON.stringify([a, { number: 5 }, { number: '2026-1', designation: 3 }]))).toEqual([
      a,
      { number: '2026-1', designation: '', client: '' },
    ]);
    expect(parseRecent('{oops')).toEqual([]);
    expect(parseRecent(null)).toEqual([]);
  });

  it('takes the répertoire’s current details', () => {
    const projects = new Map([['2026-5012', { number: '2026-5012', designation: 'Garde-corps inox', client: '' }]]);
    expect(withDirectory([a, b], projects)).toEqual([a, { ...b, designation: 'Garde-corps inox' }]);
  });

  it('links each dossier to its folder in the app', () => {
    expect(recentWidgetProps([a])).toEqual({
      message: '',
      items: [{ ...a, url: 'projectflow://dossier?number=2026-5245' }],
      url: 'projectflow://explorer',
    });
    expect(recentWidgetProps([]).message).not.toBe('');
  });
});
