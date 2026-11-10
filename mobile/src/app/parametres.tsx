import { useEffect, useMemo, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Chip } from '@/components/Choice';
import { Field } from '@/components/Field';
import { MicrosoftSignIn } from '@/components/MicrosoftSignIn';
import { Body, Caps, Kicker, Muted, SectionHeading, Title } from '@/components/Text';
import { useAuth } from '@/lib/auth';
import { COMPANY_LABELS, companySettingsFor, emptyCompanyFields, type CompanyField } from '@/lib/companySettings';
import { DEMO_PLAN_ID, demoPlanner } from '@/lib/demoProjects';
import { close } from '@/lib/navigation';
import { usePlanning } from '@/lib/planning/PlanningContext';
import { errorMessage } from '@/lib/projects/creation';
import { graphDrive } from '@/lib/projects/drive';
import { RACINE_LABEL, REFERENCE_LABEL } from '@/lib/projects/location';
import { graphPlanner, plannerPlanIdFromText, type PlannerBucket, type PlannerMember } from '@/lib/projects/planner';
import { resolveWorkbook } from '@/lib/repertoire/workbook';
import { useRestriction } from '@/lib/restriction/RestrictionContext';
import { useSettings, type Settings } from '@/lib/settings';
import { planningTeams } from '@/lib/widgets/planningWidget';
import { colors, fonts, touch } from '@/theme/tokens';

