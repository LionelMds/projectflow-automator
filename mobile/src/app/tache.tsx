import { useLocalSearchParams } from 'expo-router';
import { ChevronLeft, ChevronRight, FilePlus, FileSpreadsheet, FolderOpen, Plus, Table, Trash2, X } from 'lucide-react-native';
import { useEffect, useMemo, useState, type ComponentProps, type ReactNode } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ActionSheet, type SheetSection } from '@/components/ActionSheet';
import { Button } from '@/components/Button';
import { Notice } from '@/components/Notice';
import { Segmented, Stepper } from '@/components/Segmented';
import { Caps, Kicker, Muted } from '@/components/Text';
import { useDossierActions } from '@/lib/dossierActions';
import { selectionFeedback } from '@/lib/haptics';
import { close } from '@/lib/navigation';
import {
  assign,
  contextFor,
  deleteTask,
  moveTask,
  scheduleTask,
  setStatus,
  setTaskDays,
  taskDays,
  unassign,
  unscheduleTask,
  updateTask,
  type CommandContext,
  type TaskFields,
} from '@/lib/planning/commands';
import type { Change } from '@/lib/planning/documents';
import { describeDay, describeInstant, halfDayStart } from '@/lib/planning/display';
import { dossierCache, resolveDossierNumber } from '@/lib/planning/dossiers';
import { instantDay, minuteOfDay, parseInstant } from '@/lib/planning/instant';
import { formatDays, formatSpan, roundDays } from '@/lib/planning/quickEntry';
import { isScheduled, STATUS_LABELS, taskLabel, taskTypeOf, taskTypesOf, teamsOf, type TaskStatus } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import { todayNumber } from '@/lib/planning/walltime';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const STATUS_OPTIONS = (['provisional', 'confirmed', 'done'] as TaskStatus[]).map((value) => ({ value, label: STATUS_LABELS[value] }));
const HALF_OPTIONS = [
  { value: 'am', label: 'Matin' },
  { value: 'pm', label: 'Après-midi' },
] as const;

type TextField = 'title' | 'project_number' | 'notes';
type Picker = 'people' | 'type' | null;

/**
 * A task, laid out as one reads it: what (title, dossier, status), when, who, then the
 * details. Each change is written at once and can be undone from the bar at the bottom,
 * as on the board; texts are saved when leaving the field.
 */
