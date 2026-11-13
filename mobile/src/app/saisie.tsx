import { useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef, useState, type ComponentRef } from 'react';
import {
  Keyboard,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type HostInstance,
  type KeyboardEvent,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Notice } from '@/components/Notice';
import { Segmented } from '@/components/Segmented';
import { Caps, DossierNumber, Kicker, Muted } from '@/components/Text';
import { WhenPicker } from '@/components/WhenPicker';
import { close } from '@/lib/navigation';
import type { WorkCalendar } from '@/lib/planning/calendar';
import { calendarResolver, contextFor, createAbsence, createTask } from '@/lib/planning/commands';
import type { Change } from '@/lib/planning/documents';
import { dossierCache } from '@/lib/planning/dossiers';
import { describeDay, describeInstant } from '@/lib/planning/display';
import {
  dossierSuggestions,
  hasToken,
  replaceTypedWord,
  toggleToken,
  type ShortcutKind,
} from '@/lib/planning/entryShortcuts';
import { instantDay, minuteOfDay } from '@/lib/planning/instant';
import { ABSENCE_LABELS, taskTypeOf, teamsOf, taskTypesOf, type PlanningData } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import {
  DAY_ABBREVIATIONS,
  defaultTaskTypeKey,
  foldText,
  parseEntry,
  type EntryContext,
  type ParsedEntry,
  type Token,
} from '@/lib/planning/quickEntry';
import { UNASSIGNED } from '@/lib/planning/week';
import { dateParts, mondayOf, parseDate, todayNumber, weekday } from '@/lib/planning/walltime';
import { entryWithWhen, firstWhen, movedStart, whenRefused, type When } from '@/lib/planning/when';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, touch } from '@/theme/tokens';

const NO_PERSON_ABSENCE = 'Absence sans personne : indiquez les initiales (ex. /vac KDU 26-30.10).';
const UNREADABLE_ENTRY = 'Saisie non comprise.';
/**
 * What a task should say, in the order the example gives it: « 5299 rdv discussion 16.11
 * 8h30 1h lmds » (dossier, type, name, date, hour and length, initials). The
 * entry reads them in any order; these only guide the typing, and each can be passed over
 * (`none`: what the task is then). The type is the one step usually said by a word of the
 * title (« pose », « rdv ») or chosen by touch, not typed for itself.
 */
type Step = 'dossier' | 'title' | 'type' | 'when' | 'who';
const STEPS: { key: Step; label: string; none: string; ask: string }[] = [
  { key: 'dossier', label: 'Dossier', none: 'Sans dossier', ask: 'le n° de dossier' },
  { key: 'type', label: 'Type', none: 'Type par défaut', ask: 'le type' },
  { key: 'title', label: 'Titre', none: 'Titre du dossier', ask: 'le nom de la tâche' },
  { key: 'when', label: 'Quand', none: 'Sans date', ask: 'la date, l’heure et la durée' },
  { key: 'who', label: 'Qui', none: 'Non affecté', ask: 'les initiales' },
];
/** What « Quand » is made of, in the order it is typed. */
type WhenPart = 'date' | 'time' | 'length';
const WHEN_PARTS: Record<WhenPart, string> = { date: 'la date', time: 'l’heure', length: 'la durée' };
/** Past this length the field scrolls its text: the greyed example would no longer sit after it. */
const GHOST_MAX = 29;
const GHOST_COLOR = '#7a7f85';

/** A new task is confirmed or provisional; « Terminé » comes later, on its sheet. */
const STATUS_OPTIONS: { value: 'confirmed' | 'provisional'; label: string }[] = [
  { value: 'confirmed', label: 'Confirmé' },
  { value: 'provisional', label: 'Provisoire' },
];

