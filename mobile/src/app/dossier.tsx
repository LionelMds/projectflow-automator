import { useLocalSearchParams } from 'expo-router';
import {
  ChevronLeft,
  ChevronRight,
  DraftingCompass,
  ExternalLink,
  Eye,
  File as FileIcon,
  FileImage,
  FileSpreadsheet,
  FileText,
  Folder,
  Presentation,
  Share,
} from 'lucide-react-native';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { ActivityIndicator, FlatList, Image, Platform, Pressable, RefreshControl, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ActionSheet } from '@/components/ActionSheet';
import { Notice } from '@/components/Notice';
import { Kicker, Muted } from '@/components/Text';
import { pressFeedback } from '@/lib/haptics';
import { close } from '@/lib/navigation';
import { useProjectsBackend } from '@/lib/projects/backend';
import { findDossier } from '@/lib/projects/creation';
import type { DriveItem } from '@/lib/projects/drive';
import { dateText, fileKind, openerName, sizeText, type FileKind } from '@/lib/projects/fileKinds';
import { parseProjectNumber } from '@/lib/projects/numbers';
import { openDriveFile, openOnWeb, shareDriveFile } from '@/lib/projects/openFile';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const collator = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' });

/** Folders first, then files, each by name as Files sorts them. */
function sorted(items: DriveItem[]): DriveItem[] {
  return [...items].sort((left, right) => Number(right.folder) - Number(left.folder) || collator.compare(left.name, right.name));
}

function message(exc: unknown): string {
  return exc instanceof Error ? exc.message : 'Action impossible.';
}

const ICONS: Record<FileKind, (color: string) => ReactNode> = {
  excel: (c) => <FileSpreadsheet size={24} color={c} strokeWidth={iconStroke} />,
  word: (c) => <FileText size={24} color={c} strokeWidth={iconStroke} />,
  powerpoint: (c) => <Presentation size={24} color={c} strokeWidth={iconStroke} />,
  pdf: (c) => <FileText size={24} color={c} strokeWidth={iconStroke} />,
  image: (c) => <FileImage size={24} color={c} strokeWidth={iconStroke} />,
  cad: (c) => <DraftingCompass size={24} color={c} strokeWidth={iconStroke} />,
  text: (c) => <FileText size={24} color={c} strokeWidth={iconStroke} />,
  other: (c) => <FileIcon size={24} color={c} strokeWidth={iconStroke} />,
};

/**
 * The folder of a dossier, browsed like the Files app: its subfolders and files, an Office file
 * opened in its app, any other one in the phone's viewer, everything shareable or saved in Files.
 */
