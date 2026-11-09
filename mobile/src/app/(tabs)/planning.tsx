import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { CalendarMinus, CalendarPlus, ChevronLeft, ChevronRight, Eye, PenLine, Plus, Trash2, X, Zap } from 'lucide-react-native';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AppState,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type GestureResponderEvent,
  type HostInstance,
} from 'react-native';

import { ActionSheet, type SheetSection } from '@/components/ActionSheet';
import { Notice } from '@/components/Notice';
import { Screen } from '@/components/Screen';
import { Caps, DossierNumber, Kicker, Muted } from '@/components/Text';
import { useAuth } from '@/lib/auth';
import { useDossierActions } from '@/lib/dossierActions';
import { pressFeedback, selectionFeedback } from '@/lib/haptics';
import {
  calendarResolver,
  contextFor,
  createAbsence,
  deleteAbsence,
  deleteTask,
  setStatus,
  unscheduleTask,
} from '@/lib/planning/commands';
import {
  columnHeading,
  dayInfoFrom,
  describeDay,
  describeInstant,
  halfDayStart,
  periodText,
  shiftView,
  shownDay,
  VIEW_LABELS,
  viewDays,
  type ViewKind,
} from '@/lib/planning/display';
import type { Change } from '@/lib/planning/documents';
import { dayStart, instantDay, minuteOfDay, parseInstant } from '@/lib/planning/instant';
import {
  ABSENCE_LABELS,
  isScheduled,
  STATUS_LABELS,
  taskLabel,
  taskTypeOf,
  type PlanningTask,
  type TaskStatus,
} from '@/lib/planning/models';
import { usePlanning } from '@/lib/planning/PlanningContext';
import { snap } from '@/lib/planning/calendar';
import { parseDate, todayNumber } from '@/lib/planning/walltime';
import { buildBoard, DAY_END, DAY_START, NOON, AFTERNOON, UNASSIGNED, type Board, type Column, type Segment, type WeekRow } from '@/lib/planning/week';
import { withDossiers } from '@/lib/planning/dossiers';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const NAME_WIDTH = 52;
const LANE_HEIGHT = 30;
const BAR_HEIGHT = 26;
const TEAM_HEIGHT = 30;
const MONTH_DAY_WIDTH = 34;
const REFRESH_SECONDS = 30;
const DAY_TICKS = [DAY_START, 9 * 60, NOON, AFTERNOON, DAY_END];

