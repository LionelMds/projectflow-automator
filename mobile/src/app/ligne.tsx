import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Blueprint } from '@/components/Blueprint';
import { Button } from '@/components/Button';
import { Field } from '@/components/Field';
import { Notice } from '@/components/Notice';
import { Caps, DossierNumber, Kicker, Muted, Title } from '@/components/Text';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { close } from '@/lib/navigation';
import { changedFields, editFromRow, type RowEdit } from '@/lib/repertoire/rows';
import { colors, fonts, touch } from '@/theme/tokens';

export default function LigneScreen() {
  const params = useLocalSearchParams<{ row?: string }>();
  const { rowAt, save } = useRepertoire();
  const row = rowAt(Number(params.row));
  const [edit, setEdit] = useState<RowEdit | null>(row ? editFromRow(row) : null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const changed = useMemo(() => (row && edit ? changedFields(row, edit) : new Set<keyof RowEdit>()), [row, edit]);

  const update = (field: keyof RowEdit) => (value: string) =>
    setEdit((previous) => (previous ? { ...previous, [field]: value } : previous));

  const onSave = async () => {
    if (!row || !edit) return;
    setSaving(true);
    setError('');
    try {
      await save(row, edit);
      close('/repertoire');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Enregistrement impossible.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/repertoire')} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Ligne du répertoire</Kicker>
        <View style={styles.barButton} />
      </View>
      {!row || !edit ? (
        <View style={styles.content}>
          <Notice message="Cette ligne n'est plus affichée. Actualisez le répertoire." />
        </View>
      ) : (
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.flex}>
          <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
            <View style={styles.titles}>
              <DossierNumber style={styles.number}>{row.number}</DossierNumber>
              <Title numberOfLines={3}>{edit.designation || row.number}</Title>
            </View>
            <Blueprint style={styles.form}>
              <View style={styles.formHead}>
                <Caps>Colonnes A:E</Caps>
                {changed.size ? (
                  <Text style={styles.changed}>
                    {changed.size} modification{changed.size > 1 ? 's' : ''}
                  </Text>
                ) : null}
              </View>
              <Field label="Date" value={edit.date} onChangeText={update('date')} dirty={changed.has('date')} />
              <Field label="Client" value={edit.client} onChangeText={update('client')} dirty={changed.has('client')} />
              <Field label="Contact" value={edit.contact} onChangeText={update('contact')} dirty={changed.has('contact')} />
              <Field
                label="Désignation"
                value={edit.designation}
                onChangeText={update('designation')}
                dirty={changed.has('designation')}
                multiline
              />
            </Blueprint>
            <Muted>
              La ligne est relue avant l’écriture : si elle a changé dans le classeur partagé, l’enregistrement est
              refusé et il faut actualiser. Les colonnes comptables F:L ne sont jamais réécrites.
            </Muted>
            {error ? <Notice tone="error" message={error} /> : null}
            <Button
              label="Enregistrer la ligne"
              variant="primary"
              busy={saving}
              disabled={changed.size === 0}
              onPress={() => void onSave()}
            />
          </ScrollView>
        </KeyboardAvoidingView>
      )}
    </SafeAreaView>
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
  content: { padding: 20, gap: 16, paddingBottom: 40 },
  titles: { gap: 2 },
  number: { fontSize: 18 },
  form: { padding: 16, gap: 12 },
  formHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  changed: { fontFamily: fonts.bodyMedium, fontSize: 13, color: '#2e6b3c' },
});