export default function DossierScreen() {
  // A dossier by its number, or a folder already found (the Explorer tab gives it).
  const params = useLocalSearchParams<{ number?: string; driveId?: string; itemId?: string; name?: string }>();
  const number = (params.number ?? '').trim();
  const given = params.driveId && params.itemId ? { driveId: params.driveId, id: params.itemId, name: params.name ?? '' } : null;
  const { settings, loaded } = useSettings();
  const { drive } = useProjectsBackend();
  const { projects, wantProjects } = useRepertoire();
  const [path, setPath] = useState<DriveItem[]>([]);
  const [listing, setListing] = useState<{ folderId: string; items: DriveItem[] } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [opening, setOpening] = useState<string | null>(null);
  const [menu, setMenu] = useState<DriveItem | null>(null);

  useEffect(() => {
    if (number) void wantProjects([number]);
  }, [number, wantProjects]);

  // The project folder, found as « Ouvrir dossier » finds it on the desktop.
  useEffect(() => {
    // The settings are read at start-up: the racine is only known once they are.
    if (!loaded) return undefined;
    let alive = true;
    const open = async () => {
      await Promise.resolve();
      try {
        if (given) {
          if (alive) setPath([{ ...given, folder: true, size: 0, modified: '', webUrl: '' }]);
          return;
        }
        if (!settings.demo && !settings.racine) throw new Error('Collez la racine projets dans les paramètres.');
        const found = await findDossier(drive, settings.racine, parseProjectNumber(number));
        if (!found.folder) throw new Error(`Dossier introuvable : ${found.folderPath}`);
        if (alive) {
          setPath([found.folder]);
          setError('');
        }
      } catch (exc) {
        if (alive) setError(message(exc));
      }
    };
    void open();
    return () => {
      alive = false;
    };
    // `given` is rebuilt at each render from its three parameters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, drive, settings.demo, settings.racine, number, params.driveId, params.itemId]);

  const current = path[path.length - 1] ?? null;

  const list = async (folder: DriveItem) => {
    try {
      const items = await drive.children(folder, { thumbnails: true });
      setListing({ folderId: folder.id, items: sorted(items) });
      setError('');
    } catch (exc) {
      setError(message(exc));
    }
  };

  useEffect(() => {
    if (!current) return undefined;
    let alive = true;
    drive
      .children(current, { thumbnails: true })
      .then((items) => {
        if (!alive) return;
        setListing({ folderId: current.id, items: sorted(items) });
        setError('');
      })
      .catch((exc: unknown) => {
        if (alive) setError(message(exc));
      });
    return () => {
      alive = false;
    };
  }, [current, drive]);

  const items = listing && current && listing.folderId === current.id ? listing.items : null;
  const project = projects.get(number);
  const subtitle = useMemo(() => [project?.designation, project?.client].filter(Boolean).join(' · '), [project]);

  const openItem = async (item: DriveItem) => {
    if (item.folder) {
      setPath((previous) => [...previous, item]);
      return;
    }
    setError('');
    setOpening(item.id);
    try {
      await openDriveFile(drive, item);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setOpening(null);
    }
  };

  const onRefresh = async () => {
    if (!current) return;
    setRefreshing(true);
    await list(current);
    setRefreshing(false);
  };

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/')} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Dossier</Kicker>
        <View style={styles.barButton} />
      </View>

      <View style={styles.head}>
        <Text style={styles.number}>{number || given?.name}</Text>
        {subtitle ? (
          <Text style={styles.subtitle} numberOfLines={2}>
            {subtitle}
          </Text>
        ) : null}
        {path.length ? (
          <View style={styles.crumbs}>
            {path.length > 1 ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Dossier parent"
                onPress={() => setPath((previous) => previous.slice(0, -1))}
                style={styles.up}
              >
                <ChevronLeft size={20} color={colors.accent700} strokeWidth={iconStroke} />
              </Pressable>
            ) : null}
            <View style={styles.crumbList}>
              {path.map((folder, index) => {
                const last = index === path.length - 1;
                return (
                  <View key={folder.id} style={styles.crumb}>
                    {index > 0 ? <ChevronRight size={14} color={colors.muted} strokeWidth={iconStroke} /> : null}
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={last ? `Dossier ${folder.name}` : `Revenir à ${folder.name}`}
                      disabled={last}
                      onPress={() => setPath((previous) => previous.slice(0, index + 1))}
                      style={styles.crumbButton}
                    >
                      <Text style={[styles.crumbText, last && styles.crumbCurrent]} numberOfLines={1}>
                        {folder.name}
                      </Text>
                    </Pressable>
                  </View>
                );
              })}
            </View>
          </View>
        ) : null}
      </View>

      {error ? (
        <View style={styles.notice}>
          <Notice tone="error" message={error} />
        </View>
      ) : null}

      {!items ? (
        error && !current ? null : (
          <View style={styles.loading}>
            <ActivityIndicator color={colors.accent700} />
            <Muted>Lecture du dossier…</Muted>
          </View>
        )
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void onRefresh()} tintColor={colors.accent700} />}
          ListEmptyComponent={<Muted style={styles.empty}>Dossier vide.</Muted>}
          ListFooterComponent={
            items.length ? (
              <Muted style={styles.hint}>
                Touchez pour ouvrir. Restez appuyé sur un fichier pour le partager ou l’enregistrer dans Fichiers.
              </Muted>
            ) : undefined
          }
          renderItem={({ item }) => (
            <Row
              item={item}
              busy={opening === item.id}
              onPress={() => void openItem(item)}
              onLongPress={() => {
                pressFeedback();
                setMenu(item);
              }}
            />
          )}
        />
      )}

      <ActionSheet
        visible={menu !== null}
        onClose={() => setMenu(null)}
        kicker={menu?.folder ? 'Dossier' : 'Fichier'}
        title={menu?.name}
        subtitle={menu ? (menu.folder ? countText(menu.count) : [sizeText(menu.size), dateText(menu.modified)].filter(Boolean).join(' · ')) : ''}
        sections={
          menu
            ? [
                {
                  actions: [
                    {
                      key: 'open',
                      label: 'Ouvrir',
                      detail: menu.folder ? undefined : openerName(menu.name),
                      icon: (c) => <Eye size={20} color={c} strokeWidth={iconStroke} />,
                      onPress: () => openItem(menu),
                    },
                    ...(menu.folder
                      ? []
                      : [
                          {
                            key: 'share',
                            label: Platform.OS === 'ios' ? 'Partager ou enregistrer dans Fichiers' : 'Partager ou enregistrer',
                            icon: (c: string) => <Share size={20} color={c} strokeWidth={iconStroke} />,
                            onPress: () => shareDriveFile(drive, menu),
                          },
                        ]),
                    {
                      key: 'web',
                      label: 'Ouvrir sur le web',
                      detail: 'OneDrive',
                      icon: (c) => <ExternalLink size={20} color={c} strokeWidth={iconStroke} />,
                      onPress: () => openOnWeb(menu),
                    },
                  ],
                },
              ]
            : []
        }
      />
    </SafeAreaView>
  );
}