function clock(date: Date | null): string {
  if (!date) return '';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function rowHeight(row: WeekRow): number {
  return row.lanes * LANE_HEIGHT + 4;
}

const STATUSES: TaskStatus[] = ['provisional', 'confirmed', 'done'];

/** What a long press was on: a task (bar or card), an absence, or an empty half-day of a row. */
type Menu =
  | { kind: 'task'; task: PlanningTask }
  | { kind: 'absence'; absenceId: string; label: string }
  | { kind: 'cell'; rowKey: string; start: number; end: number | null };

export default function PlanningScreen() {
  const { status } = useAuth();
  const { settings, loaded } = useSettings();
  const planning = usePlanning();
  const { data, loading, error, loadedAt, load, notice, clearNotice, undo, runUndo, apply, author, editBlocker } = planning;
  const [view, setView] = useState<ViewKind>('week');
  const [anchor, setAnchor] = useState(() => shownDay(todayNumber()));
  const [actionError, setActionError] = useState('');
  const [menu, setMenu] = useState<Menu | null>(null);
  const dossier = useDossierActions();
  const ready = loaded && (settings.demo || status === 'signedIn');

  // « Voir dans le planning »: the week of the dossier's next task, its bars and cards outlined.
  const [focus, setFocus] = useState('');
  const params = useLocalSearchParams<{ dossier?: string; at?: string; jour?: string }>();

  // The « Planning » widget's day (« 2026-11-09 »): the day view on that day. The link is
  // cleared once followed, so that the same day asked again from the widget works again.
  const [followedDay, setFollowedDay] = useState('');
  if (params.jour && params.jour !== followedDay) {
    setFollowedDay(params.jour);
    const day = parseDate(params.jour);
    if (day !== null) {
      setView('day');
      setAnchor(shownDay(day));
    }
  } else if (!params.jour && followedDay) setFollowedDay('');
  useEffect(() => {
    if (params.jour) router.setParams({ jour: undefined });
  }, [params.jour]);

  const request = params.dossier ? `${params.dossier}@${params.at ?? ''}` : '';
  const [handled, setHandled] = useState('');
  if (request && request !== handled && data) {
    setHandled(request);
    setFocus(params.dossier ?? '');
    const days = dossierTasks(data.tasks, params.dossier ?? '')
      .map((task) => parseInstant(task.start ?? ''))
      .filter((start): start is number => start !== null)
      .map(instantDay)
      .sort((a, b) => a - b);
    const day = days.find((candidate) => candidate >= todayNumber()) ?? days[days.length - 1];
    if (day !== undefined) {
      setView('week');
      setAnchor(shownDay(day));
    }
  }

  useEffect(() => {
    if (ready) void load();
  }, [ready, load]);

  // While the board is on screen, other postes' changes come in through the delta.
  useFocusEffect(
    useCallback(() => {
      if (!ready) return undefined;
      const timer = setInterval(() => {
        if (AppState.currentState === 'active') void load();
      }, REFRESH_SECONDS * 1000);
      return () => clearInterval(timer);
    }, [ready, load]),
  );

  // As on the desktop, a task without its dossier's details shows the répertoire's; the years
  // of the dossiers on the board are read as they are needed.
  const { projects, wantProjects } = useRepertoire();
  const numbers = useMemo(
    () => [...new Set((data?.tasks ?? []).map((task) => (task.project_number ?? '').trim()).filter(Boolean))].sort().join(' '),
    [data],
  );
  useEffect(() => {
    if (ready && numbers) void wantProjects(numbers.split(' '));
  }, [ready, numbers, wantProjects]);
  const shown = useMemo(() => (data ? withDossiers(data, projects) : null), [data, projects]);

  const calendarFor = useMemo(() => (data ? calendarResolver(data) : null), [data]);
  const board = useMemo<Board | null>(() => {
    if (!data || !calendarFor) return null;
    const days = viewDays(view, anchor);
    const dayInfo = dayInfoFrom(calendarFor(null), data.meta);
    const axis = view === 'day' ? ({ kind: 'day', day: anchor } as const) : ({ kind: 'slots', days } as const);
    return buildBoard(shown ?? data, axis, { dayInfo, compact: view === 'month' });
  }, [data, shown, calendarFor, view, anchor]);

  if (!ready) {
    const waiting = status === 'loading' || !loaded;
    return (
      <Screen>
        <View style={styles.empty}>
          <Kicker>Planning</Kicker>
          <Notice
            message={waiting ? 'Chargement…' : 'Connectez-vous à Microsoft pour lire le planning partagé.'}
            action={waiting ? undefined : { label: 'Ouvrir les paramètres', onPress: () => router.push('/parametres') }}
          />
        </View>
      </Screen>
    );
  }

  /** The half-day (or the minute, in the day view) under a place of a row. */
  const slotAt = (rowKey: string, unit: number): { start: number; end: number | null } | null => {
    if (!board || !calendarFor) return null;
    const calendar = calendarFor(rowKey === UNASSIGNED ? null : rowKey);
    let start: number;
    if (view === 'day') start = snap(dayStart(anchor) + DAY_START + unit);
    else {
      const column = board.columns[Math.floor(unit / 2)];
      if (!column) return null;
      start = halfDayStart(calendar, column.day, unit % 2 === 1);
    }
    // The cell is the worked period holding the tap: a task typed without length fills it.
    const period = calendar.dayPeriods(Math.floor(start / 1440)).find(([low, high]) => low <= start && start < high);
    return { start, end: period && view !== 'day' ? period[1] : null };
  };

  /** Quick entry on that row and that half-day. */
  const newTaskAt = (rowKey: string, slot: { start: number; end: number | null }) => {
    const query: Record<string, string> = { row: rowKey, start: String(slot.start), anchor: String(anchor) };
    if (slot.end !== null) query.end = String(slot.end);
    router.push({ pathname: '/saisie', params: query });
  };

  const onCell = (rowKey: string, unit: number) => {
    const slot = slotAt(rowKey, unit);
    if (slot) newTaskAt(rowKey, slot);
  };

  const onCellLongPress = (rowKey: string, unit: number) => {
    const slot = slotAt(rowKey, unit);
    if (!slot) return;
    pressFeedback();
    setMenu({ kind: 'cell', rowKey, ...slot });
  };

  const onBar = (segment: Segment, rowKey: string) => {
    if (segment.task) router.push({ pathname: '/tache', params: { id: segment.task.id, row: rowKey } });
    else if (segment.absenceId) router.push({ pathname: '/absence', params: { id: segment.absenceId } });
  };

  const onBarLongPress = (segment: Segment) => {
    if (segment.task) {
      pressFeedback();
      setMenu({ kind: 'task', task: segment.task });
    } else if (segment.absenceId) {
      pressFeedback();
      setMenu({ kind: 'absence', absenceId: segment.absenceId, label: segment.label });
    }
  };

  const onCardLongPress = (task: PlanningTask) => {
    pressFeedback();
    setMenu({ kind: 'task', task });
  };

  const ctx = data ? contextFor(data, author) : null;
  const locked = Boolean(editBlocker);
  /** A planning change from the menu: written, then offered to « Annuler » like the others. */
  const change = (build: (context: NonNullable<typeof ctx>) => Change[], undoLabel: string) => async () => {
    if (!ctx) throw new Error('Planning indisponible.');
    await apply(build(ctx), undoLabel);
  };

  const menuSheet = (): { kicker: string; title: string; subtitle: string; accent?: string; sections: SheetSection[] } | null => {
    if (!menu || !data) return null;
    if (menu.kind === 'task') {
      const task = (shown ?? data).tasks.find((candidate) => candidate.id === menu.task.id) ?? menu.task;
      const label = taskLabel(task);
      const number = (task.project_number ?? '').trim();
      const current = task.status ?? 'confirmed';
      const start = parseInstant(task.start ?? '');
      const end = parseInstant(task.end ?? '');
      const people = Object.keys(task.assignments ?? {})
        .map((id) => data.resources.find((resource) => resource.id === id)?.initials || id)
        .join(' ');
      const when = start !== null && end !== null ? `${describeInstant(start)} → ${describeInstant(end)}` : 'À planifier';
      return {
        kicker: number || 'Sans dossier',
        title: (task.title ?? '').trim() || task.project_cache?.designation || label || 'Sans titre',
        subtitle: [when, people].filter(Boolean).join(' · '),
        accent: taskTypeOf(data.meta, task.task_type).color,
        sections: [
          {
            title: 'Statut',
            layout: 'segments',
            actions: STATUSES.map((value) => ({
              key: value,
              label: STATUS_LABELS[value],
              selected: current === value,
              disabled: locked,
              onPress:
                current === value
                  ? () => undefined
                  : () => {
                      selectionFeedback();
                      return change((c) => setStatus(c, task.id, value), `Statut ${STATUS_LABELS[value]} : ${label}`)();
                    },
            })),
          },
          {
            actions: [
              {
                key: 'edit',
                label: 'Modifier la tâche',
                icon: (c) => <PenLine size={20} color={c} strokeWidth={iconStroke} />,
                onPress: () => router.push({ pathname: '/tache', params: { id: task.id } }),
              },
              isScheduled(task)
                ? {
                    key: 'unschedule',
                    label: 'Renvoyer à planifier',
                    disabled: locked,
                    icon: (c) => <CalendarMinus size={20} color={c} strokeWidth={iconStroke} />,
                    onPress: change((c) => unscheduleTask(c, task.id), `Remis à planifier : ${label}`),
                  }
                : {
                    key: 'place',
                    label: 'Placer sur le planning',
                    detail: 'Choisir le jour et les personnes',
                    disabled: locked,
                    icon: (c) => <CalendarPlus size={20} color={c} strokeWidth={iconStroke} />,
                    onPress: () => router.push({ pathname: '/tache', params: { id: task.id } }),
                  },
            ],
          },
          { title: number ? `Dossier ${number}` : undefined, actions: number ? dossier.actions(number, ['folder', 'fiche', 'repertoire', 'creation']) : [] },
          {
            actions: [
              {
                key: 'delete',
                label: 'Supprimer la tâche',
                detail: 'Annulable juste après',
                destructive: true,
                disabled: locked,
                icon: (c) => <Trash2 size={20} color={c} strokeWidth={iconStroke} />,
                onPress: change((c) => deleteTask(c, task.id), `Tâche supprimée : ${label}`),
              },
            ],
          },
        ],
      };
    }
    if (menu.kind === 'absence') {
      return {
        kicker: 'Absence',
        title: menu.label,
        subtitle: '',
        sections: [
          {
            actions: [
              {
                key: 'open',
                label: 'Voir l’absence',
                icon: (c) => <Eye size={20} color={c} strokeWidth={iconStroke} />,
                onPress: () => router.push({ pathname: '/absence', params: { id: menu.absenceId } }),
              },
              {
                key: 'delete',
                label: 'Supprimer l’absence',
                detail: 'Annulable juste après',
                destructive: true,
                disabled: locked,
                icon: (c) => <Trash2 size={20} color={c} strokeWidth={iconStroke} />,
                onPress: change((c) => deleteAbsence(c, menu.absenceId), `Absence supprimée : ${menu.label}`),
              },
            ],
          },
        ],
      };
    }
    const person = data.resources.find((resource) => resource.id === menu.rowKey);
    const day = instantDay(menu.start);
    const half = view === 'day' ? '' : minuteOfDay(menu.start) >= 12 * 60 ? ' · après-midi' : ' · matin';
    const who = person ? person.initials || person.name : 'Non affecté';
    return {
      kicker: person?.name || who,
      title: `${describeDay(day)}${half}`,
      subtitle: '',
      sections: [
        {
          actions: [
            {
              key: 'new',
              label: 'Nouvelle tâche ici',
              detail: `Saisie rapide sur ${who}`,
              icon: (c) => <Plus size={20} color={c} strokeWidth={iconStroke} />,
              onPress: () => newTaskAt(menu.rowKey, { start: menu.start, end: menu.end }),
            },
          ],
        },
        {
          title: person ? `Absence de ${who} ce jour` : undefined,
          actions: person
            ? Object.entries(ABSENCE_LABELS).map(([reason, text]) => ({
                key: reason,
                label: text,
                disabled: locked,
                onPress: change(
                  (c) => createAbsence(c, { resourceId: person.id, firstDay: day, reason }),
                  `Absence ${text} ${who} ${describeDay(day)}`,
                ),
              }))
            : [],
        },
      ],
    };
  };
  const sheet = menuSheet();
  const focusTasks = data && focus ? dossierTasks(data.tasks, focus) : [];

  const onUndo = async () => {
    setActionError('');
    try {
      await runUndo();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : 'Annulation impossible.');
    }
  };

  const month = view === 'month';
  const trackWidth = month && board ? board.columns.length * MONTH_DAY_WIDTH : undefined;

  return (
    <Screen>
      <View style={styles.toolbar}>
        <View style={styles.segments} accessibilityRole="tablist">
          {(['day', 'week', 'month'] as ViewKind[]).map((kind) => (
            <Pressable
              key={kind}
              accessibilityRole="tab"
              accessibilityState={{ selected: view === kind }}
              onPress={() => setView(kind)}
              style={[styles.segment, view === kind && styles.segmentOn]}
            >
              <Text style={[styles.segmentText, view === kind && styles.segmentTextOn]}>{VIEW_LABELS[kind]}</Text>
            </Pressable>
          ))}
        </View>
        <Pressable accessibilityRole="button" onPress={() => setAnchor(shownDay(todayNumber()))} style={styles.todayButton}>
          <Text style={styles.todayText}>Aujourd’hui</Text>
        </Pressable>
      </View>
      <View style={styles.periodRow}>
        <Pressable accessibilityRole="button" accessibilityLabel="Précédent" onPress={() => setAnchor(shiftView(view, anchor, -1))} style={styles.arrow}>
          <ChevronLeft size={22} color={colors.accent700} strokeWidth={iconStroke} />
        </Pressable>
        <View style={styles.period}>
          <Text style={styles.periodText} numberOfLines={1}>
            {periodText(view, anchor)}
          </Text>
          <Muted>{loading ? 'Actualisation…' : settings.demo ? 'Mode démo' : loadedAt ? `À jour, ${clock(loadedAt)}` : ''}</Muted>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Suivant" onPress={() => setAnchor(shiftView(view, anchor, 1))} style={styles.arrow}>
          <ChevronRight size={22} color={colors.accent700} strokeWidth={iconStroke} />
        </Pressable>
      </View>

      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load()} tintColor={colors.accent700} />}
      >
        {error ? <Notice tone="error" message={error} action={{ label: 'Réessayer', onPress: () => void load() }} /> : null}
        {actionError ? <Notice tone="error" message={actionError} /> : null}
        {notice ? <Notice message={notice} action={{ label: 'Compris', onPress: clearNotice }} /> : null}
        {undo ? (
          <View style={styles.toast}>
            <Text style={styles.toastText} numberOfLines={2}>
              {undo.label}
            </Text>
            <Pressable accessibilityRole="button" onPress={() => void onUndo()} style={styles.toastAction}>
              <Text style={styles.toastActionText}>Annuler</Text>
            </Pressable>
          </View>
        ) : null}
        {focus ? (
          <View style={styles.focus}>
            <DossierNumber style={styles.focusNumber}>{focus}</DossierNumber>
            <Text style={styles.focusText} numberOfLines={1}>
              {focusSummary(focusTasks)}
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Ne plus mettre le dossier en évidence"
              onPress={() => setFocus('')}
              style={styles.focusClose}
            >
              <X size={18} color={colors.accent700} strokeWidth={iconStroke} />
            </Pressable>
          </View>
        ) : null}

        {board ? (
          <View style={styles.board}>
            <View style={{ width: NAME_WIDTH }}>
              <View style={styles.headerCell} />
              {board.groups.map((group) => (
                <View key={group.key}>
                  <View style={styles.teamCell}>
                    <Caps style={styles.team} numberOfLines={1}>
                      {group.name}
                    </Caps>
                  </View>
                  {group.rows.map((row) => (
                    <View key={row.key} style={[styles.nameCell, { height: rowHeight(row) }]}>
                      <Text style={styles.name} numberOfLines={1} accessibilityLabel={row.name}>
                        {row.label}
                      </Text>
                    </View>
                  ))}
                </View>
              ))}
            </View>
            <ScrollView
              key={`${view}:${board.columns[0]?.day}`}
              horizontal={month}
              scrollEnabled={month}
              showsHorizontalScrollIndicator={month}
              contentOffset={{ x: month ? todayOffset(board) : 0, y: 0 }}
              style={styles.flex}
            >
              <View style={trackWidth ? { width: trackWidth } : styles.flex}>
                <Header board={board} view={view} />
                {board.groups.map((group) => (
                  <View key={group.key}>
                    <View style={styles.teamCell} />
                    {group.rows.map((row) => (
                      <Track
                        key={row.key}
                        row={row}
                        board={board}
                        view={view}
                        focus={focus}
                        onCell={onCell}
                        onCellLongPress={onCellLongPress}
                        onBar={onBar}
                        onBarLongPress={onBarLongPress}
                      />
                    ))}
                  </View>
                ))}
              </View>
            </ScrollView>
          </View>
        ) : null}
        {board && board.groups.length === 0 ? <Muted style={styles.none}>Personne dans le planning.</Muted> : null}

        {board ? <Backlog cards={board.backlog} data={data} focus={focus} onLongPress={onCardLongPress} /> : null}
        {board ? <Muted style={styles.hint}>Touchez une tâche pour la modifier, restez appuyé pour ses actions.</Muted> : null}
      </ScrollView>

      <ActionSheet
        visible={sheet !== null}
        onClose={() => setMenu(null)}
        kicker={sheet?.kicker}
        title={sheet?.title}
        subtitle={sheet?.subtitle}
        accent={sheet?.accent}
        sections={sheet?.sections ?? []}
      />

      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Saisie rapide"
        onPress={() => router.push('/saisie')}
        style={styles.quick}
      >
        <Zap size={18} color={colors.accent700} strokeWidth={iconStroke} />
        <Text style={styles.quickText} numberOfLines={1}>
          Saisie rapide : <Text style={styles.quickHint}>5252 relevé lun LM</Text>
        </Text>
      </Pressable>
    </Screen>
  );
}