export default function TacheScreen() {
  const params = useLocalSearchParams<{ id?: string }>();
  const { data, apply, author, editBlocker, waiting, error: loadError, undo, runUndo } = useLoadedPlanning();
  const task = data?.tasks.find((candidate) => candidate.id === params.id && !candidate.deleted_at) ?? null;
  const ctx = useMemo<CommandContext | null>(() => (data ? contextFor(data, author) : null), [data, author]);
  const dossier = useDossierActions();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [edits, setEdits] = useState<Partial<Record<TextField, string>>>({});
  const [cardDay, setCardDay] = useState<number | null>(null);
  const [cardAfternoon, setCardAfternoon] = useState(false);
  const [picker, setPicker] = useState<Picker>(null);
  const [opening, setOpening] = useState<string | null>(null);
  // The dossier's details come from the répertoire, read for the dossier's year if needed.
  const { projects, wantProjects } = useRepertoire();
  const wantedNumber = (task?.project_number ?? '').trim();
  useEffect(() => {
    if (wantedNumber) void wantProjects([wantedNumber]);
  }, [wantedNumber, wantProjects]);

  if (!task || !ctx || !data) {
    return (
      <Shell onClose={() => close('/planning')}>
        <View style={styles.content}>
          {waiting ? (
            <Muted>Chargement du planning…</Muted>
          ) : (
            <Notice
              tone={loadError ? 'error' : 'info'}
              message={loadError || "Cette tâche n'est plus dans le planning (supprimée ou actualisée entre-temps)."}
            />
          )}
        </View>
      </Shell>
    );
  }

  const run = async (build: () => Change[], undoLabel?: string, after?: () => void) => {
    setError('');
    setBusy(true);
    try {
      const changes = build();
      if (changes.length) await apply(changes, undoLabel);
      after?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Modification impossible.');
    } finally {
      setBusy(false);
    }
  };

  const locked = Boolean(editBlocker);
  const label = taskLabel(task);
  const scheduled = isScheduled(task);
  const start = parseInstant(task.start ?? '');
  const end = parseInstant(task.end ?? '');
  const assignedIds = Object.keys(task.assignments ?? {});
  const calendar = ctx.calendarFor(assignedIds[0] ?? null);
  const type = taskTypeOf(data.meta, task.task_type);
  const people = data.resources.filter((resource) => !resource.deleted_at && resource.active !== false);
  const number = (task.project_number ?? '').trim();
  // The dossier card of the desktop (`show_dossier`): the répertoire's details, else the task's copy.
  const project = number ? projects.get(number) : undefined;
  const cache = task.project_cache ?? {};
  const pick = (found?: string, copied?: string) => (found || copied || '').trim();
  const designation = pick(project?.designation, cache.designation);
  const client = pick(project?.client, cache.client);
  const contact = pick(project?.contact, cache.contact);
  const managedBy = pick(project?.managedBy, cache.managed_by);

  // Texts: what is typed until it is saved, else the task's; a task without a title of its
  // own shows its dossier's designation, which is not saved as long as it is left so.
  const stored = (field: TextField) => ((task[field] as string | undefined) ?? '').trim();
  const text = (field: TextField) => edits[field] ?? (field === 'title' ? stored('title') || designation : stored(field));
  const changedTexts = (Object.keys(edits) as TextField[]).filter((field) => {
    const value = (edits[field] ?? '').trim();
    if (field === 'title' && !stored('title') && value === designation) return false;
    if (field === 'project_number') return resolveDossierNumber(value, projects) !== stored('project_number');
    return value !== stored(field);
  });
  const clearEdits = (fields: TextField[]) =>
    setEdits((previous) => Object.fromEntries(Object.entries(previous).filter(([field]) => !fields.includes(field as TextField))));
  const saveTexts = async (fields: TextField[]) => {
    const pending = fields.filter((field) => changedTexts.includes(field));
    if (!pending.length) {
      clearEdits(fields);
      return;
    }
    const update: TaskFields = {};
    for (const field of pending) {
      if (field === 'project_number') {
        // « 5231 » is the dossier « …-5231 »; its details are copied from the répertoire (`_project_cache`).
        const typed = resolveDossierNumber(text('project_number'), projects);
        const directory = typed ? await wantProjects([typed]) : projects;
        const resolved = resolveDossierNumber(typed, directory);
        update.project_number = resolved;
        const info = directory.get(resolved);
        if (info) update.project_cache = dossierCache(info);
      } else update[field] = text(field).trim();
    }
    await run(() => updateTask(ctx, task.id, update), `Modifié : ${label}`, () => clearEdits(pending));
  };
  const onClose = async () => {
    if (changedTexts.length && !locked) await saveTexts(changedTexts);
    close('/planning');
  };

  // When: the day and half-day of the bar, its days; a card is placed on the next working day.
  const placeDay = cardDay ?? (calendar.isWorkday(todayNumber()) ? todayNumber() : calendar.nextWorkday(todayNumber()));
  const afternoon = start !== null && minuteOfDay(start) >= 12 * 60;
  const moveDay = (step: number) => {
    if (start === null) return;
    const day = instantDay(start);
    const target = step > 0 ? calendar.nextWorkday(day) : calendar.previousWorkday(day);
    void run(() => moveTask(ctx, task.id, { newStart: halfDayStart(calendar, target, afternoon) }), `Déplacé : ${label}`);
  };
  const setHalf = (pm: boolean) => {
    if (start === null) return;
    void run(() => moveTask(ctx, task.id, { newStart: halfDayStart(calendar, instantDay(start), pm) }), `Déplacé : ${label}`);
  };
  // Whole or half days of the bar, else its worked hours counted to the half day (9 h a day).
  const days = scheduled ? taskDays(ctx, task) : task.estimate_days || 0;
  const worked = scheduled && start !== null && end !== null ? calendar.workBetween(start, end) : 0;
  const stepBase = days || Math.max(0.5, roundDays(worked / 540));

  const openDossier = async (key: 'folder' | 'fiche' | 'repertoire' | 'creation') => {
    setError('');
    if (key !== 'fiche') {
      if (changedTexts.length && !locked) await saveTexts(changedTexts);
      dossier[key](number);
      return;
    }
    setOpening(key);
    try {
      await dossier[key](number);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Ouverture impossible.');
    } finally {
      setOpening(null);
    }
  };

  const teams = teamsOf(data.meta);
  const pickerSections: SheetSection[] =
    picker === 'people'
      ? teams
          .map((team) => ({ title: team.name, members: people.filter((person) => (person.team || 'atelier') === team.key) }))
          .concat([{ title: 'Autres', members: people.filter((person) => !teams.some((team) => team.key === (person.team || 'atelier'))) }])
          .filter((group) => group.members.length)
          .map((group) => ({
            title: group.title,
            actions: group.members.map((person) => {
              const on = assignedIds.includes(person.id);
              return {
                key: person.id,
                label: person.name || person.initials || person.id,
                detail: person.initials,
                selected: on,
                keepOpen: true,
                disabled: locked,
                onPress: async () => {
                  selectionFeedback();
                  const changes = on ? unassign(ctx, task.id, person.id) : assign(ctx, task.id, person.id);
                  if (changes.length) await apply(changes, `${on ? 'Retiré' : 'Ajouté'} ${person.initials || person.name} : ${label}`);
                },
              };
            }),
          }))
      : picker === 'type'
        ? [
            {
              actions: taskTypesOf(data.meta).map((kind) => ({
                key: kind.key,
                label: kind.label,
                selected: (task.task_type ?? '') === kind.key,
                disabled: locked,
                icon: () => <View style={[styles.swatch, { backgroundColor: kind.color }]} />,
                onPress:
                  (task.task_type ?? '') === kind.key
                    ? () => undefined
                    : async () => {
                        selectionFeedback();
                        await apply(updateTask(ctx, task.id, { task_type: kind.key }), `Type : ${label}`);
                      },
              })),
            },
          ]
        : [];

  return (
    <Shell onClose={() => void onClose()}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={[styles.band, { backgroundColor: type.color }]} />

          {/* What */}
          <View style={styles.head}>
            <Text style={styles.number}>{number || 'Sans dossier'}</Text>
            <TextInput
              accessibilityLabel="Désignation de la tâche"
              value={text('title')}
              placeholder="Désignation de la tâche"
              placeholderTextColor="#7a7f85"
              onChangeText={(value) => setEdits((previous) => ({ ...previous, title: value }))}
              onBlur={() => void saveTexts(['title'])}
              editable={!locked}
              multiline
              // The browser's text area starts at two rows; the phone's grows with the title.
              numberOfLines={Platform.OS === 'web' ? 1 : undefined}
              blurOnSubmit
              returnKeyType="done"
              style={styles.title}
            />
            {number ? (
              <>
                {text('title').trim() !== designation ? (
                  <Text style={styles.designation}>{designation ? `Dossier : ${designation}` : 'Désignation inconnue'}</Text>
                ) : null}
                {client || contact || managedBy ? (
                  <Muted>{[client, contact, managedBy ? `géré par ${managedBy}` : ''].filter(Boolean).join(' · ')}</Muted>
                ) : null}
              </>
            ) : (
              <Muted>Tâche interne : saisissez un numéro dans « Dossier » pour la rattacher à un dossier.</Muted>
            )}
          </View>

          <Segmented
            accessibilityLabel="Statut"
            options={STATUS_OPTIONS}
            value={(task.status ?? 'confirmed') as TaskStatus}
            disabled={locked || busy}
            onChange={(status) => {
              selectionFeedback();
              void run(() => setStatus(ctx, task.id, status), `Statut ${STATUS_LABELS[status]} : ${label}`);
            }}
          />

          {number ? (
            <View style={styles.tools}>
              {(
                [
                  ['folder', 'Dossier', FolderOpen],
                  ['fiche', 'Fiche', FileSpreadsheet],
                  ['repertoire', 'Répertoire', Table],
                  ['creation', 'Création', FilePlus],
                ] as const
              ).map(([key, caption, Icon]) => (
                <Pressable
                  key={key}
                  accessibilityRole="button"
                  accessibilityLabel={
                    { folder: 'Ouvrir le dossier', fiche: 'Ouvrir la fiche', repertoire: 'Voir dans Répertoire', creation: 'Charger dans Création' }[key]
                  }
                  onPress={() => void openDossier(key)}
                  style={({ pressed }) => [styles.tool, pressed && styles.pressed]}
                >
                  {opening === key ? <ActivityIndicator color={colors.accent700} /> : <Icon size={20} color={colors.accent700} strokeWidth={iconStroke} />}
                  <Text style={styles.toolText}>{caption}</Text>
                </Pressable>
              ))}
            </View>
          ) : null}

          {editBlocker ? <Notice message={editBlocker} /> : null}
          {error ? <Notice tone="error" message={error} /> : null}

          {/* When */}
          <Card title="Quand">
            {scheduled && start !== null && end !== null ? (
              task.off_hours ? (
                <>
                  <Text style={styles.strong}>Travail de nuit</Text>
                  <Muted>
                    {describeInstant(start)} → {describeInstant(end)}
                  </Muted>
                </>
              ) : (
                <>
                  <DayPicker
                    day={instantDay(start)}
                    disabled={locked || busy}
                    onPrevious={() => moveDay(-1)}
                    onNext={() => moveDay(1)}
                  />
                  <Segmented
                    accessibilityLabel="Demi-journée"
                    options={[...HALF_OPTIONS]}
                    value={afternoon ? 'pm' : 'am'}
                    disabled={locked || busy}
                    onChange={(half) => setHalf(half === 'pm')}
                  />
                  <View style={styles.line}>
                    <Caps style={styles.lineLabel}>Durée</Caps>
                    <View style={styles.flex}>
                      <Stepper
                        value={formatSpan(calendar, start, end)}
                        lessLabel="Une demi-journée de moins"
                        moreLabel="Une demi-journée de plus"
                        lessDisabled={locked || busy || (days > 0 && days <= 0.5)}
                        moreDisabled={locked || busy}
                        onLess={() => void run(() => setTaskDays(ctx, task.id, Math.max(0.5, stepBase - (days ? 0.5 : 0))), `Durée : ${label}`)}
                        onMore={() => void run(() => setTaskDays(ctx, task.id, stepBase + 0.5), `Durée : ${label}`)}
                      />
                    </View>
                  </View>
                  <Muted style={styles.small}>
                    {describeInstant(start)} → {describeInstant(end)}
                  </Muted>
                </>
              )
            ) : (
              <>
                <Muted>À planifier{task.estimate_days ? ` · ${formatDays(task.estimate_days)}` : ''}. Choisissez où la poser :</Muted>
                <DayPicker
                  day={placeDay}
                  disabled={locked}
                  onPrevious={() => setCardDay(calendar.previousWorkday(placeDay))}
                  onNext={() => setCardDay(calendar.nextWorkday(placeDay))}
                />
                <Segmented
                  accessibilityLabel="Demi-journée"
                  options={[...HALF_OPTIONS]}
                  value={cardAfternoon ? 'pm' : 'am'}
                  disabled={locked}
                  onChange={(half) => setCardAfternoon(half === 'pm')}
                />
                <Button
                  label="Placer sur le planning"
                  variant="primary"
                  busy={busy}
                  disabled={locked}
                  onPress={() =>
                    void run(() => scheduleTask(ctx, task.id, { start: halfDayStart(calendar, placeDay, cardAfternoon) }), `Planifié : ${label}`)
                  }
                />
              </>
            )}
            {scheduled ? (
              <LinkButton label="Renvoyer à planifier" disabled={locked || busy} onPress={() => void run(() => unscheduleTask(ctx, task.id), `Remis à planifier : ${label}`)} />
            ) : null}
          </Card>

          {/* Who */}
          <Card title={scheduled ? 'Qui' : 'Personnes pressenties'}>
            <View style={styles.people}>
              {assignedIds.map((id) => {
                const person = data.resources.find((resource) => resource.id === id);
                const initials = person?.initials || person?.name || id;
                return (
                  <Pressable
                    key={id}
                    accessibilityRole="button"
                    accessibilityLabel={`Retirer ${person?.name || initials}`}
                    disabled={locked || busy}
                    onPress={() => void run(() => unassign(ctx, task.id, id), `Retiré ${initials} : ${label}`)}
                    style={({ pressed }) => [styles.person, pressed && styles.pressed]}
                  >
                    <Text style={styles.personText}>{initials}</Text>
                    <X size={14} color={colors.accent700} strokeWidth={2.2} />
                  </Pressable>
                );
              })}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Ajouter ou retirer des personnes"
                disabled={locked}
                onPress={() => setPicker('people')}
                style={({ pressed }) => [styles.add, pressed && styles.pressed, locked && styles.disabled]}
              >
                <Plus size={16} color={colors.accent700} strokeWidth={2.2} />
                <Text style={styles.addText}>{assignedIds.length ? 'Modifier' : 'Ajouter'}</Text>
              </Pressable>
            </View>
            <View style={styles.line}>
              <Caps style={styles.lineLabel}>Requises</Caps>
              <View style={styles.flex}>
                <Stepper
                  value={`${task.required_people ?? 1} pers.`}
                  lessLabel="Une personne de moins"
                  moreLabel="Une personne de plus"
                  lessDisabled={locked || busy || (task.required_people ?? 1) <= 1}
                  moreDisabled={locked || busy}
                  onLess={() => void run(() => updateTask(ctx, task.id, { required_people: (task.required_people ?? 1) - 1 }), `Personnes requises : ${label}`)}
                  onMore={() => void run(() => updateTask(ctx, task.id, { required_people: (task.required_people ?? 1) + 1 }), `Personnes requises : ${label}`)}
                />
              </View>
            </View>
          </Card>

          {/* Details */}
          <Card title="Détails">
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Type : ${type.label}. Changer le type`}
              disabled={locked}
              onPress={() => setPicker('type')}
              style={({ pressed }) => [styles.typeRow, pressed && styles.pressed]}
            >
              <View style={[styles.swatch, { backgroundColor: type.color }]} />
              <Text style={styles.typeText} numberOfLines={1}>
                {type.label}
              </Text>
              <ChevronRight size={18} color={colors.muted} strokeWidth={iconStroke} />
            </Pressable>
            <LabeledInput
              label="Dossier"
              value={text('project_number')}
              placeholder="2026-4995"
              autoCapitalize="characters"
              editable={!locked}
              onChangeText={(value) => setEdits((previous) => ({ ...previous, project_number: value }))}
              onBlur={() => void saveTexts(['project_number'])}
            />
            <LabeledInput
              label="Notes"
              value={text('notes')}
              placeholder="Remarques pour l’atelier"
              multiline
              editable={!locked}
              onChangeText={(value) => setEdits((previous) => ({ ...previous, notes: value }))}
              onBlur={() => void saveTexts(['notes'])}
            />
            <View style={styles.switchRow}>
              <Text style={[styles.strong, styles.flex]}>Urgent</Text>
              <Switch
                accessibilityLabel="Urgent"
                value={Boolean(task.urgent)}
                disabled={locked || busy}
                onValueChange={(urgent) => void run(() => updateTask(ctx, task.id, { urgent }), `Urgence : ${label}`)}
                trackColor={{ true: colors.accent700, false: colors.line }}
              />
            </View>
          </Card>

          {task.updated_by?.initials || task.updated_by?.poste ? (
            <Muted style={styles.small}>
              Modifié par {[task.updated_by.initials || task.updated_by.name, task.updated_by.poste].filter(Boolean).join(' · ')}
              {task.updated_at ? ` le ${formatStamp(task.updated_at)}` : ''}
            </Muted>
          ) : null}

          <Pressable
            accessibilityRole="button"
            disabled={locked || busy}
            onPress={() => void run(() => deleteTask(ctx, task.id), `Tâche supprimée : ${label}`, () => close('/planning'))}
            style={({ pressed }) => [styles.delete, pressed && styles.deletePressed, (locked || busy) && styles.disabled]}
          >
            <Trash2 size={18} color={colors.err} strokeWidth={iconStroke} />
            <Text style={styles.deleteText}>Supprimer la tâche</Text>
          </Pressable>
          <Muted style={[styles.small, styles.center]}>Chaque modification est enregistrée aussitôt et peut être annulée.</Muted>
        </ScrollView>

        {undo ? (
          <View style={styles.toast}>
            <Text style={styles.toastText} numberOfLines={2}>
              {undo.label}
            </Text>
            <Pressable
              accessibilityRole="button"
              onPress={() =>
                void runUndo().catch((exc: unknown) => setError(exc instanceof Error ? exc.message : 'Annulation impossible.'))
              }
              style={styles.toastAction}
            >
              <Text style={styles.toastActionText}>Annuler</Text>
            </Pressable>
          </View>
        ) : null}
      </KeyboardAvoidingView>

      <ActionSheet
        visible={picker !== null}
        onClose={() => setPicker(null)}
        kicker={picker === 'people' ? 'Personnes' : 'Type de tâche'}
        title={label || 'Tâche'}
        sections={pickerSections}
        closeLabel={picker === 'people' ? 'Terminé' : 'Annuler'}
      />
    </Shell>
  );
}

function formatStamp(text: string): string {
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)} à ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function Shell({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={onClose} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Tâche</Kicker>
        <View style={styles.barButton} />
      </View>
      {children}
    </SafeAreaView>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <View style={styles.card}>
      <Caps style={styles.cardTitle}>{title}</Caps>
      {children}
    </View>
  );
}

function DayPicker({ day, disabled, onPrevious, onNext }: { day: number; disabled: boolean; onPrevious: () => void; onNext: () => void }) {
  const today = day === todayNumber();
  return (
    <View style={styles.dayPicker}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Jour ouvré précédent"
        disabled={disabled}
        onPress={onPrevious}
        style={({ pressed }) => [styles.dayArrow, pressed && styles.pressed, disabled && styles.disabled]}
      >
        <ChevronLeft size={22} color={colors.accent700} strokeWidth={iconStroke} />
      </Pressable>
      <View style={styles.dayValue}>
        <Text style={styles.dayText}>{describeDay(day)}</Text>
        {today ? <Text style={styles.today}>Aujourd’hui</Text> : null}
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Jour ouvré suivant"
        disabled={disabled}
        onPress={onNext}
        style={({ pressed }) => [styles.dayArrow, pressed && styles.pressed, disabled && styles.disabled]}
      >
        <ChevronRight size={22} color={colors.accent700} strokeWidth={iconStroke} />
      </Pressable>
    </View>
  );
}

function LabeledInput({
  label,
  ...input
}: ComponentProps<typeof TextInput> & { label: string }) {
  return (
    <View style={styles.labeled}>
      <Caps>{label}</Caps>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor="#7a7f85"
        autoCorrect={false}
        {...input}
        style={[styles.input, input.multiline && styles.inputMulti, input.editable === false && styles.disabled]}
      />
    </View>
  );
}

function LinkButton({ label, onPress, disabled }: { label: string; onPress: () => void; disabled?: boolean }) {
  return (
    <Pressable accessibilityRole="button" onPress={onPress} disabled={disabled} style={[styles.link, disabled && styles.disabled]}>
      <Text style={styles.linkText}>{label}</Text>
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
  barButton: { minWidth: 80, minHeight: touch, justifyContent: 'center', paddingHorizontal: 8 },
  barText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 1.1, textTransform: 'uppercase', color: colors.accent700 },
  barTitle: { color: colors.text },
  content: { paddingHorizontal: 16, paddingBottom: 96, gap: 12 },
  band: { height: 4, marginHorizontal: -16 },
  head: { gap: 2, paddingTop: 8 },
  number: { fontFamily: fonts.headingBold, fontSize: 17, letterSpacing: 0.6, color: colors.accent700 },
  designation: { fontFamily: fonts.bodySemiBold, fontSize: 15, color: colors.text },
  title: {
    fontFamily: fonts.headingBold,
    fontSize: 26,
    lineHeight: 30,
    textTransform: 'uppercase',
    color: colors.text,
    paddingVertical: 2,
    paddingHorizontal: 0,
  },
  tools: { flexDirection: 'row', borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  tool: {
    flex: 1,
    minHeight: 58,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    borderLeftWidth: StyleSheet.hairlineWidth,
    borderLeftColor: colors.line,
  },
  toolText: { fontFamily: fonts.heading, fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.accent700 },
  pressed: { backgroundColor: colors.accent100 },
  disabled: { opacity: 0.45 },
  card: { padding: 14, gap: 10, borderWidth: 1, borderColor: colors.line, backgroundColor: colors.surface },
  cardTitle: { color: colors.muted },
  strong: { fontFamily: fonts.bodySemiBold, fontSize: 16, color: colors.text },
  small: { fontSize: 13 },
  center: { textAlign: 'center' },
  line: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  lineLabel: { width: 70 },
  dayPicker: { flexDirection: 'row', alignItems: 'center', borderWidth: 1, borderColor: colors.line },
  dayArrow: { width: touch + 4, height: touch + 4, alignItems: 'center', justifyContent: 'center' },
  dayValue: { flex: 1, alignItems: 'center' },
  dayText: { fontFamily: fonts.headingBold, fontSize: 19, textTransform: 'capitalize', color: colors.text },
  today: { fontFamily: fonts.body, fontSize: 12, color: colors.accent700 },
  people: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  person: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: 38,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.accent100,
  },
  personText: { fontFamily: fonts.headingBold, fontSize: 16, letterSpacing: 0.6, color: colors.accent900 },
  add: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    minHeight: 38,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: colors.accent700,
  },
  addText: { fontFamily: fonts.heading, fontSize: 14, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.accent700 },
  typeRow: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: touch, paddingHorizontal: 10, borderWidth: 1, borderColor: colors.line },
  swatch: { width: 14, height: 14 },
  typeText: { flex: 1, fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.text },
  labeled: { gap: 4 },
  input: {
    minHeight: 44,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.bg,
    fontFamily: fonts.body,
    fontSize: 16,
    color: colors.text,
  },
  inputMulti: { minHeight: 80, paddingTop: 10, textAlignVertical: 'top' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: touch },
  link: { alignSelf: 'flex-start', minHeight: touch, justifyContent: 'center' },
  linkText: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.accent700, textDecorationLine: 'underline' },
  delete: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    minHeight: touch,
    marginTop: 4,
    borderWidth: 1,
    borderColor: colors.err,
  },
  deletePressed: { backgroundColor: colors.errSoft },
  deleteText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.err },
  toast: {
    position: 'absolute',
    left: 12,
    right: 12,
    bottom: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 12,
    backgroundColor: colors.accent900,
  },
  toastText: { flex: 1, fontFamily: fonts.body, fontSize: 14, color: '#ffffff' },
  toastAction: { minHeight: touch, justifyContent: 'center', paddingHorizontal: 14 },
  toastActionText: { fontFamily: fonts.headingBold, fontSize: 15, letterSpacing: 1, textTransform: 'uppercase', color: colors.accent200 },
});