function countText(count: number | undefined): string {
  if (count === undefined) return '';
  return `${count} élément${count > 1 ? 's' : ''}`;
}

function Row({ item, busy, onPress, onLongPress }: { item: DriveItem; busy: boolean; onPress: () => void; onLongPress: () => void }) {
  const kind = fileKind(item.name);
  const meta = item.folder ? countText(item.count) : [sizeText(item.size), dateText(item.modified)].filter(Boolean).join(' · ');
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={item.folder ? `Dossier ${item.name}` : item.name}
      accessibilityHint={item.folder ? undefined : 'Appui long : partager ou enregistrer'}
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={350}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={styles.icon}>
        {item.folder ? (
          <Folder size={26} color={colors.accent700} fill={colors.accent200} strokeWidth={iconStroke} />
        ) : item.thumbnailUrl ? (
          <Image source={{ uri: item.thumbnailUrl }} style={styles.thumb} resizeMode="cover" />
        ) : (
          ICONS[kind](colors.accent700)
        )}
      </View>
      <View style={styles.rowText}>
        <Text style={styles.name} numberOfLines={2}>
          {item.name}
        </Text>
        {meta ? <Text style={styles.meta}>{meta}</Text> : null}
      </View>
      {busy ? <ActivityIndicator color={colors.accent700} /> : item.folder ? <ChevronRight size={18} color={colors.muted} strokeWidth={iconStroke} /> : null}
    </Pressable>
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
  head: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 6, gap: 2, borderBottomWidth: 1, borderBottomColor: colors.line },
  number: { fontFamily: fonts.headingBold, fontSize: 24, letterSpacing: 0.6, color: colors.accent700 },
  subtitle: { fontFamily: fonts.body, fontSize: 14, color: colors.muted },
  crumbs: { flexDirection: 'row', alignItems: 'center', marginTop: 6 },
  up: { width: touch - 8, height: touch, alignItems: 'center', justifyContent: 'center', marginLeft: -8 },
  crumbList: { flex: 1, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' },
  crumb: { flexDirection: 'row', alignItems: 'center', maxWidth: '100%' },
  crumbButton: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 3 },
  crumbText: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.accent700, textDecorationLine: 'underline' },
  crumbCurrent: { color: colors.text, textDecorationLine: 'none' },
  notice: { paddingHorizontal: 16, paddingTop: 10 },
  loading: { padding: 24, alignItems: 'center', gap: 8 },
  list: { paddingBottom: 32 },
  empty: { padding: 24, textAlign: 'center' },
  hint: { paddingHorizontal: 16, paddingTop: 12, fontSize: 13 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    minHeight: 60,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.line,
    backgroundColor: colors.surface,
  },
  pressed: { backgroundColor: colors.accent100 },
  icon: { width: 40, height: 40, alignItems: 'center', justifyContent: 'center' },
  thumb: { width: 40, height: 40, backgroundColor: colors.accent100 },
  rowText: { flex: 1, gap: 2 },
  name: { fontFamily: fonts.bodyMedium, fontSize: 16, color: colors.text },
  meta: { fontFamily: fonts.body, fontSize: 13, color: colors.muted },
});
