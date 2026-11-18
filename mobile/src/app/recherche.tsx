import { router } from 'expo-router';
import { CalendarRange, ListFilter, Search, X } from 'lucide-react-native';
import { useMemo, useState } from 'react';
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Chip } from '@/components/Chip';
import { Notice } from '@/components/Notice';
import { Caps, DossierNumber, Kicker, Muted } from '@/components/Text';
import { close } from '@/lib/navigation';
import { describeInstant } from '@/lib/planning/display';
import { withDossiers } from '@/lib/planning/dossiers';
import { formatDate, instantDay, parseInstant } from '@/lib/planning/instant';
import { STATUS_LABELS, taskTypeOf, taskTypesOf, type PlanningData, type PlanningTask, type TaskStatus } from '@/lib/planning/models';
import { useLoadedPlanning } from '@/lib/planning/PlanningContext';
import {
  EMPTY_SEARCH,
  filterCount,
  searchTasks,
  taskPeriod,
  type SearchPeriod,
  type TaskSearch,
} from '@/lib/planning/search';
import { todayNumber } from '@/lib/planning/walltime';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

/**
 * The planning's magnifier: every task, found by words typed as they come (a dossier, a
 * client, initials, a date) and narrowed by filters. A task opens on its sheet; its
 * calendar button shows its day on the board.
 */
const PERIODS: { value: SearchPeriod; label: string }[] = [
  { value: 'all', label: 'Toutes' },
  { value: 'upcoming', label: 'En cours et à venir' },
  { value: 'backlog', label: 'À planifier' },
  { value: 'past', label: 'Passées' },
];
const PERIOD_TITLES: Record<Exclude<SearchPeriod, 'all'>, string> = {
  upcoming: 'En cours et à venir',
  backlog: 'À planifier',
  past: 'Passées',
};
const STATUSES: TaskStatus[] = ['provisional', 'confirmed', 'done'];

type Row = { kind: 'title'; key: string; label: string; count: number } | { kind: 'task'; key: string; task: PlanningTask };

/** `values` with `value` in or out. */
function toggled<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((other) => other !== value) : [...values, value];
}

