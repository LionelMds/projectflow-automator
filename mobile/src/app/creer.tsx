import { router, useLocalSearchParams } from 'expo-router';
import { Check, ChevronLeft, ChevronRight, Clock, Moon } from 'lucide-react-native';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Animated, Keyboard, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import { AlertPicker } from '@/components/AlertPicker';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Notice } from '@/components/Notice';
import { Placement } from '@/components/Placement';
import { Segmented } from '@/components/Segmented';
import { Caps, DossierNumber, Kicker, Muted } from '@/components/Text';
import { Wheel, WheelBand, type WheelItem } from '@/components/Wheel';
import { WhenPicker } from '@/components/WhenPicker';
import { selectionFeedback } from '@/lib/haptics';
import { useKeyboardLift } from '@/lib/keyboard';
import { close } from '@/lib/navigation';
import { awayOver } from '@/lib/planning/away';
import { calendarResolver, contextFor, createTask, standardSpan } from '@/lib/planning/commands';
import { describeDay, describeInstant, wheelDayLabel } from '@/lib/planning/display';
import type { Change } from '@/lib/planning/documents';
import { dossierCache, resolveDossierNumber } from '@/lib/planning/dossiers';
import { dossierSuggestions } from '@/lib/planning/entryShortcuts';
import { dayStart, instantDay } from '@/lib/planning/instant';
import { taskTypeOf, taskTypesOf, teamsOf, type Reminder } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import { UNASSIGNED } from '@/lib/planning/week';
import { dateParts, todayNumber } from '@/lib/planning/walltime';
import { firstWhen, movedStart, whenRefused, type When } from '@/lib/planning/when';
import { useRecentDossiers } from '@/lib/recent/RecentContext';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

/**
 * A task created one question at a time: the dossier, its name, its type, who, when, then
 * the last details and the creation. The answers already given stay in sight on a trail
 * that leads back to each of them.
 */
type Stage = 'dossier' | 'name' | 'type' | 'who' | 'when' | 'final';
const STAGES: { key: Stage; label: string }[] = [
  { key: 'dossier', label: 'Dossier' },
  { key: 'name', label: 'Désignation' },
  { key: 'type', label: 'Type' },
  { key: 'who', label: 'Qui' },
  { key: 'when', label: 'Quand' },
  { key: 'final', label: 'Finaliser' },
];
const ORDER = STAGES.map((stage) => stage.key);
/** A night of work, as the quick entry's « nuit »: 21:00 to 05:00. */
const NIGHT_START = 21 * 60;
const NIGHT_END = 5 * 60;
/** Days the deadline wheel offers, from today. */
const DEADLINE_DAYS = 370;
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

/** The days of the deadline wheel: today and the year after. */
function daysFrom(today: number): WheelItem[] {
  return Array.from({ length: DEADLINE_DAYS }, (_, offset) => ({ key: String(today + offset), label: wheelDayLabel(today + offset, today) }));
}