/** The live tasks of a dossier. */
function dossierTasks(tasks: readonly PlanningTask[], number: string): PlanningTask[] {
  return tasks.filter((task) => !task.deleted_at && (task.project_number ?? '').trim() === number);
}

/** « 2 tâches planifiées · 1 à planifier », or that there is none. */
function focusSummary(tasks: PlanningTask[]): string {
  const scheduled = tasks.filter(isScheduled).length;
  const waiting = tasks.length - scheduled;
  if (!tasks.length) return 'Aucune tâche dans le planning';
  const parts = [];
  if (scheduled) parts.push(`${scheduled} tâche${scheduled > 1 ? 's' : ''} planifiée${scheduled > 1 ? 's' : ''}`);
  if (waiting) parts.push(`${waiting} à planifier`);
  return parts.join(' · ');
}

/** Month view: today's column a little in from the left edge. */
function todayOffset(board: Board): number {
  const index = board.columns.findIndex((column) => column.day >= todayNumber());
  return index > 1 ? (index - 1) * MONTH_DAY_WIDTH : 0;
}

function Header({ board, view }: { board: Board; view: ViewKind }) {
  if (view === 'day') {
    const span = DAY_END - DAY_START;
    return (
      <View style={styles.headerCell}>
        {DAY_TICKS.map((minute) => (
          <Text
            key={minute}
            style={[
              styles.tick,
              { left: `${((minute - DAY_START) / span) * 100}%` },
              minute === DAY_START && styles.tickFirst,
              minute === DAY_END && styles.tickLast,
            ]}
          >
            {`${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`}
          </Text>
        ))}
        {board.columns[0]?.label ? <Text style={styles.holidayTag}>{board.columns[0].label}</Text> : null}
      </View>
    );
  }
  const compact = view === 'month';
  return (
    <View style={[styles.headerCell, styles.headerDays]}>
      {board.columns.map((column) => (
        <View key={column.day} style={styles.flex} accessibilityLabel={column.label || undefined}>
          <Caps style={[styles.dayHeading, column.day === todayNumber() && styles.today]} numberOfLines={1}>
            {columnHeading(column.day, compact)}
          </Caps>
          {column.label && !compact ? (
            <Text style={styles.holiday} numberOfLines={1}>
              {column.label}
            </Text>
          ) : null}
        </View>
      ))}
    </View>
  );
}

