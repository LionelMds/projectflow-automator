import { router } from 'expo-router';
import { ChevronRight, Folder, FolderOpen, Search } from 'lucide-react-native';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';

import { ActionSheet } from '@/components/ActionSheet';
import { Notice } from '@/components/Notice';
import { Screen } from '@/components/Screen';
import { Caps, DossierNumber, Kicker, Muted, Title } from '@/components/Text';
import { useAuth } from '@/lib/auth';
import { useDossierActions } from '@/lib/dossierActions';
import { pressFeedback } from '@/lib/haptics';
import { foldText } from '@/lib/planning/quickEntry';
import { useProjectsBackend } from '@/lib/projects/backend';
import { findChildFolder, type DriveItem } from '@/lib/projects/drive';
import { RACINE_LABEL } from '@/lib/projects/location';
import { canonicalProjectNumber, isProjectNumber } from '@/lib/projects/numbers';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const collator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' });

type Listing = { year: number; folders: DriveItem[]; missing: boolean };

function message(exc: unknown): string {
  return exc instanceof Error ? exc.message : 'Lecture impossible.';
}

/** The folder's dossier number, or '' for a folder that is not a dossier. */
function numberOf(folder: DriveItem): string {
  return isProjectNumber(folder.name) ? canonicalProjectNumber(folder.name) : '';
}

/**
 * The racine projets (« Clients ») by year, as the desktop's folders are laid out
 * (`<racine>/<year>/<YYYY-NNNN>`): the dossiers of a year, opened in the explorer, with the
 * same dossier actions as the rest of the app.
 */