/** The filters in use, in a line: read when the panel is closed. */
function summary(data: PlanningData, search: TaskSearch): string {
  return [
    search.period === 'all' ? '' : PERIODS.find((period) => period.value === search.period)?.label,
    ...search.types.map((key) => taskTypeOf(data.meta, key).label),
    ...search.statuses.map((status) => STATUS_LABELS[status]),
    ...search.people.map((id) => {
      const person = data.resources.find((resource) => resource.id === id);
      return person ? person.initials || person.name : '';
    }),
    search.urgent ? 'Urgent' : '',
    search.night ? 'Nuit' : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

export default function SearchScreen() {
  const { data, waiting, error } = useLoadedPlanning();
  const { projects } = useRepertoire();
  // As on the board, a task without its dossier's details is read with the répertoire's.
  const shown = useMemo(() => (data ? withDossiers(data, projects) : null), [data, projects]);
  const [search, setSearch] = useState<TaskSearch>(EMPTY_SEARCH);
  const [filtering, setFiltering] = useState(false);
  const today = todayNumber();

  const rows = useMemo<Row[]>(() => {
    if (!shown) return [];
    const found = searchTasks(shown, search, today);
    const list: Row[] = [];
    let group = '';
    for (const task of found) {
      const period = taskPeriod(task, today);
      if (period !== group) {
        group = period;
        list.push({ kind: 'title', key: `title:${period}`, label: PERIOD_TITLES[period], count: found.filter((other) => taskPeriod(other, today) === period).length });
      }
      list.push({ kind: 'task', key: task.id, task });
    }
    return list;
  }, [shown, search, today]);
  const count = rows.filter((row) => row.kind === 'task').length;
  const filters = filterCount(search);
  const narrowed = filters > 0 || search.text.trim() !== '';

  const people = (data?.resources ?? []).filter((resource) => !resource.deleted_at && resource.active !== false);
  const kinds = taskTypesOf(data?.meta ?? null);

  const open = (task: PlanningTask) => router.push({ pathname: '/tache', params: { id: task.id } });
  /** The day view of the board on the task's first day. */
  const show = (task: PlanningTask) => {
    const start = parseInstant(task.start ?? '');
    if (start !== null) router.navigate({ pathname: '/planning', params: { jour: formatDate(instantDay(start)) } });
  };

  const renderRow = ({ item }: { item: Row }) => {
    if (item.kind === 'title') {
      return (
        <View style={styles.group}>
          <Caps>{item.label}</Caps>
          <Muted style={styles.small}>{item.count}</Muted>
        </View>
      );
    }
    const { task } = item;
    const kind = taskTypeOf(data?.meta ?? null, task.task_type);
    const number = (task.project_number ?? '').trim();
    const title = (task.title ?? '').trim() || (task.project_cache?.designation ?? '').trim() || 'Sans titre';
    const client = (task.project_cache?.client ?? '').trim();
    const start = parseInstant(task.start ?? '');
    const end = parseInstant(task.end ?? '');
    const who = Object.keys(task.assignments ?? {})
      .map((id) => data?.resources.find((resource) => resource.id === id))
      .map((person) => (person ? person.initials || person.name : ''))
      .filter(Boolean)
      .join(' ');
    const status = task.status ?? 'confirmed';
    const marks = [kind.label, who, status === 'confirmed' ? '' : STATUS_LABELS[status], task.off_hours ? '☾ Nuit' : '', client].filter(Boolean).join(' · ');
    return (
      <View style={[styles.row, { borderLeftColor: kind.color }, status === 'done' && styles.rowDone]}>
        <Pressable
          accessibilityRole="button"
          accessibilityHint="Ouvre la tâche"
          onPress={() => open(task)}
          style={({ pressed }) => [styles.rowMain, pressed && styles.pressed]}
        >
          <View style={styles.rowHead}>
            {number ? <DossierNumber style={styles.rowNumber}>{number}</DossierNumber> : null}
            <Text style={styles.rowTitle} numberOfLines={1}>
              {title}
            </Text>
            {task.urgent ? <Text style={styles.urgent}>Urgent</Text> : null}
          </View>
          <Text style={styles.rowWhen} numberOfLines={1}>
            {start !== null && end !== null ? `${describeInstant(start)} → ${describeInstant(end)}` : 'À planifier'}
          </Text>
          <Muted style={styles.small} numberOfLines={1}>
            {marks}
          </Muted>
        </Pressable>
        {start !== null ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Voir dans le planning"
            onPress={() => show(task)}
            style={({ pressed }) => [styles.rowGo, pressed && styles.pressed]}
          >
            <CalendarRange size={20} color={colors.accent700} strokeWidth={iconStroke} />
          </Pressable>
        ) : null}
      </View>
    );
  };

  // With the results, in their list: on a phone the filters would leave them no room otherwise.
  const filtersPanel = filtering ? (
    <View style={styles.panel}>
      <Caps>Quand</Caps>
      <View style={styles.chips}>
        {PERIODS.map((period) => (
          <Chip
            key={period.value}
            label={period.label}
            on={search.period === period.value}
            onPress={() => setSearch((current) => ({ ...current, period: period.value }))}
          />
        ))}
      </View>
      <Caps>Type</Caps>
      <View style={styles.chips}>
        {kinds.map((kind) => (
          <Chip
            key={kind.key}
            label={kind.label}
            color={kind.color}
            on={search.types.includes(kind.key)}
            onPress={() => setSearch((current) => ({ ...current, types: toggled(current.types, kind.key) }))}
          />
        ))}
      </View>
      <Caps>Statut</Caps>
      <View style={styles.chips}>
        {STATUSES.map((status) => (
          <Chip
            key={status}
            label={STATUS_LABELS[status]}
            on={search.statuses.includes(status)}
            onPress={() => setSearch((current) => ({ ...current, statuses: toggled(current.statuses, status) }))}
          />
        ))}
        <Chip label="Urgent" on={search.urgent} onPress={() => setSearch((current) => ({ ...current, urgent: !current.urgent }))} />
        <Chip label="☾ Nuit" on={search.night} onPress={() => setSearch((current) => ({ ...current, night: !current.night }))} />
      </View>
      {people.length ? (
        <>
          <Caps>Qui</Caps>
          <View style={styles.chips}>
            {people.map((person) => (
              <Chip
                key={person.id}
                label={person.initials || person.name}
                hint={person.name}
                on={search.people.includes(person.id)}
                onPress={() => setSearch((current) => ({ ...current, people: toggled(current.people, person.id) }))}
              />
            ))}
          </View>
        </>
      ) : null}
    </View>
  ) : filters && data ? (
    <Pressable accessibilityRole="button" accessibilityHint="Ouvre les filtres" onPress={() => setFiltering(true)} style={styles.summary}>
      <Text style={styles.summaryText} numberOfLines={1}>
        {summary(data, search)}
      </Text>
    </Pressable>
  ) : null;

  return (
    <SafeAreaView edges={['top', 'bottom']} style={styles.safe}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={() => close('/planning')} style={styles.barButton}>
          <Text style={styles.barText}>Fermer</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Recherche</Kicker>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: !narrowed }}
          disabled={!narrowed}
          onPress={() => setSearch(EMPTY_SEARCH)}
          style={[styles.barButton, styles.barRight, !narrowed && styles.hidden]}
        >
          <Text style={styles.barText}>Effacer</Text>
        </Pressable>
      </View>

      <View style={styles.top}>
        <View style={styles.field}>
          <Search size={18} color={colors.muted} strokeWidth={iconStroke} />
          <TextInput
            value={search.text}
            onChangeText={(text) => setSearch((current) => ({ ...current, text }))}
            placeholder="Dossier, client, personne, date…"
            placeholderTextColor="#8d9298"
            autoFocus
            autoCorrect={false}
            autoCapitalize="none"
            returnKeyType="search"
            accessibilityLabel="Rechercher une tâche"
            style={styles.input}
          />
          {search.text ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Effacer le texte"
              onPress={() => setSearch((current) => ({ ...current, text: '' }))}
              style={styles.clear}
            >
              <X size={18} color={colors.muted} strokeWidth={iconStroke} />
            </Pressable>
          ) : null}
        </View>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded: filtering }}
          accessibilityLabel={filters ? `Filtres, ${filters} en service` : 'Filtres'}
          onPress={() => setFiltering((current) => !current)}
          style={[styles.filterButton, (filtering || filters > 0) && styles.filterButtonOn]}
        >
          <ListFilter size={18} color={colors.accent700} strokeWidth={iconStroke} />
          {filters ? <Text style={styles.filterCount}>{filters}</Text> : null}
        </Pressable>
      </View>

      {error ? <Notice tone="error" message={error} /> : null}
      <FlatList
        data={rows}
        keyExtractor={(row) => row.key}
        renderItem={renderRow}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          <>
            {filtersPanel}
            <Muted style={styles.count}>
              {waiting ? 'Chargement…' : count === 0 ? '' : `${count} tâche${count > 1 ? 's' : ''}${narrowed ? ' trouvée' + (count > 1 ? 's' : '') : ''}`}
            </Muted>
          </>
        }
        ListEmptyComponent={
          waiting ? undefined : (
            <View style={styles.none}>
              <Muted>Aucune tâche ne correspond.</Muted>
              {narrowed ? (
                <Pressable accessibilityRole="button" onPress={() => setSearch(EMPTY_SEARCH)} style={styles.link}>
                  <Text style={styles.linkText}>Effacer la recherche</Text>
                </Pressable>
              ) : null}
            </View>
          )
        }
      />
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
  barButton: { minWidth: 96, minHeight: touch, justifyContent: 'center', paddingHorizontal: 8 },
  barRight: { alignItems: 'flex-end' },
  barText: { fontFamily: fonts.heading, fontSize: 15, letterSpacing: 1.1, textTransform: 'uppercase', color: colors.accent700 },
  barTitle: { color: colors.text },
  hidden: { opacity: 0 },
  top: { flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingTop: 12 },
  field: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: 48,
    paddingLeft: 12,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
  },
  // `minWidth`: a browser's field is as wide as twenty characters unless told otherwise.
  input: { flex: 1, minWidth: 0, minHeight: 46, fontFamily: fonts.bodyMedium, fontSize: 17, color: colors.text },
  clear: { width: 40, height: 46, alignItems: 'center', justifyContent: 'center' },
  filterButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    minWidth: 48,
    minHeight: 48,
    paddingHorizontal: 10,
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
  },
  filterButtonOn: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  filterCount: { fontFamily: fonts.headingBold, fontSize: 14, color: colors.accent900 },
  panel: { gap: 8, paddingTop: 12, paddingBottom: 4 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 4 },
  summary: { minHeight: 36, justifyContent: 'center', marginTop: 8, paddingHorizontal: 10, backgroundColor: colors.accent100 },
  summaryText: { fontFamily: fonts.body, fontSize: 14, color: colors.accent900 },
  list: { paddingHorizontal: 12, paddingBottom: 28 },
  count: { paddingTop: 10, fontSize: 13 },
  small: { fontSize: 13 },
  group: { flexDirection: 'row', alignItems: 'baseline', gap: 8, paddingTop: 14, paddingBottom: 6 },
  row: {
    flexDirection: 'row',
    marginBottom: 6,
    borderWidth: 1,
    borderColor: colors.line,
    borderLeftWidth: 5,
    backgroundColor: colors.surface,
  },
  rowDone: { opacity: 0.6 },
  rowMain: { flex: 1, gap: 2, paddingHorizontal: 10, paddingVertical: 8 },
  rowHead: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
  rowNumber: { fontSize: 16 },
  rowTitle: { flex: 1, fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.text },
  rowWhen: { fontFamily: fonts.body, fontSize: 14, color: colors.subtle },
  urgent: { fontFamily: fonts.headingBold, fontSize: 12, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.err },
  rowGo: { width: touch + 4, alignItems: 'center', justifyContent: 'center', borderLeftWidth: 1, borderLeftColor: colors.lineSoft },
  pressed: { backgroundColor: colors.accent100 },
  none: { gap: 8, paddingTop: 24, alignItems: 'flex-start' },
  link: { minHeight: 36, justifyContent: 'center' },
  linkText: { fontFamily: fonts.bodyMedium, fontSize: 15, color: colors.accent700, textDecorationLine: 'underline' },
});
