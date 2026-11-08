import { useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Chip';
import { Notice } from '@/components/Notice';
import { Caps, DossierNumber, Kicker, Muted } from '@/components/Text';
import { close } from '@/lib/navigation';
import { calendarResolver, contextFor, createAbsence, createTask } from '@/lib/planning/commands';
import type { Change } from '@/lib/planning/documents';
import { dossierCache } from '@/lib/planning/dossiers';
import { describeInstant } from '@/lib/planning/display';
import {
  dossierSuggestions,
  hasToken,
  replaceTypedWord,
  SHORTCUT_DAYS,
  SHORTCUT_DURATIONS,
  toggleToken,
  type ShortcutKind,
} from '@/lib/planning/entryShortcuts';
import { instantDay, minuteOfDay } from '@/lib/planning/instant';
import { ABSENCE_LABELS, taskTypeOf, teamsOf, taskTypesOf, type PlanningData } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import { DAY_ABBREVIATIONS, parseEntry, type EntryContext, type ParsedEntry } from '@/lib/planning/quickEntry';
import { UNASSIGNED } from '@/lib/planning/week';
import { dateParts, mondayOf, parseDate, todayNumber, weekday } from '@/lib/planning/walltime';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, touch } from '@/theme/tokens';

const NO_PERSON_ABSENCE = 'Absence sans personne : indiquez les initiales (ex. /vac KDU 26-30.10).';
const UNREADABLE_ENTRY = 'Saisie non comprise.';

function capitalized(text: string): string {
  return text.slice(0, 1).toUpperCase() + text.slice(1);
}