export default function ExplorerScreen() {
  const { status } = useAuth();
  const { settings, loaded } = useSettings();
  const { drive } = useProjectsBackend();
  const { projects, wantProjects } = useRepertoire();
  const dossier = useDossierActions();
  const thisYear = new Date().getFullYear();
  const years = [thisYear, thisYear - 1, thisYear - 2, thisYear - 3];
  const [year, setYear] = useState(thisYear);
  const [otherOpen, setOtherOpen] = useState(false);
  const [typed, setTyped] = useState('');
  const [query, setQuery] = useState('');
  const [racineName, setRacineName] = useState('');
  const [listings, setListings] = useState<Record<number, Listing>>({});
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [reload, setReload] = useState(0);
  const [menu, setMenu] = useState<DriveItem | null>(null);

  const ready = loaded && (settings.demo || (status === 'signedIn' && settings.racine !== ''));

  useEffect(() => {
    if (ready) void wantProjects([`${year}-`]);
  }, [ready, year, wantProjects]);

  useEffect(() => {
    if (!ready) return undefined;
    let alive = true;
    const read = async () => {
      await Promise.resolve();
      try {
        const racine = await drive.resolve(settings.racine, RACINE_LABEL);
        const yearFolder = findChildFolder(await drive.children(racine), String(year));
        const folders = yearFolder ? (await drive.children(yearFolder)).filter((item) => item.folder) : [];
        // Latest dossiers first, as in the répertoire.
        folders.sort((left, right) => collator.compare(right.name, left.name));
        if (!alive) return;
        setRacineName(racine.name);
        setListings((previous) => ({ ...previous, [year]: { year, folders, missing: !yearFolder } }));
        setError('');
      } catch (exc) {
        if (alive) setError(message(exc));
      } finally {
        if (alive) setRefreshing(false);
      }
    };
    void read();
    return () => {
      alive = false;
    };
  }, [ready, drive, settings.racine, year, reload]);

  const listing = listings[year] ?? null;
  const folders = useMemo(() => {
    if (!listing) return [];
    const wanted = foldText(query.trim());
    if (!wanted) return listing.folders;
    return listing.folders.filter((folder) => {
      const project = projects.get(numberOf(folder));
      return [folder.name, project?.designation, project?.client, project?.contact].some((text) =>
        foldText(text ?? '').includes(wanted),
      );
    });
  }, [listing, query, projects]);

  const chooseOther = () => {
    const value = Number(typed.trim());
    if (!/^\d{4}$/.test(typed.trim()) || value < 2000 || value > 2100) {
      setError('Tapez une année à quatre chiffres, par exemple 2019.');
      return;
    }
    setError('');
    setYear(value);
    setOtherOpen(false);
  };

  const openFolder = (folder: DriveItem) =>
    router.push({
      pathname: '/dossier',
      params: { number: numberOf(folder), driveId: folder.driveId, itemId: folder.id, name: folder.name },
    });

  if (!ready) {
    const waiting = status === 'loading' || !loaded;
    return (
      <Screen>
        <View style={styles.empty}>
          <Kicker>Explorer</Kicker>
          <Notice
            message={
              waiting
                ? 'Chargement…'
                : status !== 'signedIn'
                  ? 'Connectez-vous à Microsoft pour explorer les dossiers.'
                  : 'Collez la racine projets dans les paramètres.'
            }
            action={waiting ? undefined : { label: 'Ouvrir les paramètres', onPress: () => router.push('/parametres') }}
          />
        </View>
      </Screen>
    );
  }

  const otherYear = !years.includes(year);
  const header = (
    <View style={styles.head}>
      <View>
        <Kicker>Explorer</Kicker>
        <Title>{racineName || 'Dossiers'}</Title>
      </View>

      <View accessibilityRole="tablist" style={styles.years}>
        {years.map((candidate) => {
          const selected = candidate === year && !otherOpen;
          return (
            <Pressable
              key={candidate}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              onPress={() => {
                setOtherOpen(false);
                setError('');
                setYear(candidate);
              }}
              style={[styles.year, selected && styles.yearOn]}
            >
              <Text style={[styles.yearText, selected && styles.yearTextOn]}>{candidate}</Text>
            </Pressable>
          );
        })}
        <Pressable
          accessibilityRole="tab"
          accessibilityState={{ selected: otherOpen || otherYear }}
          onPress={() => {
            setTyped(otherYear ? String(year) : '');
            setOtherOpen(true);
          }}
          style={[styles.year, (otherOpen || otherYear) && styles.yearOn]}
        >
          <Text style={[styles.yearText, (otherOpen || otherYear) && styles.yearTextOn]}>{otherYear && !otherOpen ? year : 'Autre'}</Text>
        </Pressable>
      </View>

      {otherOpen ? (
        <View style={styles.other}>
          <TextInput
            accessibilityLabel="Année"
            value={typed}
            onChangeText={setTyped}
            placeholder="2019"
            placeholderTextColor="#7a7f85"
            keyboardType="number-pad"
            maxLength={4}
            autoFocus
            returnKeyType="go"
            onSubmitEditing={chooseOther}
            style={styles.otherInput}
          />
          <Pressable accessibilityRole="button" onPress={chooseOther} style={styles.otherButton}>
            <Text style={styles.otherButtonText}>Ouvrir</Text>
          </Pressable>
        </View>
      ) : null}

      <View style={styles.search}>
        <Search size={18} color={colors.muted} strokeWidth={iconStroke} />
        <TextInput
          accessibilityLabel="Rechercher un dossier"
          placeholder="Numéro, client ou désignation"
          placeholderTextColor="#7a7f85"
          value={query}
          onChangeText={setQuery}
          autoCorrect={false}
          clearButtonMode="while-editing"
          style={styles.searchInput}
        />
      </View>

      {error ? <Notice tone="error" message={error} /> : null}
      {listing ? (
        <View style={styles.statusRow}>
          <Caps style={styles.flex}>
            {listing.missing
              ? `Pas de dossier ${year} dans ${racineName || 'la racine'}`
              : `${folders.length} dossier${folders.length > 1 ? 's' : ''} · ${year}`}
          </Caps>
        </View>
      ) : (
        <Muted>Lecture des dossiers {year}…</Muted>
      )}
    </View>
  );

  const menuNumber = menu ? numberOf(menu) : '';
  const menuProject = menuNumber ? projects.get(menuNumber) : undefined;

  return (
    <Screen>
      <FlatList
        data={listing ? folders : []}
        keyExtractor={(folder) => folder.id}
        ListHeaderComponent={header}
        ListEmptyComponent={
          listing && !listing.missing ? (
            <Muted style={styles.none}>{query ? 'Aucun dossier ne correspond.' : 'Aucun dossier cette année.'}</Muted>
          ) : undefined
        }
        ListFooterComponent={
          folders.length ? (
            <Muted style={styles.hint}>Touchez un dossier pour l’ouvrir, restez appuyé pour le charger, voir le planning…</Muted>
          ) : undefined
        }
        renderItem={({ item }) => {
          const number = numberOf(item);
          const project = number ? projects.get(number) : undefined;
          return (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={[item.name, project?.designation, project?.client].filter(Boolean).join(', ')}
              accessibilityHint="Appui long : actions du dossier"
              onPress={() => openFolder(item)}
              onLongPress={() => {
                pressFeedback();
                setMenu(item);
              }}
              delayLongPress={350}
              style={({ pressed }) => [styles.row, pressed && styles.pressed]}
            >
              <Folder size={24} color={colors.accent700} fill={colors.accent200} strokeWidth={iconStroke} />
              <View style={styles.rowText}>
                {number ? <DossierNumber style={styles.rowNumber}>{item.name}</DossierNumber> : <Text style={styles.rowName}>{item.name}</Text>}
                {project?.designation ? (
                  <Text style={styles.designation} numberOfLines={1}>
                    {project.designation}
                  </Text>
                ) : null}
                {project?.client || project?.contact ? (
                  <Text style={styles.client} numberOfLines={1}>
                    {[project.client, project.contact].filter(Boolean).join(' · ')}
                  </Text>
                ) : null}
              </View>
              <ChevronRight size={18} color={colors.muted} strokeWidth={iconStroke} />
            </Pressable>
          );
        }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              setReload((value) => value + 1);
            }}
            tintColor={colors.accent700}
          />
        }
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.list}
      />

      <ActionSheet
        visible={menu !== null}
        onClose={() => setMenu(null)}
        kicker={menu?.name}
        title={menuProject?.designation || (menuNumber ? 'Désignation inconnue' : 'Dossier')}
        subtitle={[menuProject?.client, menuProject?.contact].filter(Boolean).join(' · ')}
        sections={
          menu
            ? [
                {
                  actions: [
                    {
                      key: 'open',
                      label: 'Ouvrir le dossier',
                      detail: 'Fichiers du dossier',
                      icon: (c) => <FolderOpen size={20} color={c} strokeWidth={iconStroke} />,
                      onPress: () => openFolder(menu),
                    },
                    ...(menuNumber ? dossier.actions(menuNumber, ['fiche', 'creation']) : []),
                  ],
                },
                { actions: menuNumber ? dossier.actions(menuNumber, ['task', 'planning', 'repertoire']) : [] },
                { actions: menuNumber ? dossier.actions(menuNumber, ['copy']) : [] },
              ]
            : []
        }
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  empty: { padding: 20, gap: 12 },
  list: { paddingHorizontal: 20, paddingBottom: 28 },
  head: { paddingTop: 18, paddingBottom: 6, gap: 12 },
  years: { flexDirection: 'row', borderWidth: 1, borderColor: colors.accent },
  year: { flex: 1, minHeight: touch, alignItems: 'center', justifyContent: 'center' },
  yearOn: { backgroundColor: colors.accent700 },
  yearText: { fontFamily: fonts.heading, fontSize: 16, letterSpacing: 0.6, color: colors.accent700 },
  yearTextOn: { color: '#ffffff' },
  other: { flexDirection: 'row', gap: 8 },
  otherInput: {
    flex: 1,
    minHeight: touch,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
    fontFamily: fonts.heading,
    fontSize: 18,
    letterSpacing: 0.8,
    color: colors.text,
  },
  otherButton: { minHeight: touch, paddingHorizontal: 16, justifyContent: 'center', backgroundColor: colors.accent700 },
  otherButtonText: { fontFamily: fonts.headingBold, fontSize: 15, letterSpacing: 1, textTransform: 'uppercase', color: '#ffffff' },
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
  statusRow: { flexDirection: 'row', alignItems: 'center', paddingTop: 4 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: 64,
    paddingVertical: 10,
    paddingHorizontal: 4,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  pressed: { backgroundColor: colors.accent100 },
  rowText: { flex: 1, gap: 1 },
  rowNumber: { fontSize: 17 },
  rowName: { fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.text },
  designation: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text },
  client: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
  none: { paddingVertical: 20 },
  hint: { paddingTop: 12, fontSize: 13 },
});