function capitalized(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

function shortDate(day: number): string {
  const { date, month } = dateParts(day);
  return `${String(date).padStart(2, '0')}.${String(month).padStart(2, '0')}`;
}

/** « 8h30 », « 9h »: an hour as it is typed. */
function typedHour(minute: number): string {
  return `${Math.floor(minute / 60)}h${minute % 60 ? String(minute % 60).padStart(2, '0') : ''}`;
}

/** « 1h », « 1h30 », « 30min »: a length as it is typed. */
function typedLength(minutes: number): string {
  if (minutes < 60) return `${minutes}min`;
  return `${Math.floor(minutes / 60)}h${minutes % 60 ? String(minutes % 60).padStart(2, '0') : ''}`;
}

/**
 * Ways of typing `when`, the nearest to the user's own first: its day, its hour and its
 * length of work (« 16.11 8h30 1h », « 16.11 8h30 1,5j »), which also says a span running
 * over several days; then ones the entry cannot mistake (« 16.11 8h30-9h30 »,
 * « 16.11-18.11 »). Which of them reads back as `when` is for the caller to check: hours,
 * lengths and breaks are the grammar's.
 */
function whenWords(when: When, calendar: WorkCalendar): string[] {
  const first = instantDay(when.start);
  const last = instantDay(when.end - 1);
  const from = `${shortDate(first)} ${typedHour(minuteOfDay(when.start))}`;
  const days = calendar.wholeDays(when.start, when.end);
  const worked = calendar.workBetween(when.start, when.end);
  return [
    ...(days !== null ? [`${from} ${String(days).replace('.', ',')}j`] : []),
    ...(worked > 0 ? [`${from} ${typedLength(worked)}`] : []),
    ...(first === last ? [`${from}-${typedHour(minuteOfDay(when.end))}`] : [`${shortDate(first)}-${shortDate(last)}`]),
  ];
}

const WHEN_KINDS: readonly string[] = ['day', 'date', 'week', 'part', 'time', 'duration', 'night'];

/**
 * `text` with `words` where it said when: in place of the first day, hour or length it
 * gave (the others removed), else before the people, as in « 5299 rdv 16.11 8h30 1h lmds ».
 */
function withWhen(text: string, tokens: readonly Token[], words: string): string {
  const said = tokens.filter((token) => WHEN_KINDS.includes(token.kind)).sort((a, b) => a.start - b.start);
  let rest = text;
  for (const token of [...said].reverse()) rest = rest.slice(0, token.start) + rest.slice(token.end);
  const who = tokens.filter((token) => token.kind === 'person' || token.kind === 'team').sort((a, b) => a.start - b.start)[0];
  const at = Math.min(said.length ? said[0].start : who ? who.start : rest.length, rest.length);
  return `${[rest.slice(0, at).trim(), words, rest.slice(at).trim()].filter(Boolean).join(' ').replace(/\s+/g, ' ')} `;
}

/**
 * How far the keyboard covers the screen above its bottom inset. The screen is a sheet: it
 * does not start at the top of the display, which a `KeyboardAvoidingView` does not know
 * (and measuring a view « in the window » answers within the sheet too), but it does end at
 * the bottom of the display, where the keyboard's own height says what it hides.
 */
function useKeyboardLift(inset: number): number {
  const [height, setHeight] = useState(0);
  useEffect(() => {
    // Android resizes the window itself; a browser has no keyboard of its own.
    if (Platform.OS !== 'ios') return undefined;
    const rise = (event: KeyboardEvent) => setHeight(event.endCoordinates.height);
    const fall = () => setHeight(0);
    const subscriptions = [Keyboard.addListener('keyboardWillShow', rise), Keyboard.addListener('keyboardWillHide', fall)];
    return () => subscriptions.forEach((subscription) => subscription.remove());
  }, []);
  return Math.max(0, height - inset);
}

export default function SaisieScreen() {
  const params = useLocalSearchParams<{ row?: string; start?: string; end?: string; anchor?: string; text?: string }>();
  const { data, apply, author, editBlocker, waiting, error: loadError } = useLoadedPlanning();
  const { settings } = useSettings();
  const { projects, wantProjects } = useRepertoire();
  // « Nouvelle tâche » of a dossier starts with its number.
  const [text, setText] = useState(() => params.text ?? '');
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  // Steps passed over by touch; a step not set here follows the text (see `stepOf`).
  const [skips, setSkips] = useState<Partial<Record<Step, boolean>>>({});
  const [busy, setBusy] = useState(false);

  // The dossiers come from the répertoire, as on the desktop: this year's, and the year of
  // a dossier typed from another one.
  const year = dateParts(todayNumber()).year;
  const source = settings.demo || Boolean(settings.repertoire);
  useEffect(() => {
    if (source) void wantProjects([`${year}-`]);
  }, [source, wantProjects, year]);

  const cellRow = params.row && params.row !== UNASSIGNED ? params.row : null;
  const cellStart = params.start ? Number(params.start) : null;
  const cellEnd = params.end ? Number(params.end) : null;

  const context = useMemo<EntryContext | null>(() => {
    if (!data) return null;
    const calendarFor = calendarResolver(data);
    const today = todayNumber();
    const anchor = params.anchor ? Number(params.anchor) : today;
    return {
      today,
      weekStart: mondayOf(Math.abs(anchor - today) < 5 ? today : anchor),
      calendar: calendarFor(null),
      resources: data.resources.filter((resource) => !resource.deleted_at && resource.active !== false),
      teams: teamsOf(data.meta),
      taskTypes: taskTypesOf(data.meta),
      findProject: (number) => projects.get(number) ?? null,
      defaultYear: dateParts(anchor).year,
      cellResourceIds: cellRow ? [cellRow] : [],
      cellStart: cellStart ?? null,
      cellEnd: cellEnd ?? null,
      calendarFor,
    };
  }, [data, projects, cellRow, cellStart, cellEnd, params.anchor]);

  const entry = useMemo<ParsedEntry | null>(() => {
    if (!context || !text.trim()) return null;
    try {
      return parseEntry(text, context);
    } catch {
      return null;
    }
  }, [context, text]);

  // « Début » and « Fin » turned on the wheels. The text wins again as soon as it changes the
  // day or the hours it gives (the last one who spoke is right).
  const today = todayNumber();
  const [picked, setPicked] = useState<{ when: When | null; basis: string } | null>(null);
  const basis = `${entry?.start ?? ''}|${entry?.end ?? ''}`;
  if (picked && picked.basis !== basis) setPicked(null);
  const override = picked && picked.basis === basis ? picked : null;
  const calendar = context ? (context.calendarFor?.(entry?.resourceIds[0] ?? null) ?? context.calendar) : null;
  // Shown at once, as in the Calendar: the span typed, else the planning cell touched or the
  // next working morning. « Sans date » sends the task to « À planifier ».
  const typedWhen = entry && entry.start !== null && entry.end !== null ? { start: entry.start, end: entry.end } : null;
  const offered = calendar
    ? firstWhen(
        calendar,
        today,
        { minutes: entry?.estimateMinutes, days: entry?.estimateDays },
        cellStart !== null ? { start: cellStart, end: cellEnd } : null,
      )
    : null;
  // The span replacing the text's (null: « Sans date »); undefined keeps the span typed.
  const applied: When | null | undefined = override ? override.when : typedWhen ? undefined : offered;
  const shownEntry = entry && calendar && applied !== undefined ? entryWithWhen(entry, calendar, applied) : entry;
  const when: When | null = applied === undefined ? typedWhen : applied;
  // An absence keeps its days, a night task its typed hours: no wheels for them.
  const timed = !entry || (entry.destination !== 'absence' && !entry.offHours);
  const refused = Boolean(applied && calendar && whenRefused(calendar, applied));

  const typedNumber = entry?.projectNumber ?? '';
  useEffect(() => {
    if (source && typedNumber && !projects.has(typedNumber)) void wantProjects([typedNumber]);
  }, [source, typedNumber, projects, wantProjects]);

  const names = useMemo(
    () => new Map<string, string>((data?.resources ?? []).map((resource) => [resource.id, resource.initials || resource.name])),
    [data],
  );
  const label = (ids: readonly string[]) => ids.map((id) => names.get(id) ?? id).join(', ');

  const create = async (keep: boolean) => {
    if (!data || !context || !entry) return;
    setError('');
    setDone('');
    // The task always takes its dossier's designation (`_title`): a dossier of a year not read
    // yet is read first, and the entry is read again with it.
    let parsed = entry;
    if (parsed.projectNumber && !projects.has(parsed.projectNumber)) {
      const directory = await wantProjects([parsed.projectNumber]);
      if (directory.has(parsed.projectNumber)) {
        parsed = parseEntry(text, { ...context, findProject: (number) => directory.get(number) ?? null });
      }
    }
    await createFrom(calendar && applied !== undefined ? entryWithWhen(parsed, calendar, applied) : parsed, keep);
  };

  const createFrom = async (entry: ParsedEntry, keep: boolean) => {
    if (!data) return;
    if (entry.destination === 'invalid') {
      setError(entry.warnings[0] ?? UNREADABLE_ENTRY);
      return;
    }
    const ctx = contextFor(data, author);
    let changes: Change[];
    let undoLabel: string;
    let message: string;
    try {
      if (entry.destination === 'absence') {
        const draft = entry.absence;
        if (!draft || !draft.resourceIds.length) {
          setError(NO_PERSON_ABSENCE);
          return;
        }
        changes = draft.resourceIds.flatMap((resourceId) =>
          createAbsence(ctx, {
            resourceId,
            firstDay: draft.firstDay,
            lastDay: draft.lastDay,
            reason: draft.reason,
            part: draft.part,
            note: draft.note,
          }),
        );
        let days = shortDate(draft.firstDay);
        if (draft.lastDay !== draft.firstDay) days = `${days} → ${shortDate(draft.lastDay)}`;
        undoLabel = `Absence ${ABSENCE_LABELS[draft.reason] ?? draft.reason} ${label(draft.resourceIds)} ${days}`;
        message = `Absence enregistrée : ${ABSENCE_LABELS[draft.reason] ?? draft.reason} ${label(draft.resourceIds)} ${days}`;
      } else {
        const project = entry.projectNumber ? (entry.project ?? projects.get(entry.projectNumber) ?? null) : null;
        const cache = project ? dossierCache(project) : undefined;
        // Saved as the preview shows it: « Pose garde-corps ».
        const title = capitalized(entry.title.trim());
        changes = createTask(ctx, {
          title,
          taskType: entry.taskType,
          projectNumber: entry.projectNumber,
          projectCache: cache,
          start: entry.start,
          end: entry.end,
          resourceIds: entry.resourceIds,
          estimateMinutes: entry.estimateMinutes,
          estimateDays: entry.estimateDays,
          status: entry.provisional ? 'provisional' : 'confirmed',
          deadline: entry.deadline,
          urgent: entry.urgent,
          notes: entry.notes,
          origin: 'quick_entry',
          offHours: entry.offHours,
        });
        const shown = `${entry.projectNumber} ${title || cache?.designation || ''}`.trim() || 'une tâche';
        undoLabel = `Créé : ${shown}`;
        message = entry.start === null ? `Ajouté à « À planifier » : ${shown}` : `Planifié : ${shown}`;
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : UNREADABLE_ENTRY);
      return;
    }
    setBusy(true);
    try {
      await apply(changes, undoLabel);
      if (keep) {
        setText('');
        setSkips({});
        setDone(message);
      } else {
        close('/planning');
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Enregistrement impossible.');
    } finally {
      setBusy(false);
    }
  };

  const type = entry && data ? taskTypeOf(data.meta, entry.taskType) : null;
  const types = data ? taskTypesOf(data.meta) : [];
  const suggestions = entry?.projectNumber ? [] : dossierSuggestions(text, projects.values());
  const toggle = (token: string, kind: ShortcutKind) => {
    setText((current) => toggleToken(current, token, kind));
    setError('');
  };
  const destination = shownEntry ? destinationText(shownEntry, label) : '';
  // The name of the task: what was typed, else its dossier's designation, as on the desktop.
  // An entry without a name of its own is given its dossier's designation: that is not a name typed.
  const designation = (shownEntry?.project?.designation ?? '').trim();
  const givenTitle = shownEntry ? capitalized(shownEntry.title.trim()) : '';
  const subTitle = givenTitle.toLowerCase() === designation.toLowerCase() ? '' : givenTitle;
  const absence = shownEntry?.destination === 'absence';
  // « Qui », as the desktop's « Détail » lays it out: everyone, team by team, as initials.
  const crews = useMemo(() => {
    if (!data) return [];
    const people = data.resources.filter((resource) => !resource.deleted_at && resource.active !== false && resource.initials);
    const teams = [...teamsOf(data.meta)].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const keys = teams.map((team) => team.key);
    for (const person of people) if (!keys.includes(person.team || 'atelier')) keys.push(person.team || 'atelier');
    return keys
      .map((key) => ({
        key,
        name: teams.find((team) => team.key === key)?.name ?? key,
        people: people
          .filter((person) => (person.team || 'atelier') === key)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase())),
      }))
      .filter((crew) => crew.people.length);
  }, [data]);
  const chosen = shownEntry ? (absence ? (shownEntry.absence?.resourceIds ?? []) : shownEntry.resourceIds) : [];
  // Who is away over the span shown: still selectable, as on the desktop, but marked.
  const away = data && when && !absence ? awayOver(data, when) : new Map<string, string>();
  /** A type chosen by touch is written as its command (« /montage »), which the text can overrule. */
  const chooseType = (key: string) => {
    const command = `/${key}`;
    if (entry?.taskType === key && !hasToken(text, command)) return;
    toggle(command, 'type');
  };

  // The guide: what is written, what is left to write, what was passed over. An absence
  // (« /vac LBI lun ») has its own words and is not guided.
  const guided = !absence && !text.trimStart().startsWith('/');
  // An entry begun with a word is taken to have no dossier (a meeting, an errand): the
  // number can still be typed later, or the step taken back by touching it.
  const beginsWithWord = /^[^\d\s/]/.test(text.trimStart());
  const whenPassed = override !== null && override.when === null;
  // The type is said when a command gives it (« /montage », or a chip), a word of the title
  // means it (« rdv »), or the people chosen bring theirs; otherwise it is only the default.
  const titleWords = entry ? foldText(entry.title).split(/[^a-z0-9]+/) : [];
  const typeSaid = Boolean(
    entry &&
      type &&
      (entry.tokens.some((token) => token.kind === 'type' && !token.ambiguous) ||
        entry.taskType !== defaultTaskTypeKey(types) ||
        [...(type.keywords ?? []), foldText(type.key)].some((keyword) => titleWords.includes(keyword) || titleWords.includes(`${keyword}s`))),
  );
  // « Quand » is written in three goes, as in « 16.11 8h30 1h »: the date, the hour, the
  // length. It is not done at the first of them, or the screen would move on before the
  // hour could be typed. A half-day (« matin ») or a night says hour and length at once, a
  // span (« 8h-10h », « 16-18.11 ») the length.
  const read = (entry?.tokens ?? []).filter((token) => !token.ambiguous);
  const reads = (...kinds: string[]) => read.some((token) => kinds.includes(token.kind));
  const halfDay = reads('part', 'night');
  const spans = read.some(
    (token) => (token.kind === 'time' && token.value.includes('-')) || ((token.kind === 'date' || token.kind === 'week') && token.value.includes('/')),
  );
  const whenLeft: WhenPart[] = [
    ...(reads('day', 'date', 'week') ? [] : (['date'] as const)),
    ...(halfDay || reads('time') ? [] : (['time'] as const)),
    ...(halfDay || spans || reads('duration') ? [] : (['length'] as const)),
  ];
  const whenBegun = whenLeft.length < 3;
  // The word that says the type (« rdv ») is not yet a name for the task: after it, the
  // name is still asked for (« rdv discussion »).
  const typeWords = type ? [...(type.keywords ?? []), type.key].map(foldText) : [];
  const ownTitle = foldText(subTitle)
    .split(/[^a-z0-9]+/)
    .filter(Boolean)
    .some((word) => !typeWords.includes(word) && !typeWords.includes(word.replace(/s$/, '')));
  const stepOf = (step: Step): 'done' | 'passed' | 'todo' => {
    if (step === 'dossier') return entry?.projectNumber ? 'done' : (skips.dossier ?? beginsWithWord) ? 'passed' : 'todo';
    if (step === 'title') return ownTitle ? 'done' : skips.title ? 'passed' : 'todo';
    if (step === 'type') return typeSaid ? 'done' : skips.type ? 'passed' : 'todo';
    if (step === 'when') {
      if (whenPassed) return 'passed';
      // Turned on the wheels, given by the cell touched, kept as typed, or written in full.
      return override?.when || cellStart !== null || skips.when || !whenLeft.length ? 'done' : 'todo';
    }
    return chosen.length || cellRow ? 'done' : skips.who ? 'passed' : 'todo';
  };
  /** What the task is without that step: a title passed over is the dossier's, when there is a dossier. */
  const noneOf = (step: (typeof STEPS)[number]) =>
    step.key === 'title' && subTitle
      ? 'Garder ainsi'
      : step.key === 'title' && !entry?.projectNumber
        ? 'Sans titre'
        : step.key === 'when' && whenBegun
          ? 'Garder ainsi'
          : step.none;
  /**
   * Pass a step over, or take it back. « Quand » passed over before anything of it is typed
   * is « Sans date », the choice its block already offers; once begun, it is kept as typed
   * (a day without an hour takes the day's first hour, as the entry reads it).
   */
  const passStep = (step: Step) => {
    setError('');
    if (step !== 'when') setSkips((previous) => ({ ...previous, [step]: stepOf(step) !== 'passed' }));
    else if (whenPassed) setPicked(null);
    else if (whenBegun) setSkips((previous) => ({ ...previous, when: true }));
    else setPicked({ when: null, basis });
  };
  // The examples are written as one types on a phone (« 5299 rdv discussion 16.11 8h30 1h lmds »)
  // and are real ones where there are: the latest dossier, a word that means a type, the
  // next working day, one's own initials.
  const latestDossier = useMemo(() => {
    let latest = '';
    for (const number of projects.keys()) if (number.startsWith(`${year}-`) && number.split('-').length === 2 && number > latest) latest = number;
    return latest.split('-')[1] ?? '';
  }, [projects, year]);
  const everyone = crews.flatMap((crew) => crew.people);
  const mine = everyone.find((person) => settings.initials && (person.initials ?? '').toUpperCase() === settings.initials.toUpperCase());
  // A word means a type by being one of its keywords, or its key.
  const typeWord = types.some((kind) => (kind.keywords ?? []).includes('rdv') || foldText(kind.key) === 'rdv')
    ? 'rdv'
    : (types.find((kind) => kind.keywords?.length)?.keywords?.[0] ?? '');
  const examples: Record<Step, string> = {
    dossier: latestDossier || '5252',
    type: typeWord,
    title: 'discussion',
    // Only what is left of it: « 8h30 1h » once the date is typed.
    when: whenLeft.map((part) => (part === 'date' ? shortDate(offered ? instantDay(offered.start) : today) : part === 'time' ? '8h30' : '1h')).join(' '),
    who: ((mine ?? everyone[0])?.initials || 'LM').toLowerCase(),
  };
  const whenAsk = whenLeft.map((part) => WHEN_PARTS[part]).join(', ').replace(/, ([^,]*)$/, ' et $1');
  const todo = guided ? STEPS.filter((step) => stepOf(step.key) === 'todo') : [];
  const next = todo[0] ?? null;
  // In the field, after what is typed: an example of each thing left to write, in grey.
  const ghost = text.length <= GHOST_MAX ? todo.map((step) => examples[step.key]).filter(Boolean).join(' ') : '';

  // The field stays on top; under it, the screen moves by itself to what is to be given
  // next, so that it shows above the keyboard: a step written, the following one comes up.
  const lift = useKeyboardLift(useSafeAreaInsets().bottom);
  const scroller = useRef<ComponentRef<typeof ScrollView>>(null);
  const scrollBox = useRef<HostInstance>(null);
  const scrollY = useRef(0);
  // The block of each step, measured when the screen moves to it: a block is pushed down
  // by those that appear over it, which its first layout knows nothing of.
  const blocks = useRef<Partial<Record<Step, HostInstance | null>>>({});
  const hold = (step: Step, node: HostInstance | null) => {
    blocks.current[step] = node;
  };
  /**
   * What the wheels were turned to, written in the field as it would be typed, so that
   * the field always says what will be created. Each way of writing it is read back
   * first: false when none gives that span (hours the entry rounds, a span over several
   * days with hours of its own).
   */
  const writeWhen = (target: When): boolean => {
    if (!context || !calendar) return false;
    for (const words of whenWords(target, calendar)) {
      const written = withWhen(text, entry?.tokens ?? [], words);
      try {
        const readBack = parseEntry(written, context);
        if (readBack.start !== target.start || readBack.end !== target.end) continue;
      } catch {
        continue;
      }
      setText(written);
      setPicked(null);
      setError('');
      return true;
    }
    return false;
  };
  const nextKey = next?.key ?? null;
  // While « Quand » is being written its wheels are open and turn with the typing: those
  // of the start for the date and the hour, those of the end for the length.
  const reveal = nextKey !== 'when' ? null : whenLeft.includes('date') || whenLeft.includes('time') ? 'start' : 'end';
  const turned = useRef(false);
  const opened = useRef(false);
  useEffect(() => {
    // Not on opening: the screen starts at its top.
    if (!opened.current) {
      opened.current = true;
      return undefined;
    }
    // Nor when a wheel was turned: the step is then done, but the hand is still on the wheels.
    if (turned.current) {
      turned.current = false;
      return undefined;
    }
    // Once the blocks have taken their new places.
    const timer = setTimeout(() => {
      if (nextKey === null) scroller.current?.scrollToEnd({ animated: true });
      else if (nextKey === 'dossier') scroller.current?.scrollTo({ y: 0, animated: true });
      else {
        const block = blocks.current[nextKey];
        if (!block) return;
        scrollBox.current?.measureInWindow((_x, top) =>
          block.measureInWindow((_left, blockTop) =>
            scroller.current?.scrollTo({ y: Math.max(0, scrollY.current + blockTop - top - 6), animated: true }),
          ),
        );
      }
    }, 80);
    return () => clearTimeout(timer);
  }, [nextKey]);

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/planning')} style={styles.barButton}>
          <Text style={styles.barText}>Annuler</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Saisie rapide</Kicker>
        <View style={styles.barButton} />
      </View>
      <View style={[styles.flex, { paddingBottom: lift }]}>
        <View style={styles.top}>
          {cellStart !== null ? (
            <Muted>
              Sur la ligne {cellRow ? names.get(cellRow) : 'Non affecté'} · {describeInstant(cellStart)}
              {minuteOfDay(cellStart) >= 12 * 60 ? ' (après-midi)' : ''}
            </Muted>
          ) : null}
          <View>
            <TextInput
              accessibilityLabel="Saisie rapide"
              accessibilityHint={next ? `Reste à écrire : ${todo.map((step) => step.ask).join(', ')}` : undefined}
              value={text}
              onChangeText={(value) => {
                turned.current = false;
                setText(value);
                setError('');
              }}
              autoFocus
              autoCapitalize="none"
              autoCorrect={false}
              returnKeyType="done"
              onSubmitEditing={() => void create(false)}
              style={styles.input}
            />
            {/* Drawn over the field in its own type: what is typed, unseen, then the example. */}
            {ghost ? (
              <View pointerEvents="none" style={styles.ghost}>
                <Text style={styles.ghostText} numberOfLines={1}>
                  {text}
                  <Text style={styles.ghostHint}>
                    {text && !/\s$/.test(text) ? ' ' : ''}
                    {ghost}
                  </Text>
                </Text>
              </View>
            ) : null}
          </View>

          {guided ? (
            <View style={styles.steps}>
              <View style={styles.wrap}>
                {STEPS.map((step) => {
                  const state = stepOf(step.key);
                  return (
                    <Pressable
                      key={step.key}
                      accessibilityRole="button"
                      accessibilityLabel={
                        state === 'done' ? `${step.label} : écrit` : state === 'passed' ? `${noneOf(step)}. Toucher pour le demander` : `${step.label} : à écrire. Toucher pour passer`
                      }
                      disabled={state === 'done'}
                      onPress={() => passStep(step.key)}
                      style={[styles.step, state === 'done' && styles.stepDone, state === 'passed' && styles.stepPassed, step === next && styles.stepNext]}
                    >
                      <Text style={[styles.stepText, state === 'done' && styles.stepTextDone, state === 'passed' && styles.stepTextPassed, step === next && styles.stepTextNext]}>
                        {state === 'done' ? `✓ ${step.label}` : state === 'passed' ? noneOf(step) : step.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
              {next ? (
                <View style={styles.stepHint}>
                  <Muted style={[styles.small, styles.flex]}>
                    {next.key === 'type'
                      ? `À choisir : le type, ci-dessous${typeWord ? ` ou par un mot (« ${typeWord} »)` : ''}.`
                      : `À écrire : ${next.key === 'when' ? whenAsk : next.ask}, par ex. « ${examples[next.key]} ».`}
                  </Muted>
                  <Pressable accessibilityRole="button" accessibilityLabel={`Passer : ${noneOf(next)}`} onPress={() => passStep(next.key)} style={styles.whenLink}>
                    <Text style={styles.whenLinkText}>Passer</Text>
                  </Pressable>
                </View>
              ) : null}
            </View>
          ) : null}
        </View>

        <View ref={scrollBox} style={styles.flex}>
        <ScrollView
          ref={scroller}
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          onScroll={(event) => {
            scrollY.current = event.nativeEvent.contentOffset.y;
          }}
          scrollEventThrottle={16}
        >
          {suggestions.length ? (
            <View style={styles.suggestions}>
              <Caps>Dossiers du répertoire</Caps>
              {suggestions.map((project) => (
                <Pressable
                  key={project.number}
                  accessibilityRole="button"
                  accessibilityLabel={`Choisir le dossier ${project.number}`}
                  onPress={() => setText((current) => replaceTypedWord(current, project.number))}
                  style={({ pressed }) => [styles.suggestion, pressed && styles.suggestionPressed]}
                >
                  <DossierNumber style={styles.suggestionNumber}>{project.number}</DossierNumber>
                  <Text style={styles.suggestionText} numberOfLines={1}>
                    {[project.client, project.designation].filter(Boolean).join(' · ')}
                  </Text>
                </Pressable>
              ))}
            </View>
          ) : null}

          {/* What is understood, laid out as the desktop's « Détail »: dossier, name, type, when, who, status. */}
          {shownEntry ? (
            <View ref={(node) => hold('title', node)}>
            <Blueprint style={styles.preview}>
              {absence && shownEntry.absence ? (
                <>
                  <Text style={styles.title}>{ABSENCE_LABELS[shownEntry.absence.reason] ?? shownEntry.absence.reason}</Text>
                  <Muted>{shownEntry.interpretation}</Muted>
                </>
              ) : (
                <>
                  <View style={styles.numberRow}>
                    <Text style={[styles.number, !shownEntry.projectNumber && styles.numberNone]}>
                      {shownEntry.projectNumber || 'Sans dossier'}
                    </Text>
                    {shownEntry.project?.client ? (
                      <Muted style={styles.client} numberOfLines={1}>
                        {shownEntry.project.client}
                      </Muted>
                    ) : null}
                  </View>
                  <Caps>Sous-désignation</Caps>
                  <Text style={[styles.title, !subTitle && !designation && styles.titleNone]} numberOfLines={2}>
                    {subTitle || designation || 'Nom libre de la tâche'}
                  </Text>
                  {subTitle && designation ? <Muted style={styles.small}>Projet : {designation}</Muted> : null}
                </>
              )}
            </Blueprint>
            </View>
          ) : null}

          {!absence && types.length ? (
            <View style={styles.block} ref={(node) => hold('type', node)}>
              <Caps>Type</Caps>
              <View style={styles.wrap}>
                {types.map((kind) => (
                  <Chip
                    key={kind.key}
                    label={kind.label}
                    color={kind.color}
                    on={Boolean(entry) && type?.key === kind.key}
                    onPress={() => chooseType(kind.key)}
                  />
                ))}
              </View>
            </View>
          ) : null}

          {timed ? (
            <View style={styles.whenBlock} ref={(node) => hold('when', node)}>
              <View style={styles.whenHead}>
                <Caps style={styles.flex}>Quand</Caps>
                {when ? (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => setPicked({ when: null, basis })}
                    style={styles.whenLink}
                  >
                    <Text style={styles.whenLinkText}>Sans date</Text>
                  </Pressable>
                ) : null}
              </View>
              {when && calendar ? (
                <WhenPicker
                  value={when}
                  calendar={calendar}
                  today={today}
                  disabled={Boolean(editBlocker)}
                  reveal={reveal}
                  onChange={(next, edge) => {
                    turned.current = true;
                    const target = edge === 'start' ? movedStart(calendar, when, next.start) : next;
                    // Written in the field when the entry can say it; kept beside it otherwise.
                    if (!writeWhen(target)) setPicked({ when: target, basis });
                  }}
                />
              ) : (
                <Pressable
                  accessibilityRole="button"
                  disabled={!calendar || Boolean(editBlocker)}
                  // Back to the span typed, else the one offered.
                  onPress={() => setPicked(null)}
                  style={({ pressed }) => [styles.choose, pressed && styles.suggestionPressed]}
                >
                  <Text style={styles.chooseText}>À planifier, sans date</Text>
                  <Text style={styles.chooseAction}>Choisir le début et la fin</Text>
                </Pressable>
              )}
            </View>
          ) : null}

          {!absence ? (
            <View style={styles.flagRow}>
              <Chip
                label="! Urgent"
                color={entry?.urgent ? colors.err : undefined}
                on={Boolean(entry?.urgent)}
                onPress={() => toggle('!', 'flag')}
              />
              <Muted style={[styles.small, styles.flex]}>
                {shownEntry && shownEntry.deadline !== null ? `Échéance : ${describeDay(shownEntry.deadline)}` : 'Échéance : tapez « <06.11 »'}
              </Muted>
            </View>
          ) : null}

          {crews.length ? (
            <View style={styles.block} ref={(node) => hold('who', node)}>
              <View style={styles.blockHead}>
                <Caps style={styles.flex}>Qui</Caps>
                <Muted style={styles.small}>
                  {chosen.length ? `${chosen.length} sélectionné${chosen.length > 1 ? 's' : ''}` : 'Personne'}
                </Muted>
              </View>
              {crews.map((crew) => (
                <View key={crew.key} style={styles.crew}>
                  <Muted style={styles.small}>{crew.name}</Muted>
                  <View style={styles.wrap}>
                    {crew.people.map((person) => {
                      const on = chosen.includes(person.id);
                      const reason = away.get(person.id);
                      return (
                        <Pressable
                          key={person.id}
                          accessibilityRole="button"
                          accessibilityState={{ selected: on }}
                          accessibilityLabel={`${person.name}${reason ? `, absent sur la période : ${reason}` : ''}`}
                          onPress={() => toggle(person.initials ?? '', 'person')}
                          style={[styles.who, reason ? styles.whoAway : null, on && styles.whoOn]}
                        >
                          <Text style={[styles.whoText, reason ? styles.whoTextAway : null, on && styles.whoTextOn]}>{person.initials}</Text>
                        </Pressable>
                      );
                    })}
                  </View>
                </View>
              ))}
              {away.size ? <Muted style={styles.small}>En pointillé : absent sur la période, mais sélectionnable.</Muted> : null}
            </View>
          ) : null}

          {!absence ? (
            <View style={styles.block}>
              <Caps>Statut</Caps>
              <Segmented
                accessibilityLabel="Statut"
                options={STATUS_OPTIONS}
                value={entry?.provisional ? 'provisional' : 'confirmed'}
                onChange={(status) => {
                  if ((status === 'provisional') !== hasToken(text, '?')) toggle('?', 'flag');
                }}
              />
            </View>
          ) : null}

          {waiting ? <Muted>Chargement du planning…</Muted> : null}
          {loadError ? <Notice tone="error" message={loadError} /> : null}
          {editBlocker ? <Notice message={editBlocker} /> : null}
          {done ? <Notice message={done} /> : null}
          {error ? <Notice tone="error" message={error} /> : null}

          {shownEntry ? (
            <View style={styles.outcome}>
              {destination ? <Text style={styles.destination}>{destination}</Text> : null}
              {[...shownEntry.warnings, ...(data ? absenceWarnings(shownEntry, data, names) : [])].map((warning) => (
                <Text key={warning} style={styles.warning}>
                  ▲ {warning}
                </Text>
              ))}
            </View>
          ) : null}

          <Muted style={styles.small}>
            Tout se tape aussi : lun, heures 7h30-11h, 4h, « {'<'}06.11 » pour une échéance, « nuit », /vac LBI 26.10-30.10 pour une
            absence. « Sans date » envoie la tâche dans « À planifier ».
          </Muted>
        </ScrollView>
        </View>

        {/* Always within reach, as the foot of the desktop's panel. */}
        <View style={styles.actions}>
          <Button
            label="Créer et garder"
            disabled={!entry || busy || refused || Boolean(editBlocker)}
            onPress={() => void create(true)}
            style={styles.flex}
          />
          <Button
            label="Créer"
            variant="primary"
            busy={busy}
            disabled={!entry || refused || Boolean(editBlocker)}
            onPress={() => void create(false)}
            style={styles.flex}
          />
        </View>
      </View>
    </SafeAreaView>
  );
}

/** Who is away at some point of `when`, and why (an absence, or a day off every week). */
function awayOver(data: PlanningData, when: When): Map<string, string> {
  const reasons = new Map<string, string>();
  const first = instantDay(when.start);
  const last = instantDay(when.end - 1);
  for (const record of data.absences) {
    if (record.deleted_at) continue;
    const from = parseDate(record.first_day);
    const to = parseDate(record.last_day);
    if (from === null || to === null || from > last || to < first) continue;
    reasons.set(record.resource_id, ABSENCE_LABELS[record.reason ?? 'absent'] ?? 'Absent');
  }
  for (const resource of data.resources) {
    for (const off of resource.recurring_off ?? []) {
      for (let day = first; day <= last; day += 1) {
        if (weekday(day) === off.weekday && !reasons.has(resource.id)) reasons.set(resource.id, ABSENCE_LABELS[off.reason ?? 'ecole'] ?? 'Absent');
      }
    }
  }
  return reasons;
}

/**
 * « LBI est absent jeu–ven (Vacances). » for the people of a dated task: the task is laid
 * all the same and the desktop flags the conflict on its bar.
 */
function absenceWarnings(entry: ParsedEntry, data: PlanningData, names: Map<string, string>): string[] {
  if (entry.destination === 'absence' || entry.start === null || entry.end === null) return [];
  const first = instantDay(entry.start);
  const last = instantDay(entry.end - 1);
  const warnings: string[] = [];
  for (const id of entry.resourceIds) {
    const hits: { from: number; to: number; label: string }[] = [];
    for (const absence of data.absences) {
      if (absence.deleted_at || absence.resource_id !== id) continue;
      const from = parseDate(absence.first_day);
      const to = parseDate(absence.last_day);
      if (from === null || to === null || from > last || to < first) continue;
      hits.push({ from: Math.max(from, first), to: Math.min(to, last), label: ABSENCE_LABELS[absence.reason ?? 'absent'] ?? 'Absent' });
    }
    const resource = data.resources.find((candidate) => candidate.id === id);
    for (const off of resource?.recurring_off ?? []) {
      for (let day = first; day <= last; day += 1) {
        if (weekday(day) === off.weekday) hits.push({ from: day, to: day, label: ABSENCE_LABELS[off.reason ?? 'ecole'] ?? 'Absent' });
      }
    }
    for (const hit of hits) {
      const days =
        hit.from === hit.to ? DAY_ABBREVIATIONS[weekday(hit.from)] : `${DAY_ABBREVIATIONS[weekday(hit.from)]}–${DAY_ABBREVIATIONS[weekday(hit.to)]}`;
      warnings.push(`${names.get(id) ?? id} est absent ${days} (${hit.label}). La tâche sera posée, avec un conflit signalé.`);
    }
  }
  return warnings;
}

/** « Ira dans le planning, sur les lignes LBI et YCH. », as in the mobile design. */
function destinationText(entry: ParsedEntry, label: (ids: readonly string[]) => string): string {
  if (entry.destination === 'grid') {
    const people = label(entry.resourceIds);
    return entry.resourceIds.length > 1
      ? `Ira dans le planning, sur les lignes ${people.replace(/, ([^,]*)$/, ' et $1')}.`
      : `Ira dans le planning, sur la ligne ${people}.`;
  }
  if (entry.destination === 'unassigned') return 'Ira dans le planning, sur la ligne Non affecté.';
  if (entry.destination === 'backlog') return 'Ira dans « À planifier ».';
  if (entry.destination === 'absence') return entry.absence?.resourceIds.length ? '' : NO_PERSON_ABSENCE;
  return '';
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  barButton: { minWidth: 80, minHeight: touch, justifyContent: 'center', paddingHorizontal: 8 },
  barText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 1.1, textTransform: 'uppercase', color: colors.accent700 },
  barTitle: { color: colors.text },
  // The field and its steps, which do not scroll.
  top: { gap: 8, paddingHorizontal: 20, paddingTop: 14, paddingBottom: 10, borderBottomWidth: 1, borderBottomColor: colors.lineSoft },
  content: { paddingHorizontal: 20, paddingTop: 14, gap: 14, paddingBottom: 24 },
  input: {
    minHeight: 52,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
    fontFamily: fonts.bodyMedium,
    fontSize: 18,
    color: colors.text,
  },
  // Over the field, to the pixel: its border and padding, its type.
  ghost: { position: 'absolute', left: 13, right: 13, top: 0, bottom: 0, justifyContent: 'center' },
  ghostText: { fontFamily: fonts.bodyMedium, fontSize: 18, color: 'transparent' },
  ghostHint: { color: GHOST_COLOR },
  steps: { gap: 4 },
  step: { minHeight: 32, justifyContent: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: colors.line },
  stepDone: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  stepNext: { borderColor: colors.accent700 },
  stepText: { fontFamily: fonts.heading, fontSize: 14, letterSpacing: 0.5, color: colors.muted },
  stepTextDone: { color: colors.accent900 },
  // Passed over: said as what the task then is (« Sans dossier »), in a dashed frame.
  stepPassed: { borderStyle: 'dashed' },
  stepTextPassed: { fontFamily: fonts.body, letterSpacing: 0 },
  stepTextNext: { fontFamily: fonts.headingBold, color: colors.accent800 },
  stepHint: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  preview: { padding: 14, gap: 4 },
  // The dossier number leads, large and underlined in the accent, as on the desktop.
  numberRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 10,
    marginBottom: 6,
    paddingBottom: 4,
    borderBottomWidth: 2,
    borderBottomColor: colors.accent,
  },
  number: { fontFamily: fonts.headingBold, fontSize: 30, lineHeight: 34, letterSpacing: 0.6, color: colors.accent900 },
  numberNone: { fontSize: 20, color: colors.muted },
  client: { flex: 1, textAlign: 'right' },
  title: { fontFamily: fonts.bodySemiBold, fontSize: 16, color: colors.text },
  titleNone: { fontFamily: fonts.body, color: colors.muted },
  block: { gap: 6 },
  blockHead: { flexDirection: 'row', alignItems: 'baseline' },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  flagRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  crew: { gap: 4 },
  // Initials to tick, five to a row on a phone.
  who: {
    minWidth: 56,
    height: touch,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  whoOn: { borderStyle: 'solid', borderColor: colors.accent700, backgroundColor: colors.accent100 },
  whoAway: { borderStyle: 'dashed', borderColor: colors.absence, backgroundColor: colors.absenceSoft },
  whoText: { fontFamily: fonts.headingBold, fontSize: 16, letterSpacing: 0.6, color: colors.subtle },
  whoTextOn: { color: colors.accent900 },
  whoTextAway: { color: colors.absenceInk },
  outcome: { gap: 4 },
  destination: { fontFamily: fonts.body, fontSize: 14, color: colors.accent800 },
  warning: { fontFamily: fonts.body, fontSize: 14, color: colors.absenceInk },
  small: { fontSize: 13 },
  suggestions: { gap: 6 },
  suggestion: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    minHeight: touch,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  suggestionPressed: { backgroundColor: colors.accent100 },
  suggestionNumber: { fontSize: 15 },
  suggestionText: { flex: 1, fontFamily: fonts.body, fontSize: 14, color: colors.text },
  actions: {
    flexDirection: 'row',
    gap: 10,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    backgroundColor: colors.bg,
  },
  whenBlock: { gap: 2, padding: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  whenHead: { flexDirection: 'row', alignItems: 'center', minHeight: 28 },
  whenLink: { minHeight: 28, justifyContent: 'center', paddingLeft: 12 },
  whenLinkText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.accent700, textDecorationLine: 'underline' },
  choose: { minHeight: touch + 8, justifyContent: 'center', gap: 2, paddingVertical: 6 },
  chooseText: { fontFamily: fonts.body, fontSize: 15, color: colors.muted },
  chooseAction: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.accent700 },
});