function Shading({ columns, view }: { columns: Column[]; view: ViewKind }) {
  if (view === 'day') {
    const column = columns[0];
    const span = DAY_END - DAY_START;
    const pct = (minute: number) => `${((minute - DAY_START) / span) * 100}%` as const;
    if (!column) return null;
    if (column.closed) return <View style={[styles.closed, styles.fill]} />;
    return (
      <>
        <View style={[styles.closed, { left: pct(NOON), width: pct(DAY_START + AFTERNOON - NOON) }]} />
        {!column.worked[1] ? <View style={[styles.closed, { left: pct(NOON), right: 0 }]} /> : null}
      </>
    );
  }
  const units = columns.length * 2;
  const pct = (unit: number) => `${(unit / units) * 100}%` as const;
  return (
    <>
      {columns.map((column, index) => (
        <Fragment key={column.day}>
          {!column.worked[0] ? <View style={[styles.closed, { left: pct(index * 2), width: pct(1) }]} /> : null}
          {!column.worked[1] ? <View style={[styles.closed, { left: pct(index * 2 + 1), width: pct(1) }]} /> : null}
          <View style={[styles.dayLine, { left: pct(index * 2) }]} />
        </Fragment>
      ))}
    </>
  );
}

function Track({
  row,
  board,
  view,
  focus,
  onCell,
  onCellLongPress,
  onBar,
  onBarLongPress,
}: {
  row: WeekRow;
  board: Board;
  view: ViewKind;
  focus: string;
  onCell: (rowKey: string, unit: number) => void;
  onCellLongPress: (rowKey: string, unit: number) => void;
  onBar: (segment: Segment, rowKey: string) => void;
  onBarLongPress: (segment: Segment) => void;
}) {
  const track = useRef<HostInstance>(null);
  const height = rowHeight(row);
  const pct = (unit: number) => `${(unit / board.units) * 100}%` as const;
  const at = (event: GestureResponderEvent, then: (rowKey: string, unit: number) => void) => {
    // pageX exists on phones and in the browser alike (locationX does not on the web).
    const pageX = event.nativeEvent.pageX;
    track.current?.measure((_x, _y, width, _height, left) => {
      if (!width) return;
      const unit = Math.floor(((pageX - left) / width) * board.units);
      then(row.key, Math.min(board.units - 1, Math.max(0, unit)));
    });
  };
  return (
    <Pressable
      ref={track}
      accessibilityLabel={`Ligne ${row.name} : toucher pour une saisie rapide, appui long pour une absence`}
      onPress={(event) => at(event, onCell)}
      onLongPress={(event) => at(event, onCellLongPress)}
      delayLongPress={350}
      style={[styles.track, { height }]}
    >
      <Shading columns={board.columns} view={view} />
      {row.segments.map((segment) => (
        <Bar
          key={`${segment.id}:${segment.lane}`}
          segment={segment}
          focused={Boolean(focus) && (segment.task?.project_number ?? '').trim() === focus}
          dimmed={Boolean(focus) && segment.kind === 'task' && (segment.task?.project_number ?? '').trim() !== focus}
          style={{ left: pct(segment.startSlot), width: pct(segment.span), top: segment.lane * LANE_HEIGHT + 4 }}
          onPress={() => onBar(segment, row.key)}
          onLongPress={() => onBarLongPress(segment)}
        />
      ))}
    </Pressable>
  );
}