function clock(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`;
}

export default function CreerScreen() {
  const params = useLocalSearchParams<{ row?: string; start?: string; end?: string; anchor?: string; dossier?: string }>();
  const { data, apply, author, editBlocker, waiting, error: loadError } = useLoadedPlanning();
  const { settings } = useSettings();
  const { projects, wantProjects } = useRepertoire();
  const { recent, remember } = useRecentDossiers();
  const lift = useKeyboardLift(useSafeAreaInsets().bottom);
  const today = todayNumber();
  const year = dateParts(today).year;

  // What the place the screen was opened from already says: a cell of the planning gives
  // who and when, a dossier its number. Those questions are answered, and passed over.
  const cellRow = params.row && params.row !== UNASSIGNED ? params.row : null;
  const cellStart = params.start ? Number(params.start) : null;
  const cellEnd = params.end ? Number(params.end) : null;
  const given: Partial<Record<Stage, boolean>> = {
    dossier: Boolean(params.dossier),
    who: Boolean(params.row),
    when: cellStart !== null,
  };

  const [stage, setStage] = useState<Stage>(given.dossier ? 'name' : 'dossier');
  const [number, setNumber] = useState(() => (params.dossier ?? '').trim());
  const [noDossier, setNoDossier] = useState(false);
  const [title, setTitle] = useState('');
  const [typeKey, setTypeKey] = useState<string | null>(null);
  const [people, setPeople] = useState<string[]>(cellRow ? [cellRow] : []);
  const [picked, setPicked] = useState<When | null>(null);
  const [dated, setDated] = useState(true);
  // The end of the span last touched: the preview shows the week of that end.
  const [edge, setEdge] = useState<'start' | 'end'>('start');
  const [night, setNight] = useState(false);
  // « Hors horaires standard »: the task may lie over the lunch hour and on Friday afternoon.
  const [offStandard, setOffStandard] = useState(false);
  // The span as it was when the task was let out, and what it was before: put back at once,
  // untouched, it is given its first hours again rather than a day of ten hours laid on nine.
  const letOut = useRef<{ shown: When; before: When | null } | null>(null);
  const [notes, setNotes] = useState('');
  const [provisional, setProvisional] = useState(false);
  const [urgent, setUrgent] = useState(false);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [choosingDeadline, setChoosingDeadline] = useState(false);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [error, setError] = useState('');
  const [done, setDone] = useState('');
  const [busy, setBusy] = useState(false);

  // The dossiers come from the répertoire, as on the desktop: this year's to begin with.
  const source = settings.demo || Boolean(settings.repertoire);
  useEffect(() => {
    if (source) void wantProjects([`${year}-`]);
  }, [source, wantProjects, year]);
  // « 5299 » is the dossier « 2026-5299 »: the one the répertoire has, else this year's.
  // Until then the year is not made up: the field reads « 20AA-XXXX », and « 20AA » gives
  // way to the year as soon as the number says which (the répertoire's dossier, or four
  // digits typed).
  const typed = number.trim();
  const short = /^\d+$/.test(typed);
  const found = short ? resolveDossierNumber(typed, projects) : typed.toUpperCase();
  const matched = short && !/^\d+$/.test(found);
  const yearShown = matched ? found.slice(0, 4) : short && typed.length >= 4 ? String(year) : null;
  const resolved = !typed ? '' : short && !matched ? `${year}-${typed}` : found;
  const project = resolved ? (projects.get(resolved) ?? null) : null;
  const designation = (project?.designation ?? '').trim();
  // To touch instead of typing: what matches the digits typed, else the dossiers last
  // opened on this phone, else the latest of the répertoire.
  const latest = useMemo(
    () => [...projects.values()].sort((a, b) => (a.number < b.number ? 1 : a.number > b.number ? -1 : 0)).slice(0, 5),
    [projects],
  );
  const offers = typed ? dossierSuggestions(typed, projects.values(), 5) : recent.length ? recent.slice(0, 5) : latest;
  const offersTitle = typed ? 'Dossiers du répertoire' : recent.length ? 'Dossiers récents' : 'Derniers dossiers';

  const types = data ? taskTypesOf(data.meta) : [];
  const type = data && typeKey ? taskTypeOf(data.meta, typeKey) : null;
  // « Qui », as the desktop's « Détail » lays it out: everyone, team by team, as initials.
  const crews = useMemo(() => {
    if (!data) return [];
    const everyone = data.resources.filter((resource) => !resource.deleted_at && resource.active !== false && resource.initials);
    const teams = [...teamsOf(data.meta)].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const keys = teams.map((team) => team.key);
    for (const person of everyone) if (!keys.includes(person.team || 'atelier')) keys.push(person.team || 'atelier');
    return keys
      .map((key) => ({
        key,
        name: teams.find((team) => team.key === key)?.name ?? key,
        people: everyone
          .filter((person) => (person.team || 'atelier') === key)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.name.toLowerCase().localeCompare(b.name.toLowerCase())),
      }))
      .filter((crew) => crew.people.length);
  }, [data]);
  const initialsOf = (id: string) => data?.resources.find((resource) => resource.id === id)?.initials || id;
  const me = crews.flatMap((crew) => crew.people).find((person) => settings.initials && (person.initials ?? '').toUpperCase() === settings.initials.toUpperCase());

  // When: the span turned on the wheels, else the one first offered (the cell touched, or
  // the next working morning); a night is the evening of that day.
  // The hours of the first person; a task let out of them is laid and measured without their breaks.
  const standard = useMemo(() => (data ? calendarResolver(data)(people[0] ?? null) : null), [data, people]);
  const calendar = standard ? standard.hoursOf(offStandard && !night) : null;
  const offered = calendar ? firstWhen(calendar, today, {}, cellStart !== null ? { start: cellStart, end: cellEnd } : null) : null;
  const day: When | null = dated ? (picked ?? offered) : null;
  const span: When | null =
    day && night
      ? { start: dayStart(instantDay(day.start)) + NIGHT_START, end: dayStart(instantDay(day.start) + 1) + NIGHT_END }
      : day;
  const refused = Boolean(day && !night && calendar && whenRefused(calendar, day));
  const away = data && span ? awayOver(data, span) : new Map<string, string>();

  // One question on screen: it comes in from the side it is reached by.
  const [enter] = useState(() => new Animated.Value(1));
  useEffect(() => {
    enter.setValue(0);
    Animated.timing(enter, { toValue: 1, duration: 180, useNativeDriver: Platform.OS !== 'web' }).start();
  }, [stage, enter]);
  const slide = useMemo(() => enter.interpolate({ inputRange: [0, 1], outputRange: [14, 0] }), [enter]);

  const go = (to: Stage) => {
    setError('');
    setChoosingDeadline(false);
    if (to !== 'dossier' && to !== 'name' && to !== 'final') Keyboard.dismiss();
    setStage(to);
  };
  /** The question after (or before) `from` that is still to be asked: those the entry point answered are passed. */
  const beside = (from: Stage, step: 1 | -1): Stage => {
    let index = ORDER.indexOf(from) + step;
    while (index > 0 && index < ORDER.length - 1 && given[ORDER[index]]) index += step;
    return ORDER[Math.min(Math.max(index, 0), ORDER.length - 1)];
  };
  const index = ORDER.indexOf(stage);
  const first = beside(stage, -1) === stage || (index === 1 && given.dossier);

  /** What each question was answered with, for the trail; empty while it is not. */
  const answer = (key: Stage): string => {
    if (key === 'dossier') return resolved ? resolved.replace(/^\d{4}-/, '') : noDossier ? 'Sans dossier' : '';
    if (key === 'name') return title.trim() ? capitalized(title.trim()) : resolved && ORDER.indexOf(stage) > 1 ? 'Nom du projet' : '';
    if (key === 'type') return type?.label ?? '';
    if (key === 'who') return people.length ? people.map(initialsOf).join(' ') : ORDER.indexOf(stage) > 3 || given.who ? 'Non affecté' : '';
    if (key === 'when') {
      if (!dated) return 'Sans date';
      if (!span || !(ORDER.indexOf(stage) > 4 || given.when || picked)) return '';
      return night ? `Nuit du ${shortDate(instantDay(span.start))}` : `${shortDate(instantDay(span.start))} ${clock(span.start - dayStart(instantDay(span.start)))}`;
    }
    return '';
  };

  const ready =
    stage === 'dossier'
      ? Boolean(typed)
      : stage === 'name'
        ? Boolean(title.trim() || resolved)
        : stage === 'type'
          ? typeKey !== null
          : stage === 'when'
            ? !refused
            : true;

  const reset = () => {
    setTitle('');
    setNotes('');
    setUrgent(false);
    setDeadline(null);
    setProvisional(false);
  };

  const create = async (another: boolean) => {
    if (!data) return;
    setError('');
    setDone('');
    setBusy(true);
    try {
      // The dossier's details are copied in the task: a dossier of a year not read yet is read first.
      const directory = resolved && !projects.has(resolved) ? await wantProjects([resolved]) : projects;
      const info = resolved ? (directory.get(resolved) ?? null) : null;
      const name = capitalized(title.trim());
      const kind = typeKey ?? taskTypeOf(data.meta, undefined).key;
      let changes: Change[];
      try {
        changes = createTask(contextFor(data, author), {
          title: name,
          taskType: kind,
          projectNumber: resolved,
          projectCache: info ? dossierCache(info) : undefined,
          start: span?.start ?? null,
          end: span?.end ?? null,
          resourceIds: people,
          estimateMinutes: !span ? 0 : night ? span.end - span.start : (calendar?.workBetween(span.start, span.end) ?? 0),
          estimateDays: !span || night ? 0 : (calendar?.wholeDays(span.start, span.end) ?? 0),
          status: provisional ? 'provisional' : 'confirmed',
          deadline,
          urgent,
          notes: notes.trim(),
          origin: 'quick_entry',
          offHours: night && span !== null,
          offStandard: offStandard && !night,
          reminders,
        });
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : 'Création impossible.');
        return;
      }
      const shown = `${resolved} ${name || info?.designation || ''}`.trim() || 'une tâche';
      await apply(changes, `Créé : ${shown}`);
      if (resolved) remember(resolved);
      if (another) {
        // The same dossier, type, people and day: only what names the task is asked again.
        reset();
        setDone(span ? `Planifié : ${shown}` : `Ajouté à « À planifier » : ${shown}`);
        setStage('name');
      } else close('/planning');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Enregistrement impossible.');
    } finally {
      setBusy(false);
    }
  };

  const toText = () => {
    const query: Record<string, string> = {};
    if (params.row) query.row = params.row;
    if (params.start) query.start = params.start;
    if (params.end) query.end = params.end;
    if (params.anchor) query.anchor = params.anchor;
    const words = [resolved, title.trim()].filter(Boolean).join(' ');
    if (words) query.text = `${words} `;
    router.replace({ pathname: '/saisie', params: query });
  };

  const toggle = (id: string) => {
    selectionFeedback();
    setPeople((current) => (current.includes(id) ? current.filter((other) => other !== id) : [...current, id]));
  };

  let body: ReactNode = null;
  if (stage === 'dossier') {
    body = (
      <>
        <Question title="Quel dossier ?" hint="Tapez son numéro, ou touchez-en un ci-dessous." />
        <View style={styles.numberField}>
          {short || !typed ? <Text style={[styles.numberYear, yearShown ? styles.numberYearKnown : null]}>{yearShown ?? '20AA'}-</Text> : null}
          <TextInput
            accessibilityLabel="Numéro du dossier"
            value={number}
            onChangeText={(value) => {
              setNumber(value.replace(/[^\dA-Za-z-]/g, ''));
              setNoDossier(false);
            }}
            keyboardType="number-pad"
            autoFocus
            maxLength={14}
            placeholder="XXXX"
            placeholderTextColor="#a5a9ae"
            style={styles.numberInput}
          />
        </View>
        {project ? (
          <View style={styles.found}>
            <Check size={18} color={colors.ok} strokeWidth={2} />
            <Text style={styles.foundText} numberOfLines={2}>
              {[project.client, project.designation].filter(Boolean).join(' · ') || 'Dossier du répertoire'}
            </Text>
          </View>
        ) : typed.length >= 3 ? (
          <Muted style={styles.small}>Pas dans le répertoire lu : le numéro sera gardé tel quel.</Muted>
        ) : null}
        {offers.length ? (
          <View style={styles.list}>
            <Caps>{offersTitle}</Caps>
            {offers.map((item) => (
              <Pressable
                key={item.number}
                accessibilityRole="button"
                accessibilityLabel={`Choisir le dossier ${item.number}`}
                onPress={() => {
                  selectionFeedback();
                  setNumber(item.number);
                  setNoDossier(false);
                  go('name');
                }}
                style={({ pressed }) => [styles.offer, pressed && styles.pressed]}
              >
                <DossierNumber style={styles.offerNumber}>{item.number}</DossierNumber>
                <Text style={styles.offerText} numberOfLines={1}>
                  {[item.client, item.designation].filter(Boolean).join(' · ')}
                </Text>
                <ChevronRight size={18} color={colors.muted} strokeWidth={iconStroke} />
              </Pressable>
            ))}
          </View>
        ) : null}
      </>
    );
  } else if (stage === 'name') {
    body = (
      <>
        <Question
          title={resolved ? 'Quelle tâche sur ce dossier ?' : 'Quel est le nom de la tâche ?'}
          hint={designation ? `Projet : ${designation}` : resolved ? `Dossier ${resolved}` : 'Sans dossier, la tâche porte ce nom.'}
        />
        <TextInput
          accessibilityLabel="Désignation de la tâche"
          value={title}
          onChangeText={setTitle}
          autoFocus
          autoCorrect={false}
          returnKeyType="next"
          onSubmitEditing={() => ready && go(beside('name', 1))}
          placeholder={resolved ? 'ex. Pose du garde-corps' : 'ex. Rendez-vous client'}
          placeholderTextColor="#a5a9ae"
          style={styles.input}
        />
        {resolved ? <Muted style={styles.small}>Laissez vide pour garder le nom du projet.</Muted> : null}
      </>
    );
  } else if (stage === 'type') {
    body = (
      <>
        <Question title="Quel type de tâche ?" />
        <View style={styles.tiles}>
          {types.map((kind) => {
            const on = typeKey === kind.key;
            return (
              <Pressable
                key={kind.key}
                accessibilityRole="button"
                accessibilityState={{ selected: on }}
                accessibilityLabel={kind.label}
                onPress={() => {
                  selectionFeedback();
                  setTypeKey(kind.key);
                  // The choice shows for a moment, then the next question comes by itself.
                  setTimeout(() => go(beside('type', 1)), 170);
                }}
                style={({ pressed }) => [styles.tile, { borderColor: kind.color }, (on || pressed) && { backgroundColor: `${kind.color}33` }]}
              >
                <View style={[styles.tileSwatch, { backgroundColor: kind.color }]} />
                <Text style={styles.tileText}>{kind.label}</Text>
                {on ? <Check size={18} color={colors.accent900} strokeWidth={2} /> : null}
              </Pressable>
            );
          })}
        </View>
      </>
    );
  } else if (stage === 'who') {
    body = (
      <>
        <Question title="Qui s’en charge ?" hint={people.length ? `${people.length} sélectionné${people.length > 1 ? 's' : ''}` : 'Personne : la tâche ira sur « Non affecté ».'} />
        {me && !people.includes(me.id) ? (
          <Pressable accessibilityRole="button" onPress={() => toggle(me.id)} style={({ pressed }) => [styles.meRow, pressed && styles.pressed]}>
            <Text style={styles.meText}>Moi · {me.initials}</Text>
          </Pressable>
        ) : null}
        {crews.map((crew) => (
          <View key={crew.key} style={styles.crew}>
            <Muted style={styles.small}>{crew.name}</Muted>
            <View style={styles.wrap}>
              {crew.people.map((person) => {
                const on = people.includes(person.id);
                return (
                  <Pressable
                    key={person.id}
                    accessibilityRole="button"
                    accessibilityState={{ selected: on }}
                    accessibilityLabel={person.name}
                    onPress={() => toggle(person.id)}
                    style={[styles.who, on && styles.whoOn]}
                  >
                    <Text style={[styles.whoText, on && styles.whoTextOn]}>{person.initials}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        ))}
      </>
    );
  } else if (stage === 'when') {
    body = (
      <>
        <View style={styles.between}>
          <Text accessibilityRole="header" style={styles.question}>
            Quand ?
          </Text>
          <Pressable accessibilityRole="button" onPress={() => setDated((current) => !current)} style={styles.link}>
            <Text style={styles.linkText}>{dated ? 'Sans date' : 'Choisir une date'}</Text>
          </Pressable>
        </View>
        {!day || !calendar ? (
          <Notice message="Sans date : la tâche ira dans « À planifier », à placer plus tard." />
        ) : (
          <>
            {night && span ? (
              <View style={styles.night}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Nuit précédente"
                  onPress={() => setPicked({ start: day.start - 1440, end: day.end - 1440 })}
                  style={styles.nightArrow}
                >
                  <ChevronLeft size={22} color={colors.accent700} strokeWidth={iconStroke} />
                </Pressable>
                <View style={styles.nightText}>
                  <Text style={styles.nightDay}>Nuit du {describeDay(instantDay(span.start))}</Text>
                  <Muted>21:00 → 05:00 le lendemain</Muted>
                </View>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Nuit suivante"
                  onPress={() => setPicked({ start: day.start + 1440, end: day.end + 1440 })}
                  style={styles.nightArrow}
                >
                  <ChevronRight size={22} color={colors.accent700} strokeWidth={iconStroke} />
                </Pressable>
              </View>
            ) : (
              <View style={styles.card}>
                <WhenPicker
                  value={day}
                  calendar={calendar}
                  today={today}
                  onEdge={setEdge}
                  onChange={(next, turned) => {
                    setEdge(turned);
                    setPicked(turned === 'start' ? movedStart(calendar, day, next.start) : next);
                  }}
                />
              </View>
            )}
            {/* The two ways out of the working hours, side by side: one at most. */}
            <View style={styles.hours}>
            <Pressable
              accessibilityRole="switch"
              accessibilityState={{ checked: night }}
              onPress={() => {
                selectionFeedback();
                // A night has its own hours: it is never « hors horaires standard » as well.
                if (!night) setOffStandard(false);
                setNight((current) => !current);
              }}
              style={[styles.nightToggle, night && styles.nightToggleOn]}
            >
              <Moon size={16} color={night ? '#ffffff' : colors.accent700} strokeWidth={iconStroke} />
              <Text style={[styles.nightToggleText, night && styles.nightToggleTextOn]}>Travail de nuit</Text>
            </Pressable>
            <Pressable
              accessibilityRole="switch"
              accessibilityState={{ checked: offStandard && !night }}
              accessibilityHint="La pause de midi et le vendredi après-midi comptent"
              onPress={() => {
                selectionFeedback();
                if (!offStandard || night) {
                  // Let out: only the switch changes, the hours shown stay.
                  if (day && !night) {
                    letOut.current = { shown: day, before: picked };
                    setPicked(day);
                  }
                  setNight(false);
                  setOffStandard(true);
                } else {
                  // Put back: the time it lasted, taken again on the usual hours.
                  const kept = letOut.current;
                  if (day && kept && kept.shown.start === day.start && kept.shown.end === day.end) setPicked(kept.before);
                  else if (day && standard) {
                    const [start, end] = standardSpan(standard, standard.withoutBreaks(), day.start, day.end);
                    setPicked({ start, end });
                  }
                  letOut.current = null;
                  setOffStandard(false);
                }
              }}
              style={[styles.nightToggle, offStandard && !night && styles.nightToggleOn]}
            >
              <Clock size={16} color={offStandard && !night ? '#ffffff' : colors.accent700} strokeWidth={iconStroke} />
              <Text style={[styles.nightToggleText, offStandard && !night && styles.nightToggleTextOn]}>Hors horaires standard</Text>
            </Pressable>
            </View>
            {data && span ? (
              <Placement
                data={data}
                when={span}
                people={people}
                taskType={typeKey ?? taskTypeOf(data.meta, undefined).key}
                night={night}
                edge={night ? 'start' : edge}
              />
            ) : null}
          </>
        )}
      </>
    );
  } else {
    body = (
      <>
        <Question title="Tout est bon ?" hint="Touchez une ligne pour la corriger." />
        <View style={styles.card}>
          <Recap label="Dossier" value={resolved ? `${resolved}${project?.client ? ` · ${project.client}` : ''}` : 'Sans dossier'} onPress={() => go('dossier')} />
          <Recap label="Désignation" value={capitalized(title.trim()) || designation || 'Sans titre'} onPress={() => go('name')} />
          <Recap label="Type" value={(type ?? (data ? taskTypeOf(data.meta, undefined) : null))?.label ?? ''} swatch={(type ?? (data ? taskTypeOf(data.meta, undefined) : null))?.color} onPress={() => go('type')} />
          <Recap label="Qui" value={people.length ? people.map(initialsOf).join(', ') : 'Non affecté'} onPress={() => go('who')} />
          <Recap
            label="Quand"
            value={span ? `${describeInstant(span.start)} → ${describeInstant(span.end)}${night ? ' · nuit' : offStandard ? ' · hors horaires standard' : ''}` : 'Sans date (À planifier)'}
            onPress={() => go('when')}
            last
          />
        </View>
        {[...away].filter(([id]) => people.includes(id)).map(([id, reason]) => (
          <Text key={id} style={styles.warning}>
            ▲ {initialsOf(id)} est absent sur ce créneau ({reason}). La tâche sera posée, avec un conflit signalé.
          </Text>
        ))}
        <View style={styles.block}>
          <Caps>Statut</Caps>
          <Segmented
            accessibilityLabel="Statut"
            options={STATUS_OPTIONS}
            value={provisional ? 'provisional' : 'confirmed'}
            onChange={(status) => setProvisional(status === 'provisional')}
          />
        </View>
        <View style={styles.flags}>
          <Chip label="! Urgent" color={urgent ? colors.err : undefined} on={urgent} onPress={() => setUrgent((current) => !current)} />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={deadline === null ? 'Ajouter une échéance' : `Échéance : ${describeDay(deadline)}`}
            onPress={() => {
              if (deadline === null) setDeadline(today + 7);
              setChoosingDeadline((current) => !current);
            }}
            style={[styles.deadline, deadline !== null && styles.deadlineOn]}
          >
            <Text style={[styles.deadlineText, deadline !== null && styles.deadlineTextOn]}>
              {deadline === null ? '+ Échéance' : `Échéance : ${describeDay(deadline)}`}
            </Text>
          </Pressable>
        </View>
        {choosingDeadline && deadline !== null ? (
          <View style={styles.card}>
            <View style={styles.wheels}>
              <WheelBand />
              <Wheel accessibilityLabel="Échéance : jour" items={daysFrom(today)} index={deadline - today} onChange={(at) => setDeadline(today + at)} />
            </View>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setDeadline(null);
                setChoosingDeadline(false);
              }}
              style={styles.link}
            >
              <Text style={styles.linkText}>Sans échéance</Text>
            </Pressable>
          </View>
        ) : null}
        <View style={styles.block}>
          <Caps>Alerte notification (facultatif)</Caps>
          <AlertPicker reminders={reminders} start={span?.start ?? null} today={today} onChange={setReminders} />
        </View>
        <View style={styles.block}>
          <Caps>Notes (facultatif)</Caps>
          <TextInput
            accessibilityLabel="Notes"
            value={notes}
            onChangeText={setNotes}
            multiline
            placeholder="Remarques pour l’atelier"
            placeholderTextColor="#a5a9ae"
            style={[styles.input, styles.notes]}
          />
        </View>
      </>
    );
  }

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/planning')} style={styles.barButton}>
          <Text style={styles.barText}>Annuler</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Nouvelle tâche</Kicker>
        <Pressable accessibilityRole="button" accessibilityHint="Écrire la tâche en une ligne" onPress={toText} style={[styles.barButton, styles.barRight]}>
          <Text style={styles.barQuiet}>Saisie texte</Text>
        </Pressable>
      </View>

      {/* The trail: where one is, and what was answered, each answer leading back to its question. */}
      <View style={styles.trail}>
        <View style={styles.progress}>
          <View style={[styles.progressOn, { width: `${((index + 1) / ORDER.length) * 100}%` }]} />
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={styles.crumbs}>
          {STAGES.map((item, position) => {
            const said = answer(item.key);
            const here = item.key === stage;
            return (
              <Pressable
                key={item.key}
                accessibilityRole="button"
                accessibilityLabel={`${item.label}${said ? ` : ${said}` : ''}`}
                accessibilityState={{ selected: here }}
                onPress={() => go(item.key)}
                style={[styles.crumb, said ? styles.crumbSaid : null, here && styles.crumbHere]}
              >
                <Text style={[styles.crumbText, said ? styles.crumbTextSaid : null, here && styles.crumbTextHere]} numberOfLines={1}>
                  {said || `${position + 1} ${item.label}`}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>

      <View style={[styles.flex, { paddingBottom: lift }]}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {waiting ? <Muted>Chargement du planning…</Muted> : null}
          {loadError ? <Notice tone="error" message={loadError} /> : null}
          {done ? <Notice message={done} /> : null}
          <Animated.View key={stage} style={[styles.stage, { opacity: enter, transform: [{ translateX: slide }] }]}>
            {body}
          </Animated.View>
          {stage === 'final' && editBlocker ? <Notice message={editBlocker} /> : null}
          {error ? <Notice tone="error" message={error} /> : null}
        </ScrollView>

        <View style={styles.foot}>
          {stage === 'final' ? (
            <>
              <Button label="Créer et continuer" disabled={busy || Boolean(editBlocker) || !data} onPress={() => void create(true)} style={styles.flex} accessibilityHint="Crée la tâche et en prépare une autre sur le même dossier" />
              <Button label="Créer" variant="primary" busy={busy} disabled={Boolean(editBlocker) || !data} onPress={() => void create(false)} style={styles.flex} />
            </>
          ) : (
            <>
              {first ? null : <Button label="Retour" onPress={() => go(beside(stage, -1))} />}
              {stage === 'dossier' ? (
                <Button
                  label="Sans dossier"
                  onPress={() => {
                    setNumber('');
                    setNoDossier(true);
                    go('name');
                  }}
                  accessibilityHint="Un rendez-vous, une tâche interne"
                />
              ) : null}
              <Button
                label={stage === 'who' && !people.length ? 'Non affecté' : 'Suivant'}
                variant="primary"
                disabled={!ready}
                onPress={() => go(beside(stage, 1))}
                style={styles.flex}
              />
            </>
          )}
        </View>
      </View>
    </SafeAreaView>
  );
}

/** The question of a screen, large, and what helps answer it. */
function Question({ title, hint }: { title: string; hint?: string }) {
  return (
    <View style={styles.head}>
      <Text accessibilityRole="header" style={styles.question}>
        {title}
      </Text>
      {hint ? <Muted>{hint}</Muted> : null}
    </View>
  );
}

/** A line of the last screen: what was answered, and the way back to its question. */
function Recap({ label, value, swatch, onPress, last = false }: { label: string; value: string; swatch?: string; onPress: () => void; last?: boolean }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label} : ${value}. Modifier`} onPress={onPress} style={({ pressed }) => [styles.recap, last && styles.recapLast, pressed && styles.pressed]}>
      <Caps style={styles.recapLabel}>{label}</Caps>
      {swatch ? <View style={[styles.tileSwatch, { backgroundColor: swatch }]} /> : null}
      <Text style={styles.recapValue} numberOfLines={2}>
        {value}
      </Text>
      <ChevronRight size={16} color={colors.muted} strokeWidth={iconStroke} />
    </Pressable>
  );
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
  barButton: { minWidth: 96, minHeight: touch, justifyContent: 'center', paddingHorizontal: 8 },
  barRight: { alignItems: 'flex-end' },
  barText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 1.1, textTransform: 'uppercase', color: colors.accent700 },
  // The one-line entry stays a touch away, without asking for attention.
  barQuiet: { fontFamily: fonts.body, fontSize: 13, color: colors.muted, textDecorationLine: 'underline' },
  barTitle: { color: colors.text },
  trail: { borderBottomWidth: 1, borderBottomColor: colors.lineSoft },
  progress: { height: 3, backgroundColor: colors.lineSoft },
  progressOn: { height: 3, backgroundColor: colors.accent700 },
  crumbs: { gap: 6, paddingHorizontal: 16, paddingVertical: 8 },
  crumb: { minHeight: 30, maxWidth: 150, justifyContent: 'center', paddingHorizontal: 9, borderWidth: 1, borderColor: colors.lineSoft },
  crumbSaid: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  crumbHere: { borderColor: colors.accent900, borderWidth: 2 },
  crumbText: { fontFamily: fonts.heading, fontSize: 13, letterSpacing: 0.4, color: colors.muted },
  crumbTextSaid: { color: colors.accent900 },
  crumbTextHere: { fontFamily: fonts.headingBold, color: colors.accent900 },
  content: { padding: 20, gap: 14, paddingBottom: 28 },
  stage: { gap: 14 },
  head: { gap: 4 },
  question: { fontFamily: fonts.headingBold, fontSize: 28, lineHeight: 32, letterSpacing: 0.3, textTransform: 'uppercase', color: colors.text },
  small: { fontSize: 13 },
  between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  link: { minHeight: 36, justifyContent: 'center', alignSelf: 'flex-start' },
  linkText: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.accent700, textDecorationLine: 'underline' },
  // The dossier number, as large as on the desktop's panel.
  numberField: { flexDirection: 'row', alignItems: 'baseline', borderBottomWidth: 2, borderBottomColor: colors.accent, paddingBottom: 2 },
  // « 20AA- » while the year is not known, in the grey of a placeholder; then the year, as the number.
  numberYear: { fontFamily: fonts.headingBold, fontSize: 40, color: '#a5a9ae' },
  numberYearKnown: { color: colors.accent900 },
  // `minWidth`: a browser's field is as wide as twenty characters unless told otherwise, and would push the screen sideways.
  numberInput: { flex: 1, minWidth: 0, fontFamily: fonts.headingBold, fontSize: 40, letterSpacing: 0.8, color: colors.accent900, paddingVertical: 4 },
  found: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  foundText: { flex: 1, fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text },
  list: { gap: 6 },
  offer: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    minHeight: touch + 4,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  offerNumber: { fontSize: 16 },
  offerText: { flex: 1, fontFamily: fonts.body, fontSize: 14, color: colors.text },
  pressed: { backgroundColor: colors.accent100 },
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
  notes: { minHeight: 84, paddingTop: 10, textAlignVertical: 'top', fontFamily: fonts.body, fontSize: 16, borderColor: colors.line },
  tiles: { gap: 8 },
  tile: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: 54,
    paddingHorizontal: 14,
    borderWidth: 1,
    borderLeftWidth: 6,
    backgroundColor: colors.surface,
  },
  tileSwatch: { width: 12, height: 12 },
  tileText: { flex: 1, fontFamily: fonts.heading, fontSize: 18, letterSpacing: 0.4, color: colors.text },
  meRow: { minHeight: touch, justifyContent: 'center', paddingHorizontal: 12, borderWidth: 1, borderStyle: 'dashed', borderColor: colors.accent700 },
  meText: { fontFamily: fonts.heading, fontSize: 16, letterSpacing: 0.6, color: colors.accent800 },
  crew: { gap: 4 },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  who: {
    minWidth: 60,
    height: 48,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  whoOn: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  whoText: { fontFamily: fonts.headingBold, fontSize: 17, letterSpacing: 0.6, color: colors.subtle },
  whoTextOn: { color: colors.accent900 },
  card: { paddingHorizontal: 12, paddingVertical: 4, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  night: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  nightArrow: { width: touch + 4, height: 64, alignItems: 'center', justifyContent: 'center' },
  nightText: { flex: 1, alignItems: 'center', gap: 2 },
  nightDay: { fontFamily: fonts.bodySemiBold, fontSize: 16, color: colors.text },
  nightToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: 8,
    minHeight: touch,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  nightToggleOn: { borderColor: colors.accent900, backgroundColor: colors.accent900 },
  hours: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  nightToggleText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.5, color: colors.subtle },
  nightToggleTextOn: { color: '#ffffff' },
  recap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: touch + 2,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
  },
  recapLast: { borderBottomWidth: 0 },
  recapLabel: { width: 92 },
  recapValue: { flex: 1, fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text },
  warning: { fontFamily: fonts.body, fontSize: 14, color: colors.absenceInk },
  block: { gap: 6 },
  flags: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 8 },
  deadline: { minHeight: touch, justifyContent: 'center', paddingHorizontal: 12, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  deadlineOn: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  deadlineText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.5, color: colors.subtle },
  deadlineTextOn: { color: colors.accent900 },
  wheels: { flexDirection: 'row', marginVertical: 4 },
  foot: {
    flexDirection: 'row',
    gap: 10,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    backgroundColor: colors.bg,
  },
});
