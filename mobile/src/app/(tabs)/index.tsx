import { Redirect, router, useLocalSearchParams } from 'expo-router';
import { ArrowRight } from 'lucide-react-native';
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentRef } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Chip, Choice } from '@/components/Choice';
import { Field } from '@/components/Field';
import { Journal, journalEntry, type JournalEntry } from '@/components/Journal';
import { Notice } from '@/components/Notice';
import { Screen } from '@/components/Screen';
import { StatusBanner, type BannerState } from '@/components/StatusBanner';
import { Body, Caps, Kicker, Muted, SectionHeading, Title } from '@/components/Text';
import { useAuth } from '@/lib/auth';
import { useProjectsBackend } from '@/lib/projects/backend';
import {
  clientSuggestions,
  createProject,
  errorMessage,
  EXISTING_QUESTION,
  loadProject,
  updateProject,
  type CreationDeps,
  type CreationForm,
  type CreationOutcome,
  type ExistingDecision,
  type PlannerFormData,
} from '@/lib/projects/creation';
import { DEMO_PLAN_ID } from '@/lib/demoProjects';
import type { DriveItem } from '@/lib/projects/drive';
import { numberText, parseProjectNumber } from '@/lib/projects/numbers';
import { openWorkbook } from '@/lib/projects/openFile';
import type { PlannerBucket, PlannerMember } from '@/lib/projects/planner';
import { useRecentDossiers } from '@/lib/recent/RecentContext';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import type { RepertoireRow } from '@/lib/repertoire/rows';
import { useRestriction } from '@/lib/restriction/RestrictionContext';
import { useSettings, type Settings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

type Busy = null | 'next' | 'load' | 'create' | 'update';
type TextField = 'year' | 'projectId' | 'subprojectId' | 'designation' | 'societe' | 'contact' | 'localisation' | 'gerePar';

function defaultPlanner(settings: Settings): PlannerFormData {
  return {
    enabled: false,
    bucketId: settings.plannerBucketId,
    bucketName: settings.plannerBucketName,
    assigneeIds: settings.plannerAssigneeIds,
    assigneeNames: settings.plannerAssigneeNames,
    dueEnabled: false,
    dueDays: settings.plannerDueDays > 0 ? settings.plannerDueDays : 7,
  };
}

function emptyForm(year: string, settings: Settings): CreationForm {
  return {
    year,
    projectId: '',
    subprojectId: '',
    designation: '',
    societe: '',
    contact: '',
    localisation: '',
    gerePar: '',
    planner: defaultPlanner(settings),
  };
}

function headline(form: CreationForm): string {
  if (!form.projectId.trim()) return 'Nouveau projet';
  const number = [form.year.trim(), form.projectId.trim(), form.subprojectId.trim()].filter(Boolean).join('-');
  return `${number} · ${form.designation.trim() || 'Sans désignation'}`;
}

/** « Accès limité » has no Création: the app then opens on the Explorer. */
export default function CreationTab() {
  const { restricted } = useRestriction();
  return restricted ? <Redirect href="/explorer" /> : <CreationScreen />;
}

function CreationScreen() {
  const { status } = useAuth();
  const { settings: stored, loaded } = useSettings();
  // The demo mode has its own plan: Planner can be tried without configuring it.
  const settings = useMemo<Settings>(
    () => (stored.demo ? { ...stored, plannerEnabled: true, plannerPlanId: DEMO_PLAN_ID } : stored),
    [stored],
  );
  const { peek, upsertProject } = useRepertoire();
  const { remember } = useRecentDossiers();
  const backend = useProjectsBackend();
  const scroll = useRef<ComponentRef<typeof ScrollView>>(null);

  const [form, setForm] = useState<CreationForm>(() => emptyForm(String(new Date().getFullYear()), settings));
  const [busy, setBusy] = useState<Busy>(null);
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [banner, setBanner] = useState<BannerState | null>(null);
  const [result, setResult] = useState<{ number: string; folder: DriveItem | null; fiche: DriveItem | null } | null>(null);
  const [question, setQuestion] = useState<((answer: ExistingDecision) => void) | null>(null);
  const [rows, setRows] = useState<{ year: string; rows: RepertoireRow[] } | null>(null);
  const [focused, setFocused] = useState<'societe' | 'contact' | null>(null);
  const [failed, setFailed] = useState<Busy>(null);

  // The settings are read at start-up, and may change: the Planner fields follow them.
  const [plannerDefaults, setPlannerDefaults] = useState(settings);
  if (plannerDefaults !== settings) {
    setPlannerDefaults(settings);
    setForm((previous) => ({ ...previous, planner: defaultPlanner(settings) }));
  }

  const log = useCallback((message: string) => setEntries((previous) => [...previous, journalEntry(message)]), []);

  const set = (field: TextField) => (value: string) => setForm((previous) => ({ ...previous, [field]: value }));
  const setPlanner = (patch: Partial<PlannerFormData>) =>
    setForm((previous) => ({ ...previous, planner: { ...previous.planner, ...patch } }));

  const connected = settings.demo || status === 'signedIn';
  const configured = settings.demo || (settings.racine !== '' && settings.reference !== '' && settings.repertoire !== '');
  const ready = loaded && connected && configured;

  const deps = useMemo<CreationDeps>(
    () => ({
      drive: backend.drive,
      planner: backend.planner,
      settings,
      repertoire: {
        peek,
        upsert: (year, project, options) => upsertProject(year, project, options),
      },
      log,
      confirmExisting: () => new Promise<ExistingDecision>((resolve) => setQuestion(() => resolve)),
    }),
    [backend, settings, peek, upsertProject, log],
  );

  const run = async (kind: Exclude<Busy, null>, progress: string, operation: () => Promise<void>) => {
    setBusy(kind);
    setFailed(null);
    setBanner({ kind: 'progress', message: progress });
    setResult(null);
    scroll.current?.scrollTo({ y: 0, animated: true });
    try {
      await operation();
    } catch (exc) {
      const message = errorMessage(exc);
      log(`! ${message}`);
      setBanner({ kind: 'error', message });
      setFailed(kind);
    } finally {
      setBusy(null);
      setQuestion(null);
    }
  };

  const showOutcome = (outcome: CreationOutcome) => {
    if (outcome.status === 'cancelled') {
      setBanner(null);
      return;
    }
    const title =
      outcome.status === 'created'
        ? `Projet ${outcome.number} créé`
        : outcome.status === 'updated'
          ? `Projet ${outcome.number} mis à jour`
          : `Projet ${outcome.number} prêt`;
    const detail = outcome.failures
      ? `${outcome.failures} étape${outcome.failures > 1 ? 's' : ''} en erreur : voir le journal.`
      : outcome.folderPath;
    setBanner({ kind: 'success', message: title, detail });
    setResult({ number: outcome.number, folder: outcome.folder, fiche: outcome.fiche });
    remember(outcome.number);
  };

  const rememberRows = (year: string, snapshotRows: RepertoireRow[]) => setRows({ year, rows: snapshotRows });

  const onNext = () =>
    void run('next', 'Lecture du répertoire…', async () => {
      const year = Number(form.year);
      if (!/^\d{4}$/.test(form.year.trim())) throw new Error("L'année doit contenir exactement 4 chiffres.");
      const snapshot = await peek(year);
      rememberRows(form.year, snapshot.rows);
      const next = snapshot.nextAvailable;
      setBanner(null);
      if (!next) {
        log('! Aucun numéro disponible trouvé');
        return;
      }
      const number = parseProjectNumber(next.number);
      setForm((previous) => ({ ...previous, year: String(number.year), projectId: number.projectId, subprojectId: '' }));
      log(`+ Numéro disponible : ${next.number}`);
    });

  /** « Charger »: the typed number, or a dossier asked from the répertoire or the planning. */
  const loadNumber = (typed: { year: string; projectId: string; subprojectId: string } | string) =>
    void run('load', 'Chargement du projet…', async () => {
      let year: string;
      let projectId: string;
      let subprojectId: string;
      if (typeof typed === 'string') {
        const asked = parseProjectNumber(typed);
        [year, projectId, subprojectId] = [String(asked.year), asked.projectId, asked.subprojectId ?? ''];
      } else ({ year, projectId, subprojectId } = typed);
      // The number shows at once, even when the dossier cannot be read.
      setForm((previous) => ({ ...previous, year, projectId, subprojectId }));
      const loadedProject = await loadProject(deps, year, projectId, subprojectId);
      const number = loadedProject.number;
      setForm((previous) => ({
        ...previous,
        year: String(number.year),
        projectId: number.projectId,
        subprojectId: number.subprojectId ?? '',
        designation: loadedProject.designation,
        societe: loadedProject.societe,
        contact: loadedProject.contact,
        localisation: loadedProject.localisation,
        gerePar: loadedProject.gerePar,
      }));
      setBanner({ kind: 'success', message: `Projet ${numberText(number)} chargé`, detail: loadedProject.folderPath });
      setResult({ number: numberText(number), folder: loadedProject.folder, fiche: loadedProject.fiche });
      remember(numberText(number));
    });
  const onLoad = () => loadNumber({ year: form.year, projectId: form.projectId, subprojectId: form.subprojectId });

  // « Charger dans Création » from the répertoire or the planning: the dossier loaded at once.
  const params = useLocalSearchParams<{ charger?: string; at?: string }>();
  const request = params.charger ? `${params.charger}@${params.at ?? ''}` : '';
  const handledRequest = useRef('');
  useEffect(() => {
    if (!request || request === handledRequest.current || !ready || busy) return;
    handledRequest.current = request;
    loadNumber(params.charger ?? '');
    // Run once per request: `loadNumber` changes at every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, ready, busy]);

  const onCreate = () =>
    void run('create', `Création en cours — ${headline(form).split(' · ')[0]}`, async () => {
      showOutcome(await createProject(deps, form));
    });

  const onUpdate = () =>
    void run('update', `Mise à jour en cours — ${headline(form).split(' · ')[0]}`, async () => {
      showOutcome(await updateProject(deps, form));
    });

  /** « Réinitialiser » of the desktop (`reset_form_fields`): every field but the year, and the banner. */
  const onReset = () => {
    setForm((previous) => emptyForm(previous.year, settings));
    setBanner(null);
    setResult(null);
    setFailed(null);
  };

  /** « Ouvrir dossier »: the dossier's explorer; « Ouvrir fiche »: the fiche in Excel. */
  const openFolder = (number: string) => router.push({ pathname: '/dossier', params: { number } });
  const openFiche = (fiche: DriveItem | null) => {
    if (!fiche) {
      log('! Fiche introuvable');
      return;
    }
    openWorkbook(fiche).catch((exc: unknown) => log(`! ${errorMessage(exc)}`));
  };

  // Suggestions Société / Contact from the répertoire rows of the year, read once on focus.
  const onFocusClient = (field: 'societe' | 'contact') => {
    setFocused(field);
    if (rows?.year === form.year || !ready || !/^\d{4}$/.test(form.year.trim())) return;
    const year = form.year;
    peek(Number(year))
      .then((snapshot) => rememberRows(year, snapshot.rows))
      .catch((exc) => log(`! Suggestions Société/Contact indisponibles : ${errorMessage(exc)}`));
  };
  // Late, so that a tap on a suggestion lands before it disappears (web).
  const onBlurClient = (field: 'societe' | 'contact') =>
    setTimeout(() => setFocused((current) => (current === field ? null : current)), 200);
  const suggestions =
    focused && rows?.year === form.year
      ? clientSuggestions(rows.rows, focused, focused === 'societe' ? form.societe : form.contact, form.societe)
      : [];

  const working = busy !== null;
  const notice = !loaded
    ? null
    : !connected
      ? 'Connectez-vous à Microsoft pour créer un projet, ou activez le mode démo dans les paramètres.'
      : !configured
        ? 'Collez les liens de la racine projets, du dossier de référence et du répertoire chantier dans les paramètres.'
        : null;

  return (
    <Screen>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView ref={scroll} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.titles}>
            <View style={styles.kickerRow}>
              <Kicker style={styles.flex}>Création projet</Kicker>
              <Pressable accessibilityRole="button" onPress={onReset} disabled={working} style={styles.reset}>
                <Text style={[styles.resetText, working && styles.resetDisabled]}>Réinitialiser</Text>
              </Pressable>
            </View>
            <Title numberOfLines={3}>{headline(form)}</Title>
          </View>

          {notice ? (
            <Notice message={notice} action={{ label: 'Ouvrir les paramètres', onPress: () => router.push('/parametres') }} />
          ) : null}

          {banner ? (
            <StatusBanner
              state={banner}
              actions={
                banner.kind === 'success' && result
                  ? [
                      { label: 'Ouvrir dossier', onPress: () => openFolder(result.number) },
                      { label: 'Ouvrir fiche', onPress: () => openFiche(result.fiche) },
                    ]
                  : banner.kind === 'error' && busy === null && failed
                    ? [{ label: 'Réessayer', onPress: { next: onNext, load: onLoad, create: onCreate, update: onUpdate }[failed] }]
                    : []
              }
            />
          ) : null}

          <Blueprint style={styles.section}>
            <SectionHeading number="01">Identification</SectionHeading>
            <View style={styles.identity}>
              <View style={styles.yearField}>
                <Field label="Année" value={form.year} onChangeText={set('year')} keyboardType="number-pad" maxLength={4} />
              </View>
              <View style={styles.flex}>
                <NumberField value={form.projectId} onChangeText={set('projectId')} />
              </View>
              <View style={styles.subField}>
                <Field
                  label="Sous-projet"
                  value={form.subprojectId}
                  onChangeText={set('subprojectId')}
                  placeholder="Optionnel"
                  keyboardType="number-pad"
                />
              </View>
            </View>
            <Button
              label="Suivant disponible"
              icon={<ArrowRight size={18} color={colors.accent700} strokeWidth={iconStroke} />}
              onPress={onNext}
              busy={busy === 'next'}
              disabled={working || !ready}
              style={styles.start}
            />
            <Field label="Désignation" value={form.designation} onChangeText={set('designation')} placeholder="Ouvrage, bâtiment, lot…" />
          </Blueprint>

          <Blueprint style={styles.section}>
            <View style={styles.headingRow}>
              <View style={styles.flex}>
                <SectionHeading number="02">Client</SectionHeading>
              </View>
              <Muted style={styles.small}>Suggestions depuis le répertoire</Muted>
            </View>
            <Field
              label="Société"
              value={form.societe}
              onChangeText={set('societe')}
              placeholder="Société"
              onFocus={() => onFocusClient('societe')}
              onBlur={() => onBlurClient('societe')}
            />
            {focused === 'societe' && suggestions.length ? (
              <Suggestions values={suggestions} onPick={(value) => setForm((previous) => ({ ...previous, societe: value }))} />
            ) : null}
            <Field
              label="Contact"
              value={form.contact}
              onChangeText={set('contact')}
              placeholder="Contact"
              onFocus={() => onFocusClient('contact')}
              onBlur={() => onBlurClient('contact')}
            />
            {focused === 'contact' && suggestions.length ? (
              <Suggestions values={suggestions} onPick={(value) => setForm((previous) => ({ ...previous, contact: value }))} />
            ) : null}
            <Field label="Localisation" value={form.localisation} onChangeText={set('localisation')} placeholder="Adresse du chantier" />
            <View style={styles.pair}>
              <View style={styles.flex}>
                <Field label="Géré par · C6" value={form.gerePar} onChangeText={set('gerePar')} autoCapitalize="characters" />
              </View>
              <View style={[styles.flex, styles.initialsField]}>
                <Caps>Initiales utilisateur · C9</Caps>
                <View style={styles.initialsRow}>
                  <Text style={styles.initials}>{settings.initials || '—'}</Text>
                  <Pressable
                    accessibilityRole="link"
                    accessibilityLabel="Modifier les initiales dans les paramètres"
                    onPress={() => router.push('/parametres')}
                    style={styles.link}
                  >
                    <Text style={styles.linkText}>Paramètres</Text>
                  </Pressable>
                </View>
              </View>
            </View>
          </Blueprint>

          <PlannerSection
            settings={settings}
            planner={form.planner}
            onChange={setPlanner}
            backend={backend}
            disabled={working}
          />

          <Blueprint style={styles.section}>
            <SectionHeading number="04">Fichiers CAO</SectionHeading>
            <Choice
              label="Ajouter arborescence SolidWorks"
              detail="SolidWorks ne tourne pas sur un téléphone : à ajouter depuis le poste Windows."
              checked={false}
              disabled
            />
            <Choice
              label="Ajouter modèle AutoCAD"
              detail="À ajouter depuis le poste, avec « Mettre à jour » et la case cochée."
              checked={false}
              disabled
            />
            <View style={styles.rule} />
            <View style={styles.outlook}>
              <Body style={styles.outlookTitle}>Dossier Outlook</Body>
              <Muted style={styles.small}>
                Créé par ProjectFlow sur le poste (Outlook local) : le téléphone ne crée pas de dossier Outlook.
              </Muted>
            </View>
          </Blueprint>

          <Journal entries={entries} empty="Aucune opération. Chaque étape de la création s'affichera ici." />
        </ScrollView>

        {question ? (
          <View style={styles.question} accessibilityViewIsModal>
            <Text accessibilityRole="header" style={styles.questionTitle}>
              {EXISTING_QUESTION.title}
            </Text>
            <Body style={styles.questionText}>{EXISTING_QUESTION.message}</Body>
            <Muted style={styles.small}>{EXISTING_QUESTION.yes}</Muted>
            <Muted style={styles.small}>{EXISTING_QUESTION.no}</Muted>
            <View style={styles.questionActions}>
              <Button label="Annuler" onPress={() => question('cancel')} style={styles.flex} />
              <Button label="Non" onPress={() => question('no')} style={styles.flex} />
              <Button label="Oui" variant="primary" onPress={() => question('yes')} style={styles.flex} />
            </View>
          </View>
        ) : null}

        <View style={styles.footer}>
          <Button label="Charger" onPress={onLoad} busy={busy === 'load'} disabled={working || !ready} style={styles.flex} />
          <Button label="Mettre à jour" onPress={onUpdate} busy={busy === 'update'} disabled={working || !ready} style={styles.wide} />
          <Button label="Créer" variant="primary" onPress={onCreate} busy={busy === 'create'} disabled={working || !ready} style={styles.flex} />
        </View>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function NumberField({ value, onChangeText }: { value: string; onChangeText: (value: string) => void }) {
  return (
    <View style={styles.numberField}>
      <Caps>Numéro</Caps>
      <TextInput
        accessibilityLabel="Numéro"
        value={value}
        onChangeText={onChangeText}
        placeholder="4995"
        placeholderTextColor="#7a7f85"
        keyboardType="number-pad"
        style={styles.numberInput}
      />
    </View>
  );
}

function Suggestions({ values, onPick }: { values: string[]; onPick: (value: string) => void }) {
  return (
    <View style={styles.chips} accessibilityLabel="Suggestions du répertoire">
      {values.map((value) => (
        <Chip key={value} label={value} onPress={() => onPick(value)} />
      ))}
    </View>
  );
}

function PlannerSection({
  settings,
  planner,
  onChange,
  backend,
  disabled,
}: {
  settings: Settings;
  planner: PlannerFormData;
  onChange: (patch: Partial<PlannerFormData>) => void;
  backend: ReturnType<typeof useProjectsBackend>;
  disabled: boolean;
}) {
  const available = settings.plannerEnabled;
  const [options, setOptions] = useState<{ buckets: PlannerBucket[]; members: PlannerMember[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [choosing, setChoosing] = useState(false);

  // Another plan (or the demo mode switched): its columns and members are read again.
  const source = `${backend.demo}|${settings.plannerPlanId}`;
  const [loadedFor, setLoadedFor] = useState(source);
  if (loadedFor !== source) {
    setLoadedFor(source);
    setOptions(null);
    setError('');
  }

  const loadOptions = async () => {
    if (loading || options) return;
    setLoading(true);
    setError('');
    try {
      const planId = backend.demo ? 'demo-plan' : settings.plannerPlanId;
      const [buckets, members] = await Promise.all([backend.planner.buckets(planId), backend.planner.members(planId)]);
      setOptions({ buckets, members });
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setLoading(false);
    }
  };

  const active = available && planner.enabled && !disabled;
  const buckets = options?.buckets ?? (planner.bucketId ? [{ id: planner.bucketId, name: planner.bucketName || planner.bucketId }] : []);
  const memberLabel = loading
    ? 'Chargement Planner…'
    : planner.assigneeIds.length
      ? planner.assigneeIds
          .map((id, index) => options?.members.find((member) => member.id === id)?.name || planner.assigneeNames[index] || id)
          .join(', ')
      : 'Utilisateur connecté';

  const toggleMember = (member: PlannerMember) => {
    const selected = planner.assigneeIds.includes(member.id);
    const ids = selected ? planner.assigneeIds.filter((id) => id !== member.id) : [...planner.assigneeIds, member.id];
    const names = ids.map((id) => options?.members.find((candidate) => candidate.id === id)?.name ?? '');
    onChange({ assigneeIds: ids, assigneeNames: names });
  };

  return (
    <Blueprint style={styles.section}>
      <SectionHeading number="03">Microsoft Planner</SectionHeading>
      <Choice
        label="Créer une tâche Planner"
        detail={available ? undefined : 'Désactivé dans les paramètres'}
        checked={available && planner.enabled}
        disabled={!available || disabled}
        onPress={() => {
          const enabled = !planner.enabled;
          onChange({ enabled });
          if (enabled) void loadOptions();
        }}
      />
      <View style={[styles.plannerDetails, !active && styles.dimmed]} pointerEvents={active ? 'auto' : 'none'}>
        <View style={styles.subsection}>
          <Caps>Colonne</Caps>
          {buckets.length ? (
            <View style={styles.chips}>
              {buckets.map((bucket) => (
                <Chip
                  key={bucket.id}
                  label={bucket.name}
                  selected={bucket.id === planner.bucketId}
                  onPress={() => onChange({ bucketId: bucket.id, bucketName: bucket.name })}
                />
              ))}
            </View>
          ) : (
            <Muted style={styles.small}>{loading ? 'Chargement Planner…' : 'Colonne des paramètres'}</Muted>
          )}
        </View>
        <View style={styles.subsection}>
          <Caps>Membres</Caps>
          <View style={styles.membersRow}>
            <Body style={styles.flex} numberOfLines={2}>
              {memberLabel}
            </Body>
            <Button
              label={choosing ? 'Fermer' : 'Choisir'}
              onPress={() => {
                setChoosing((previous) => !previous);
                void loadOptions();
              }}
              disabled={loading}
            />
          </View>
          {choosing && options ? (
            options.members.length ? (
              <View style={styles.chips}>
                {options.members.map((member) => (
                  <Chip
                    key={member.id}
                    label={member.name || member.email || member.id}
                    selected={planner.assigneeIds.includes(member.id)}
                    onPress={() => toggleMember(member)}
                  />
                ))}
              </View>
            ) : (
              <Muted style={styles.small}>Aucun membre Planner disponible pour le plan sélectionné.</Muted>
            )
          ) : null}
        </View>
        <View style={styles.dueRow}>
          <Choice
            label="Échéance"
            checked={planner.dueEnabled}
            onPress={() => onChange({ dueEnabled: !planner.dueEnabled })}
            style={styles.flex}
          />
          <TextInput
            accessibilityLabel="Jours d'échéance"
            value={String(planner.dueDays)}
            onChangeText={(value) => {
              const days = Number(value.replace(/\D/g, '') || '0');
              onChange({ dueDays: Math.min(365, days) });
            }}
            editable={planner.dueEnabled}
            keyboardType="number-pad"
            maxLength={3}
            style={[styles.dueInput, !planner.dueEnabled && styles.dimmed]}
          />
          <Muted>jours</Muted>
        </View>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
    </Blueprint>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  wide: { flex: 1.45, paddingHorizontal: 8 },
  content: { padding: 20, gap: 18, paddingBottom: 32 },
  titles: { gap: 2 },
  kickerRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  reset: { minHeight: touch, justifyContent: 'center', paddingLeft: 8 },
  resetText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.accent700, textDecorationLine: 'underline' },
  resetDisabled: { opacity: 0.4 },
  section: { padding: 16, gap: 12 },
  identity: { flexDirection: 'row', gap: 10 },
  yearField: { width: 84 },
  subField: { width: 96 },
  numberField: { gap: 4 },
  numberInput: {
    minHeight: 44,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    fontFamily: fonts.heading,
    fontSize: 18,
    letterSpacing: 0.7,
    color: colors.accent700,
  },
  start: { alignSelf: 'flex-start' },
  headingRow: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  small: { fontSize: 13 },
  pair: { flexDirection: 'row', gap: 10 },
  initialsField: { gap: 4 },
  initialsRow: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8 },
  initials: {
    minWidth: 34,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: colors.accent,
    textAlign: 'center',
    fontFamily: fonts.headingBold,
    fontSize: 15,
    letterSpacing: 0.8,
    color: colors.accent700,
  },
  link: { minHeight: touch, justifyContent: 'center', paddingHorizontal: 4 },
  linkText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.accent700, textDecorationLine: 'underline' },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  plannerDetails: { gap: 12 },
  dimmed: { opacity: 0.45 },
  subsection: { gap: 6 },
  membersRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dueRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dueInput: {
    width: 64,
    minHeight: 44,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    textAlign: 'right',
    fontFamily: fonts.body,
    fontSize: 16,
    color: colors.text,
  },
  rule: { height: 1, backgroundColor: colors.line },
  outlook: { gap: 2 },
  outlookTitle: { fontFamily: fonts.bodyMedium },
  error: { fontFamily: fonts.body, fontSize: 14, color: colors.err },
  question: {
    marginHorizontal: 12,
    marginBottom: 8,
    padding: 14,
    gap: 6,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
  },
  questionTitle: { fontFamily: fonts.headingBold, fontSize: 18, textTransform: 'uppercase', color: colors.text },
  questionText: { fontSize: 15 },
  questionActions: { flexDirection: 'row', gap: 8, paddingTop: 6 },
  footer: {
    flexDirection: 'row',
    gap: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: colors.line,
    backgroundColor: colors.bg,
  },
});