function Bar({
  segment,
  focused,
  dimmed,
  style,
  onPress,
  onLongPress,
}: {
  segment: Segment;
  focused: boolean;
  dimmed: boolean;
  style: { left: `${number}%`; width: `${number}%`; top: number };
  onPress: () => void;
  onLongPress: () => void;
}) {
  if (segment.kind === 'absence') {
    return (
      <Pressable
        accessibilityRole={segment.absenceId ? 'button' : undefined}
        accessibilityLabel={segment.label}
        disabled={!segment.absenceId}
        onPress={onPress}
        onLongPress={onLongPress}
        delayLongPress={350}
        style={[styles.bar, styles.absence, style]}
      >
        <Text style={styles.absenceText} numberOfLines={1}>
          {segment.label}
        </Text>
      </Pressable>
    );
  }
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${segment.task ? taskLabel(segment.task) : segment.label}${segment.badge ? `, ${segment.badge}` : ''}`}
      accessibilityHint="Appui long : actions de la tâche"
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={350}
      style={[
        styles.bar,
        style,
        { borderColor: segment.color, backgroundColor: `${segment.color}33` },
        segment.provisional && styles.provisional,
        segment.done && styles.done,
        focused && styles.focused,
        dimmed && styles.dimmed,
      ]}
    >
      <Text style={styles.barText} numberOfLines={1}>
        {/* The badge leads so that a narrow bar still shows « 1/2 » or « ×2 ». */}
        {segment.badge ? <Text style={styles.badge}>{segment.badge} </Text> : null}
        {segment.task?.urgent ? <Text style={styles.urgent}>! </Text> : null}
        {segment.label}
        {segment.provisional ? ' ?' : ''}
      </Text>
    </Pressable>
  );
}

function Backlog({
  cards,
  data,
  focus,
  onLongPress,
}: {
  cards: PlanningTask[];
  data: ReturnType<typeof usePlanning>['data'];
  focus: string;
  onLongPress: (task: PlanningTask) => void;
}) {
  return (
    <View style={styles.backlog}>
      <View style={styles.backlogHead}>
        <Text style={styles.backlogTitle}>À planifier</Text>
        <Caps>
          {cards.length} carte{cards.length > 1 ? 's' : ''}
        </Caps>
      </View>
      {cards.length === 0 ? <Muted>Aucune carte. Une saisie sans jour arrive ici.</Muted> : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.cards}>
        {cards.map((task) => {
          const color = taskTypeOf(data?.meta ?? null, task.task_type).color;
          const mine = (task.project_number ?? '').trim() === focus;
          return (
            <Pressable
              key={task.id}
              accessibilityRole="button"
              accessibilityLabel={taskLabel(task)}
              accessibilityHint="Appui long : actions de la carte"
              onPress={() => router.push({ pathname: '/tache', params: { id: task.id } })}
              onLongPress={() => onLongPress(task)}
              delayLongPress={350}
              style={[
                styles.card,
                { borderColor: color, backgroundColor: `${color}22` },
                focus && mine && styles.focused,
                focus && !mine && styles.dimmed,
              ]}
            >
              <DossierNumber style={styles.cardNumber}>{task.project_number || '—'}</DossierNumber>
              <Text style={styles.cardTitle} numberOfLines={2}>
                {(task.title ?? '').trim() || task.project_cache?.designation || 'Sans titre'}
              </Text>
              {(task.required_people ?? 1) > 1 ? <Muted>{task.required_people}p</Muted> : null}
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  fill: { left: 0, right: 0 },
  empty: { padding: 20, gap: 12 },
  toolbar: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 12, paddingTop: 8 },
  segments: { flex: 1, flexDirection: 'row', borderWidth: 1, borderColor: colors.line },
  segment: { flex: 1, minHeight: 36, alignItems: 'center', justifyContent: 'center' },
  segmentOn: { backgroundColor: colors.accent100 },
  segmentText: { fontFamily: fonts.heading, fontSize: 14, letterSpacing: 1, textTransform: 'uppercase', color: colors.muted },
  segmentTextOn: { color: colors.accent900 },
  todayButton: { minHeight: 36, justifyContent: 'center', paddingHorizontal: 10, borderWidth: 1, borderColor: colors.line },
  todayText: { fontFamily: fonts.heading, fontSize: 14, letterSpacing: 1, textTransform: 'uppercase', color: colors.accent700 },
  periodRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  arrow: { width: touch, height: touch, alignItems: 'center', justifyContent: 'center' },
  period: { flex: 1, alignItems: 'center' },
  periodText: { fontFamily: fonts.headingBold, fontSize: 17, textTransform: 'uppercase', color: colors.text },
  content: { padding: 16, paddingBottom: 32, gap: 10 },
  toast: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 12,
    backgroundColor: colors.accent900,
  },
  toastText: { flex: 1, fontFamily: fonts.body, fontSize: 14, color: '#ffffff' },
  toastAction: { minHeight: touch, justifyContent: 'center', paddingHorizontal: 14 },
  toastActionText: { fontFamily: fonts.headingBold, fontSize: 15, letterSpacing: 1, textTransform: 'uppercase', color: colors.accent200 },
  board: { flexDirection: 'row' },
  headerCell: { height: 34, borderBottomWidth: 1, borderBottomColor: colors.text, position: 'relative' },
  headerDays: { flexDirection: 'row', alignItems: 'flex-end', paddingBottom: 2 },
  dayHeading: { textAlign: 'center' },
  today: { color: colors.accent700 },
  holiday: { fontFamily: fonts.body, fontSize: 9, color: colors.absenceInk, textAlign: 'center' },
  holidayTag: { position: 'absolute', top: 0, right: 0, fontFamily: fonts.body, fontSize: 11, color: colors.absenceInk },
  tick: { position: 'absolute', bottom: 2, fontFamily: fonts.heading, fontSize: 11, color: colors.muted, marginLeft: -2 },
  tickFirst: { marginLeft: 0 },
  tickLast: { marginLeft: -30 },
  teamCell: { height: TEAM_HEIGHT, justifyContent: 'flex-end', paddingBottom: 4 },
  team: { color: colors.accent700, width: 240 },
  nameCell: { justifyContent: 'center', borderBottomWidth: 1, borderBottomColor: colors.lineSoft },
  name: { fontFamily: fonts.headingBold, fontSize: 14, letterSpacing: 0.5, color: colors.text },
  track: { position: 'relative', borderBottomWidth: 1, borderBottomColor: colors.lineSoft },
  dayLine: { position: 'absolute', top: 0, bottom: 0, width: 1, backgroundColor: colors.lineSoft },
  closed: { position: 'absolute', top: 0, bottom: 0, backgroundColor: colors.closed },
  bar: {
    position: 'absolute',
    height: BAR_HEIGHT,
    borderWidth: 1,
    paddingHorizontal: 4,
    justifyContent: 'center',
    overflow: 'hidden',
  },
  barText: { fontFamily: fonts.heading, fontSize: 12, color: colors.text },
  badge: { fontFamily: fonts.headingBold, color: colors.accent900 },
  urgent: { fontFamily: fonts.headingBold, color: colors.err },
  provisional: { borderStyle: 'dashed' },
  done: { opacity: 0.45 },
  focused: { borderWidth: 2.5, borderColor: colors.accent900 },
  dimmed: { opacity: 0.3 },
  focus: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 12,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.accent100,
  },
  focusNumber: { fontSize: 15 },
  focusText: { flex: 1, fontFamily: fonts.body, fontSize: 14, color: colors.accent900 },
  focusClose: { width: touch, height: touch, alignItems: 'center', justifyContent: 'center' },
  hint: { fontSize: 13, marginTop: 4 },
  absence: { backgroundColor: colors.absenceSoft, borderColor: colors.absence },
  absenceText: { fontFamily: fonts.heading, fontSize: 12, color: colors.absenceInk },
  none: { paddingVertical: 16 },
  backlog: { marginTop: 10, gap: 8 },
  backlogHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between' },
  backlogTitle: { fontFamily: fonts.headingBold, fontSize: 17, letterSpacing: 0.8, textTransform: 'uppercase', color: colors.text },
  cards: { gap: 8 },
  card: { width: 160, padding: 8, gap: 2, borderWidth: 1, minHeight: touch },
  cardNumber: { fontSize: 14 },
  cardTitle: { fontFamily: fonts.bodyMedium, fontSize: 14, color: colors.text },
  quick: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    minHeight: 48,
    marginHorizontal: 12,
    marginBottom: 8,
    paddingHorizontal: 12,
    borderWidth: 1,
    borderColor: colors.accent700,
    backgroundColor: colors.surface,
  },
  quickText: { flex: 1, fontFamily: fonts.heading, fontSize: 15, letterSpacing: 0.6, color: colors.accent800 },
  quickHint: { fontFamily: fonts.body, color: colors.subtle },
});
