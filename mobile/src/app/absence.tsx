import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Notice } from '@/components/Notice';
import { Body, Caps, Kicker, Muted, Title } from '@/components/Text';
import { close } from '@/lib/navigation';
import { contextFor, deleteAbsence } from '@/lib/planning/commands';
import { describeDay } from '@/lib/planning/display';
import { ABSENCE_LABELS } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import { parseDate } from '@/lib/planning/walltime';
import { colors, fonts, touch } from '@/theme/tokens';

const PARTS: Record<string, string> = { day: 'Journée', morning: 'Matin', afternoon: 'Après-midi' };

export default function AbsenceScreen() {
  const params = useLocalSearchParams<{ id?: string }>();
  const { data, apply, author, editBlocker, waiting, error: loadError } = useLoadedPlanning();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const absence = data?.absences.find((candidate) => candidate.id === params.id && !candidate.deleted_at) ?? null;
  const person = absence ? data?.resources.find((resource) => resource.id === absence.resource_id) : undefined;

  const onDelete = async () => {
    if (!data || !absence) return;
    setError('');
    setBusy(true);
    try {
      const reason = ABSENCE_LABELS[absence.reason ?? 'absent'] ?? absence.reason;
      await apply(deleteAbsence(contextFor(data, author), absence.id), `Absence supprimée : ${reason} ${person?.initials ?? ''}`.trim());
      close('/planning');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Suppression impossible.');
    } finally {
      setBusy(false);
    }
  };

  const first = absence ? parseDate(absence.first_day) : null;
  const last = absence ? parseDate(absence.last_day) : null;

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/planning')} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Absence</Kicker>
        <View style={styles.barButton} />
      </View>
      <ScrollView contentContainerStyle={styles.content}>
        {!absence ? (
          waiting ? (
            <Muted>Chargement du planning…</Muted>
          ) : (
            <Notice tone={loadError ? 'error' : 'info'} message={loadError || "Cette absence n'est plus dans le planning."} />
          )
        ) : (
          <>
            <Title>{ABSENCE_LABELS[absence.reason ?? 'absent'] ?? absence.reason}</Title>
            <Blueprint style={styles.section}>
              <Caps>Personne</Caps>
              <Body>{person ? `${person.initials ? `${person.initials} · ` : ''}${person.name}` : 'Inconnue'}</Body>
              <Caps>Jours</Caps>
              <Body>
                {first !== null ? describeDay(first) : absence.first_day}
                {last !== null && last !== first ? ` → ${describeDay(last)}` : ''}
                {first === last && absence.part && absence.part !== 'day' ? ` · ${PARTS[absence.part]}` : ''}
              </Body>
              {absence.note ? (
                <>
                  <Caps>Note</Caps>
                  <Body>{absence.note}</Body>
                </>
              ) : null}
            </Blueprint>
            <Muted>Une absence se saisit dans la saisie rapide : « /vac LBI lun 5j ».</Muted>
            {editBlocker ? <Notice message={editBlocker} /> : null}
            {error ? <Notice tone="error" message={error} /> : null}
            <Button label="Supprimer l’absence" busy={busy} disabled={Boolean(editBlocker)} onPress={() => void onDelete()} />
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.bg },
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
  content: { padding: 20, gap: 16, paddingBottom: 40 },
  section: { padding: 16, gap: 6 },
});