function shortDate(day: number): string {
  const { date, month } = dateParts(day);
  return `${String(date).padStart(2, '0')}.${String(month).padStart(2, '0')}`;
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
    await createFrom(parsed, keep);
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
          requiredPeople: entry.requiredPeople,
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
  const suggestions = entry?.projectNumber ? [] : dossierSuggestions(text, projects.values());
  const activePeople = (data?.resources ?? []).filter((resource) => !resource.deleted_at && resource.active !== false && resource.initials);
  const toggle = (token: string, kind: ShortcutKind) => {
    setText((current) => toggleToken(current, token, kind));
    setError('');
  };
  const destination = entry ? destinationText(entry, label) : '';

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/planning')} style={styles.barButton}>
          <Text style={styles.barText}>Annuler</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Saisie rapide</Kicker>
        <View style={styles.barButton} />
      </View>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {cellStart !== null ? (
            <Muted>
              Sur la ligne {cellRow ? names.get(cellRow) : 'Non affecté'} · {describeInstant(cellStart)}
              {minuteOfDay(cellStart) >= 12 * 60 ? ' (après-midi)' : ''}
            </Muted>
          ) : null}
          <TextInput
            accessibilityLabel="Saisie rapide"
            value={text}
            onChangeText={(value) => {
              setText(value);
              setError('');
            }}
            placeholder="5252 relevé lun LM"
            placeholderTextColor="#7a7f85"
            autoFocus
            autoCapitalize="none"
            autoCorrect={false}
            returnKeyType="done"
            onSubmitEditing={() => void create(false)}
            style={styles.input}
          />

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

          <View style={styles.shortcuts}>
            {activePeople.length ? (
              <ShortcutRow label="Qui">
                {activePeople.map((person) => (
                  <Chip
                    key={person.id}
                    label={person.initials ?? ''}
                    hint={person.name}
                    on={hasToken(text, person.initials ?? '')}
                    onPress={() => toggle(person.initials ?? '', 'person')}
                  />
                ))}
              </ShortcutRow>
            ) : null}
            <ShortcutRow label="Quand">
              {SHORTCUT_DAYS.map((day) => (
                <Chip key={day} label={day} on={hasToken(text, day)} onPress={() => toggle(day, 'day')} />
              ))}
            </ShortcutRow>
            <ShortcutRow label="Durée">
              {SHORTCUT_DURATIONS.map((duration) => (
                <Chip key={duration} label={duration} on={hasToken(text, duration)} onPress={() => toggle(duration, 'duration')} />
              ))}
            </ShortcutRow>
            <ShortcutRow label="Statut">
              <Chip label="? Provisoire" on={hasToken(text, '?')} onPress={() => toggle('?', 'flag')} />
              <Chip label="! Urgent" on={hasToken(text, '!')} onPress={() => toggle('!', 'flag')} />
            </ShortcutRow>
          </View>

          {waiting ? <Muted>Chargement du planning…</Muted> : null}
          {loadError ? <Notice tone="error" message={loadError} /> : null}
          {editBlocker ? <Notice message={editBlocker} /> : null}
          {done ? <Notice message={done} /> : null}
          {error ? <Notice tone="error" message={error} /> : null}

          {entry ? (
            <Blueprint style={styles.preview}>
              {entry.destination === 'absence' && entry.absence ? (
                <>
                  <Text style={styles.previewTitle}>{ABSENCE_LABELS[entry.absence.reason] ?? entry.absence.reason}</Text>
                  <Muted>{entry.interpretation}</Muted>
                </>
              ) : (
                <>
                  <View style={styles.previewHead}>
                    {entry.projectNumber ? <DossierNumber style={styles.previewNumber}>{entry.projectNumber}</DossierNumber> : null}
                    <Text style={styles.previewTitle} numberOfLines={2}>
                      {capitalized(entry.title.trim()) || entry.project?.designation || (entry.projectNumber ? '' : 'Sans titre')}
                    </Text>
                  </View>
                  {type ? (
                    <View style={styles.typeRow}>
                      <View style={[styles.swatch, { backgroundColor: type.color }]} />
                      <Caps>{type.label}</Caps>
                    </View>
                  ) : null}
                  <Text style={styles.when}>
                    {entry.start !== null && entry.end !== null
                      ? `${describeInstant(entry.start)} → ${describeInstant(entry.end)}`
                      : 'À planifier'}
                  </Text>
                  {entry.resourceIds.length ? (
                    <View style={styles.people}>
                      {entry.resourceIds.map((id) => (
                        <Text key={id} style={styles.person}>
                          {names.get(id) ?? id}
                        </Text>
                      ))}
                    </View>
                  ) : null}
                  <Muted>{entry.interpretation}</Muted>
                </>
              )}
              {destination ? <Text style={styles.destination}>{destination}</Text> : null}
              {[...entry.warnings, ...(data ? absenceWarnings(entry, data, names) : [])].map((warning) => (
                <Text key={warning} style={styles.warning}>
                  ▲ {warning}
                </Text>
              ))}
            </Blueprint>
          ) : null}

          <View style={styles.syntax}>
            <Muted style={styles.small}>
              Tout se tape aussi : heures 7h30-11h, 4h, 2p personnes requises, /vac LBI 26.10-30.10 pour une absence. Sans jour, la
              tâche va dans « À planifier ».
            </Muted>
          </View>

          <View style={styles.actions}>
            <Button
              label="Créer et garder"
              disabled={!entry || busy || Boolean(editBlocker)}
              onPress={() => void create(true)}
              style={styles.flex}
            />
            <Button
              label="Créer"
              variant="primary"
              busy={busy}
              disabled={!entry || Boolean(editBlocker)}
              onPress={() => void create(false)}
              style={styles.flex}
            />
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

/** A row of shortcuts: its caption, then chips to scroll sideways. */
function ShortcutRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <View style={styles.shortcutRow}>
      <Caps style={styles.shortcutLabel}>{label}</Caps>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" contentContainerStyle={styles.chips}>
        {children}
      </ScrollView>
    </View>
  );
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
  content: { padding: 20, gap: 14, paddingBottom: 40 },
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
  preview: { padding: 14, gap: 6 },
  previewHead: { gap: 2 },
  previewNumber: { fontSize: 16 },
  previewTitle: { fontFamily: fonts.headingBold, fontSize: 20, color: colors.text },
  typeRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  swatch: { width: 10, height: 10 },
  when: { fontFamily: fonts.bodySemiBold, fontSize: 15, color: colors.text },
  people: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  person: {
    fontFamily: fonts.headingBold,
    fontSize: 14,
    letterSpacing: 0.5,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: colors.accent700,
    color: colors.accent900,
    backgroundColor: colors.accent100,
  },
  destination: { fontFamily: fonts.body, fontSize: 14, color: colors.accent800 },
  warning: { fontFamily: fonts.body, fontSize: 14, color: colors.absenceInk },
  syntax: { gap: 4 },
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
  shortcuts: { gap: 8 },
  shortcutRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  shortcutLabel: { width: 52 },
  chips: { gap: 6, paddingRight: 12 },
  actions: { flexDirection: 'row', gap: 10 },
});
