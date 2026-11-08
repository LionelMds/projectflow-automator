import { router, useLocalSearchParams } from 'expo-router';

import { PenLine, Search } from 'lucide-react-native';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionSheet } from '@/components/ActionSheet';
import { Button } from '@/components/Button';
import { Notice } from '@/components/Notice';
import { Screen } from '@/components/Screen';
import { Caps, DossierNumber, Kicker, Muted, Title } from '@/components/Text';
import { useAuth } from '@/lib/auth';
import { useDossierActions } from '@/lib/dossierActions';
import { pressFeedback } from '@/lib/haptics';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { filterRows, visibleRows, type RepertoireRow } from '@/lib/repertoire/rows';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

function clock(date: Date | null): string {
  if (!date) return '';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

export default function RepertoireScreen() {
  const { status } = useAuth();
  const { settings, loaded } = useSettings();
  const { snapshot, loading, error, loadedAt, load } = useRepertoire();
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(snapshot?.year ?? thisYear);
  const [query, setQuery] = useState('');
  const [menu, setMenu] = useState<RepertoireRow | null>(null);
  const dossier = useDossierActions();

  // « Voir dans Répertoire » from the planning: the dossier's year, the dossier searched.
  const params = useLocalSearchParams<{ dossier?: string; at?: string }>();
  const request = params.dossier ? `${params.dossier}@${params.at ?? ''}` : '';
  const [handled, setHandled] = useState('');
  if (request && request !== handled) {
    setHandled(request);
    const wantedYear = Number(params.dossier?.slice(0, 4));
    if (wantedYear) setYear(wantedYear);
    setQuery(params.dossier ?? '');
  }

  const ready = loaded && (settings.demo || (status === 'signedIn' && settings.repertoire !== ''));

  useEffect(() => {
    if (ready) void load(year);
  }, [ready, year, settings.repertoire, settings.demo, load]);

  const rows = useMemo(() => {
    if (!snapshot || snapshot.year !== year) return [];
    // Latest dossiers first, as one looks for them on a phone.
    return filterRows(visibleRows(snapshot), query).reverse();
  }, [snapshot, year, query]);

  const next = snapshot?.year === year ? snapshot.nextAvailable : null;

  const header = (
    <View style={styles.head}>
      <View style={styles.titleRow}>
        <View style={styles.flex}>
          <Kicker>Répertoire chantier</Kicker>
          <Title>Année {year}</Title>
        </View>
        <View accessibilityRole="tablist" style={styles.years}>
          {[thisYear, thisYear + 1].map((candidate) => {
            const selected = candidate === year;
            return (
              <Pressable
                key={candidate}
                accessibilityRole="tab"
                accessibilityState={{ selected }}
                onPress={() => setYear(candidate)}
                style={[styles.year, selected && styles.yearSelected]}
              >
                <Text style={[styles.yearText, selected && styles.yearTextSelected]}>{candidate}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
      <View style={styles.search}>
        <Search size={18} color={colors.muted} strokeWidth={iconStroke} />
        <TextInput
          accessibilityLabel="Rechercher dans le répertoire"
          placeholder="Numéro, client, contact ou désignation"
          placeholderTextColor="#7a7f85"
          value={query}
          onChangeText={setQuery}
          style={styles.searchInput}
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
      </View>
      {ready ? (
        <View style={styles.statusRow}>
          <Caps style={styles.source}>{settings.demo ? 'Mode démo' : 'Classeur OneDrive'}</Caps>
          <Muted style={styles.flex}>{loading ? '· Chargement…' : loadedAt ? `· À jour, ${clock(loadedAt)}` : ''}</Muted>
          <Button label="Actualiser" onPress={() => void load(year)} disabled={loading} style={styles.refresh} />
        </View>
      ) : null}
      {error ? <Notice tone="error" message={error} /> : null}
      {ready && snapshot?.year === year && rows.length ? (
        <Muted style={styles.hint}>Touchez une ligne pour la modifier, restez appuyé pour ouvrir le dossier, la fiche…</Muted>
      ) : null}
      {ready && snapshot?.year === year ? (
        <View style={styles.columns}>
          <Caps style={styles.colNumber}>No.</Caps>
          <Caps style={styles.colDate}>Date</Caps>
          <Caps style={styles.flex}>Client · Désignation</Caps>
        </View>
      ) : null}
    </View>
  );

  if (!ready) {
    const message =
      status === 'loading' || !loaded
        ? 'Chargement…'
        : status !== 'signedIn'
          ? 'Connectez-vous à Microsoft pour lire le répertoire chantier.'
          : 'Collez le lien OneDrive / SharePoint du répertoire chantier dans les paramètres.';
    return (
      <Screen>
        <View style={styles.empty}>
          <Kicker>Répertoire chantier</Kicker>
          <Notice message={message} action={{ label: 'Ouvrir les paramètres', onPress: () => router.push('/parametres') }} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <FlatList
        data={rows}
        keyExtractor={(row) => String(row.sheetRow)}
        ListHeaderComponent={header}
        renderItem={({ item }) => (
          <Row
            row={item}
            onLongPress={() => {
              pressFeedback();
              setMenu(item);
            }}
          />
        )}
        ListFooterComponent={
          next && !query ? (
            <View style={styles.nextBox}>
              <DossierNumber>{next.number}</DossierNumber>
              <Muted>Prochaine ligne disponible</Muted>
            </View>
          ) : undefined
        }
        ListEmptyComponent={
          !loading && snapshot?.year === year ? (
            <Muted style={styles.none}>{query ? 'Aucun dossier ne correspond.' : 'Aucun dossier cette année.'}</Muted>
          ) : undefined
        }
        refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load(year)} tintColor={colors.accent700} />}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.list}
      />
      <ActionSheet
        visible={menu !== null}
        onClose={() => setMenu(null)}
        kicker={menu?.number}
        title={menu ? menu.texts[4] || 'Sans désignation' : ''}
        subtitle={menu ? [menu.texts[2], menu.texts[3]].filter(Boolean).join(' · ') : ''}
        sections={
          menu
            ? [
                { actions: dossier.actions(menu.number, ['creation', 'folder', 'fiche']) },
                { actions: dossier.actions(menu.number, ['task', 'planning']) },
                {
                  actions: [
                    {
                      key: 'edit',
                      label: 'Modifier la ligne',
                      detail: 'Date, client, contact, désignation',
                      icon: (c) => <PenLine size={20} color={c} strokeWidth={iconStroke} />,
                      onPress: () => openLine(menu),
                    },
                    ...dossier.actions(menu.number, ['copy']),
                  ],
                },
              ]
            : []
        }
      />
    </Screen>
  );
}

function openLine(row: RepertoireRow) {
  router.push({ pathname: '/ligne', params: { row: String(row.sheetRow) } });
}

/** A tap edits the line; a long press offers what can be done with the dossier. */
function Row({ row, onLongPress }: { row: RepertoireRow; onLongPress: () => void }) {
  const [, date, client, contact, designation] = row.texts;
  const who = [client, contact].filter(Boolean).join(' · ');
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${row.number}, ${who}, ${designation}`}
      accessibilityHint="Appui long : actions du dossier"
      onPress={() => openLine(row)}
      onLongPress={onLongPress}
      delayLongPress={350}
      style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
    >
      <DossierNumber style={styles.colNumber}>{row.number}</DossierNumber>
      <Muted style={styles.colDate}>{date.slice(0, 5)}</Muted>
      <View style={styles.flex}>
        <Text style={styles.client} numberOfLines={1}>
          {who || '—'}
        </Text>
        <Muted numberOfLines={2}>{designation}</Muted>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  list: { paddingHorizontal: 20, paddingBottom: 28 },
  empty: { padding: 20, gap: 12 },
  head: { paddingTop: 18, gap: 12 },
  titleRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 10 },
  years: { flexDirection: 'row', borderWidth: 1, borderColor: colors.accent },
  year: { minHeight: 36, paddingHorizontal: 10, justifyContent: 'center' },
  yearSelected: { backgroundColor: colors.accent700 },
  yearText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.6, color: colors.accent700 },
  yearTextSelected: { color: '#ffffff' },
  search: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: touch,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  searchInput: { flex: 1, minHeight: touch, fontFamily: fonts.body, fontSize: 15, color: colors.text },
  statusRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  source: { color: colors.subtle },
  refresh: { minHeight: 36 },
  hint: { fontSize: 13 },
  columns: {
    flexDirection: 'row',
    gap: 8,
    paddingTop: 6,
    paddingBottom: 6,
    paddingHorizontal: 4,
    borderTopWidth: 1,
    borderTopColor: colors.text,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  colNumber: { width: 82 },
  colDate: { width: 44 },
  row: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
    minHeight: touch,
  },
  rowPressed: { backgroundColor: colors.accent100 },
  client: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text },
  nextBox: {
    marginTop: 10,
    padding: 12,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: colors.accent,
  },
  none: { paddingVertical: 20 },
});