export default function ParametresScreen() {
  const { status, account, error, canSignIn, busy, signInPage, signIn, finishSignIn, cancelSignIn, signOut, getAccessToken } =
    useAuth();
  const { settings, loaded, save } = useSettings();
  // « Accès limité »: only the account, the widget and the password stay here.
  const { restricted } = useRestriction();
  // Untouched fields follow the stored settings (read asynchronously at start-up).
  const [edits, setEdits] = useState<Partial<Settings>>({});
  const draft: Settings = { ...settings, ...edits };
  const [saving, setSaving] = useState(false);

  // The plan link and the days are kept as typed until « Enregistrer ».
  const [planText, setPlanText] = useState<string | null>(null);
  const [dueText, setDueText] = useState<string | null>(null);
  const [saveError, setSaveError] = useState('');
  const [plannerOptions, setPlannerOptions] = useState<{ buckets: PlannerBucket[]; members: PlannerMember[] } | null>(null);
  const [plannerLoading, setPlannerLoading] = useState(false);
  const [plannerError, setPlannerError] = useState('');
  const planner = useMemo(() => (draft.demo ? demoPlanner() : graphPlanner(getAccessToken)), [draft.demo, getAccessToken]);

  // The company's settings, once the sign-in is done: each one checked with that account
  // first, only those still empty, once per account.
  const [companySetup, setCompanySetup] = useState<
    { state: 'checking' } | { state: 'done'; filled: CompanyField[]; missing: CompanyField[] } | null
  >(null);
  const checkedFor = useRef('');
  useEffect(() => {
    if (!loaded || settings.demo || status !== 'signedIn') return;
    const who = account?.username || 'compte';
    if (checkedFor.current === who || !emptyCompanyFields(settings).length) return;
    checkedFor.current = who;
    const drive = graphDrive(getAccessToken);
    const apply = async () => {
      await Promise.resolve();
      setCompanySetup({ state: 'checking' });
      const result = await companySettingsFor(settings, async (field, value) => {
        if (field === 'repertoire') {
          await resolveWorkbook(getAccessToken, value);
          return true;
        }
        const item = await drive.resolve(value, field === 'racine' ? RACINE_LABEL : REFERENCE_LABEL);
        return item.folder;
      });
      if (Object.keys(result.filled).length) await save({ ...settings, ...result.filled });
      setCompanySetup({ state: 'done', filled: Object.keys(result.filled) as CompanyField[], missing: result.missing });
    };
    void apply();
  }, [loaded, settings, status, account, getAccessToken, save]);

  const update = (field: keyof Settings) => (value: string) => setEdits((previous) => ({ ...previous, [field]: value }));

  const planId = (): string => (planText === null ? draft.plannerPlanId : plannerPlanIdFromText(planText));

  const onSave = async () => {
    setSaveError('');
    let plannerPlanId: string;
    try {
      plannerPlanId = planId();
    } catch (exc) {
      setSaveError(errorMessage(exc));
      return;
    }
    const plannerDueDays = dueText === null ? draft.plannerDueDays : Number(dueText.trim() || 'NaN');
    setSaving(true);
    try {
      await save({ ...draft, plannerPlanId, plannerDueDays });
      close('/');
    } finally {
      setSaving(false);
    }
  };

  const loadPlanner = async () => {
    setPlannerError('');
    setPlannerLoading(true);
    try {
      const id = draft.demo ? DEMO_PLAN_ID : planId();
      const [buckets, members] = await Promise.all([planner.buckets(id), planner.members(id)]);
      setPlannerOptions({ buckets, members });
    } catch (exc) {
      setPlannerError(errorMessage(exc));
    } finally {
      setPlannerLoading(false);
    }
  };

  const toggleMember = (member: PlannerMember) => {
    const ids = draft.plannerAssigneeIds;
    const names = draft.plannerAssigneeNames;
    const index = ids.indexOf(member.id);
    setEdits((previous) => ({
      ...previous,
      plannerAssigneeIds: index >= 0 ? ids.filter((id) => id !== member.id) : [...ids, member.id],
      plannerAssigneeNames:
        index >= 0 ? names.filter((_, position) => position !== index) : [...names, member.name || member.email || member.id],
    }));
  };

  // « Ma sélection » of the « Planning » widget: the planning's people, team by team.
  const { data: planning, load: loadPlanning } = usePlanning();
  const teams = useMemo(() => (planning ? planningTeams(planning) : []), [planning]);
  const canReadPlanning = loaded && (settings.demo || status === 'signedIn');
  useEffect(() => {
    if (Platform.OS !== 'android' && canReadPlanning && !planning) void loadPlanning();
  }, [canReadPlanning, planning, loadPlanning]);
  const togglePerson = (id: string) =>
    setEdits((previous) => {
      const chosen = previous.widgetPeople ?? settings.widgetPeople;
      return { ...previous, widgetPeople: chosen.includes(id) ? chosen.filter((other) => other !== id) : [...chosen, id] };
    });

  const buckets =
    plannerOptions?.buckets ??
    (draft.plannerBucketId ? [{ id: draft.plannerBucketId, name: draft.plannerBucketName || draft.plannerBucketId }] : []);
  const members =
    plannerOptions?.members ??
    draft.plannerAssigneeIds.map((id, index) => ({ id, name: draft.plannerAssigneeNames[index] || id, email: '' }));

  // Microsoft's page replaces the settings until it answers or « Annuler ».
  if (signInPage) {
    return (
      <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
        <MicrosoftSignIn url={signInPage} onRedirect={(url) => void finishSignIn(url)} onCancel={cancelSignIn} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/')} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Paramètres</Kicker>
        <View style={styles.barButton} />
      </View>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Title>Paramètres</Title>

          <Blueprint style={styles.section}>
            <SectionHeading number="01">Compte Microsoft</SectionHeading>
            {status === 'signedIn' ? (
              <>
                <Body>{account ? `${account.name} · ${account.username}` : 'Connecté'}</Body>
                {companySetup?.state === 'checking' ? <Muted>Vérification des dossiers de l’entreprise…</Muted> : null}
                {companySetup?.state === 'done' && companySetup.filled.length ? (
                  <Text style={styles.applied}>
                    Réglages de l’entreprise vérifiés et remplis : {companySetup.filled.map((field) => COMPANY_LABELS[field]).join(', ')}.
                  </Text>
                ) : null}
                {companySetup?.state === 'done' && companySetup.missing.length ? (
                  <Text style={styles.error}>
                    Introuvable avec ce compte : {companySetup.missing.map((field) => COMPANY_LABELS[field]).join(', ')}. Vérifiez le
                    compte connecté, ou collez le lien ci-dessous.
                  </Text>
                ) : null}
                {/* Limited, the phone keeps its account: it is reconnected only with the password lifted. */}
                {restricted ? null : <Button label="Se déconnecter" onPress={() => void signOut()} style={styles.start} />}
              </>
            ) : (
              <>
                <Muted>
                  {canSignIn
                    ? 'Connectez-vous avec le compte Microsoft partagé du répertoire chantier : le planning est dans son OneDrive. La page Microsoft s’ouvre dans l’app, comme sur le poste.'
                    : 'La connexion Microsoft se fait depuis l’app sur le téléphone. Ici, utilisez le mode démo.'}
                </Muted>
                <Button
                  label="Se connecter à Microsoft"
                  variant="primary"
                  busy={busy}
                  disabled={!canSignIn}
                  onPress={() => void signIn()}
                  style={styles.start}
                />
              </>
            )}
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </Blueprint>

          {restricted ? null : (
            <>
              <Blueprint style={styles.section}>
                <SectionHeading number="02">Répertoire chantier</SectionHeading>
                <Field
                  label="Lien OneDrive / SharePoint du classeur"
                  value={draft.repertoire}
                  onChangeText={update('repertoire')}
                  placeholder="https://… ou Dossier/Répertoire chantier.xlsx"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <Muted>Le même lien que dans les paramètres du poste. Un chemin est lu depuis la racine du OneDrive connecté.</Muted>
              </Blueprint>

              <Blueprint style={styles.section}>
                <SectionHeading number="03">Planning</SectionHeading>
                <Field
                  label="Dossier OneDrive"
                  value={draft.planningFolder}
                  onChangeText={update('planningFolder')}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </Blueprint>

              <Blueprint style={styles.section}>
                <View style={styles.switchRow}>
                  <View style={styles.flex}>
                    <SectionHeading number="04">Mode démo</SectionHeading>
                    <Muted>Données d’exemple en mémoire : rien n’est lu ni écrit chez Microsoft.</Muted>
                  </View>
                  <Switch
                    accessibilityLabel="Mode démo"
                    value={draft.demo}
                    onValueChange={(demo) => setEdits((previous) => ({ ...previous, demo }))}
                    trackColor={{ true: colors.accent700, false: colors.line }}
                  />
                </View>
              </Blueprint>

              <Blueprint style={styles.section}>
                <SectionHeading number="05">Utilisateur</SectionHeading>
                <Field
                  label="Initiales utilisateur"
                  value={draft.initials}
                  onChangeText={update('initials')}
                  autoCapitalize="characters"
                  maxLength={6}
                />
              </Blueprint>

              <Blueprint style={styles.section}>
                <SectionHeading number="06">Dossiers projets</SectionHeading>
                <Field
                  label="Racine projets"
                  value={draft.racine}
                  onChangeText={update('racine')}
                  placeholder="https://… ou Projets"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <Field
                  label="Dossier de référence"
                  value={draft.reference}
                  onChangeText={update('reference')}
                  placeholder="https://… ou Projets/00 - Dossier de référence"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <Muted>
                  Les mêmes dossiers que sur le poste : lien OneDrive / SharePoint (recommandé) ou chemin depuis la racine du
                  OneDrive connecté. Un projet est créé dans « racine / année / numéro » en copiant le dossier de référence.
                </Muted>
              </Blueprint>

              <Blueprint style={styles.section}>
                <View style={styles.switchRow}>
                  <View style={styles.flex}>
                    <SectionHeading number="07">Microsoft Planner</SectionHeading>
                    <Muted>Une tâche « numéro - désignation » par projet créé, comme sur le poste.</Muted>
                  </View>
                  <Switch
                    accessibilityLabel="Créer les tâches Planner"
                    value={draft.plannerEnabled}
                    onValueChange={(plannerEnabled) => setEdits((previous) => ({ ...previous, plannerEnabled }))}
                    trackColor={{ true: colors.accent700, false: colors.line }}
                  />
                </View>
                <Field
                  label="Plan Planner : lien ou identifiant"
                  value={planText ?? draft.plannerPlanId}
                  onChangeText={(value) => {
                    setPlanText(value);
                    setPlannerOptions(null);
                  }}
                  placeholder="https://planner.cloud.microsoft/…"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <Button label="Charger colonnes et membres" onPress={() => void loadPlanner()} busy={plannerLoading} style={styles.start} />
                {plannerError ? <Text style={styles.error}>{plannerError}</Text> : null}
                <View style={styles.group}>
                  <Caps>Colonne</Caps>
                  {buckets.length ? (
                    <View style={styles.chips}>
                      {buckets.map((bucket) => (
                        <Chip
                          key={bucket.id}
                          label={bucket.name}
                          selected={bucket.id === draft.plannerBucketId}
                          onPress={() => setEdits((previous) => ({ ...previous, plannerBucketId: bucket.id, plannerBucketName: bucket.name }))}
                        />
                      ))}
                    </View>
                  ) : (
                    <Muted>Chargez le plan pour choisir la colonne.</Muted>
                  )}
                </View>
                <View style={styles.group}>
                  <Caps>Membres par défaut</Caps>
                  {members.length ? (
                    <View style={styles.chips}>
                      {members.map((member) => (
                        <Chip
                          key={member.id}
                          label={member.name || member.email || member.id}
                          selected={draft.plannerAssigneeIds.includes(member.id)}
                          onPress={() => toggleMember(member)}
                        />
                      ))}
                    </View>
                  ) : (
                    <Muted>Aucun : la tâche va au compte connecté.</Muted>
                  )}
                </View>
                <View style={styles.due}>
                  <Field
                    label="Échéance (jours)"
                    value={dueText ?? String(draft.plannerDueDays)}
                    onChangeText={setDueText}
                    keyboardType="number-pad"
                    maxLength={3}
                  />
                </View>
                <Muted>
                  Planner demande les autorisations Tasks.ReadWrite, User.ReadBasic.All et GroupMember.Read.All : reconnectez le
                  compte Microsoft si Microsoft les refuse.
                </Muted>
              </Blueprint>
            </>
          )}

          {Platform.OS !== 'android' ? (
            <Blueprint style={styles.section}>
              <SectionHeading number="08">Widget Planning</SectionHeading>
              <Muted>
                Sur l’iPhone, touchez longuement le widget « Planning », puis « Modifier le widget » : « Afficher » propose Moi,
                Ma sélection, une équipe, une personne ou tout le monde. « Ma sélection » montre les personnes cochées ici.
              </Muted>
              {teams.length ? (
                teams.map((team) => (
                  <View key={team.key} style={styles.group}>
                    <Caps>{team.name}</Caps>
                    <View style={styles.chips}>
                      {team.members.map((person) => (
                        <Chip
                          key={person.id}
                          label={person.initials?.trim() ? `${person.initials.trim().toUpperCase()} · ${person.name}` : person.name}
                          selected={draft.widgetPeople.includes(person.id)}
                          onPress={() => togglePerson(person.id)}
                        />
                      ))}
                    </View>
                  </View>
                ))
              ) : (
                <Muted>Les personnes apparaissent une fois le planning lu (connexion Microsoft ou mode démo).</Muted>
              )}
            </Blueprint>
          ) : null}

          <AccessSection />

          {saveError ? <Text style={styles.error}>{saveError}</Text> : null}
          <Button label="Enregistrer" variant="primary" busy={saving} onPress={() => void onSave()} />
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

/**
 * « Accès limité »: a password limits this phone to the Explorer (each dossier's Photos and
 * Plans) and the Planning to look at; the same password gives the whole app back.
 */
function AccessSection() {
  const { restricted, restrict, unrestrict } = useRestriction();
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [problem, setProblem] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    try {
      const why = await action();
      setProblem(why);
      if (!why) {
        setPassword('');
        setConfirmation('');
      }
    } catch (exc) {
      setProblem(errorMessage(exc));
    } finally {
      setBusy(false);
    }
  };

  const passwordField = { secureTextEntry: true, autoCapitalize: 'none', autoCorrect: false, autoComplete: 'off', textContentType: 'none' } as const;

  return (
    <Blueprint style={styles.section}>
      <SectionHeading number="09">Accès limité</SectionHeading>
      {restricted ? (
        <>
          <Body>Actif sur ce téléphone : Explorer, avec les dossiers Photos et Plans de chaque dossier, et Planning en consultation.</Body>
          <Field label="Mot de passe" value={password} onChangeText={setPassword} {...passwordField} />
          <Button
            label="Retrouver l’accès complet"
            variant="primary"
            busy={busy}
            disabled={!password}
            onPress={() => void run(async () => ((await unrestrict(password)) ? '' : 'Mot de passe incorrect.'))}
            style={styles.start}
          />
        </>
      ) : (
        <>
          <Muted>
            Pour un téléphone prêté : seulement l’Explorer, avec les dossiers Photos et Plans de chaque dossier, et le Planning en
            consultation. Le même mot de passe rend ensuite l’accès complet.
          </Muted>
          <Field label="Mot de passe" value={password} onChangeText={setPassword} {...passwordField} />
          <Field label="Confirmer le mot de passe" value={confirmation} onChangeText={setConfirmation} {...passwordField} />
          <Button
            label="Limiter l’accès"
            busy={busy}
            disabled={!password || !confirmation}
            onPress={() => void run(() => restrict(password, confirmation))}
            style={styles.start}
          />
        </>
      )}
      {problem ? <Text style={styles.error}>{problem}</Text> : null}
    </Blueprint>
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
  barText: {
    fontFamily: fonts.heading,
    fontSize: 15,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
    color: colors.accent700,
  },
  barTitle: { color: colors.text },
  content: { padding: 20, gap: 18, paddingBottom: 40 },
  section: { padding: 16, gap: 12 },
  start: { alignSelf: 'flex-start' },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  error: { fontFamily: fonts.body, fontSize: 14, color: colors.err },
  applied: { fontFamily: fonts.body, fontSize: 14, color: colors.ok },
  group: { gap: 6 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  due: { width: 140 },
});
