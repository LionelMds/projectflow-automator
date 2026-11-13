import { router, useFocusEffect, useLocalSearchParams } from 'expo-router';
import { CalendarMinus, CalendarPlus, ChevronLeft, ChevronRight, Eye, PenLine, Plus, Trash2, Users, X, Zap } from 'lucide-react-native';
import { Fragment, memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentRef } from 'react';
import {
  Animated,
  AppState,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
  type GestureResponderEvent,
  type HostInstance,
  type NativeScrollEvent,
  type NativeSyntheticEvent,
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
import { parseDate, todayNumber, weekday } from '@/lib/planning/walltime';
import {
  barParts,
  buildBoard,
  DAY_END,
  DAY_START,
  NOON,
  AFTERNOON,
  UNASSIGNED,
  type Board,
  type Column,
  type Segment,
  type WeekGroup,
  type WeekRow,
} from '@/lib/planning/week';
import { withDossiers } from '@/lib/planning/dossiers';
import { useRepertoire } from '@/lib/repertoire/RepertoireContext';
import { useRestriction } from '@/lib/restriction/RestrictionContext';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

const NAME_WIDTH = 44;
const CONTENT_PAD = 10;
/** Where the tracks begin, from the left edge of the screen. */
const TRACK_LEFT = CONTENT_PAD + NAME_WIDTH;
/**
 * Height of a lane before any zoom. Day and week bars stack the dossier over the title,
 * on a row tall enough for a finger; the month's, a column of 34 px each, keep one line.
 */
const LANE_BASE: Record<ViewKind, number> = { day: 42, week: 42, month: 30 };
const LANE_GAP = 4;
/**
 * Pinching widens the days and, more gently, raises the rows; a bar then shows as many
 * lines as it has room for (dossier, title, client, hours, people, notes), as the events
 * of the iPhone's Calendar do.
 */
const ZOOM_MAX: Record<ViewKind, number> = { day: 3, week: 4, month: 6 };
const ZOOM_STEP = 0.02;
const NUMBER_LINE = 16;
const DETAIL_LINE = 14;
/** From this height a bar stacks its lines; under it, one line. */
const STACKED_FROM = 34;
/** Of a bar's height and width, what its border and padding take from its texts. */
const BAR_FRAME = 4;
const BAR_PADDING = 6;
/** Mean width of a character, to judge whether a text needs a second line (13 px condensed bold, 11 px regular). */
const HEAD_CHAR = 6;
const TEXT_CHAR = 5.6;
/** A text never takes more lines than this, however much room is left. */
const MAX_WRAP = 4;
/** A day this wide has room for its full name, and shows where noon falls. */
const WIDE_DAY = 110;
const NARROW_DAY = 44;
const TINY_DAY = 22;
/** How much a day's heading can say, from its width. */
type Grain = 'tiny' | 'narrow' | 'normal' | 'wide';
const TEAM_HEIGHT = 30;
/** A horizontal drag this long turns the page (previous or next day, week). */
const SWIPE_START = 24;
const SWIPE_TURN = 56;
const WEEKDAY_LETTERS = 'LMMJV';
const MONTH_DAY_WIDTH = 34;
const REFRESH_SECONDS = 30;
const DAY_TICKS = [DAY_START, 9 * 60, NOON, AFTERNOON, DAY_END];
/** The day view zoomed in: every hour that has room beside its neighbours. */
const HOUR_TICKS = [DAY_START, ...[8, 9, 10, 11, 12, 13, 14, 15].map((hour) => hour * 60), DAY_END];
const HOUR_TICKS_FROM = 44;

function clock(date: Date | null): string {
  if (!date) return '';
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Rows grow more slowly than days widen: at ×4 a lane is a little over twice as tall. */
function laneHeight(view: ViewKind, zoom: number): number {
  return Math.round(LANE_BASE[view] * Math.pow(Math.max(1, zoom), 0.6));
}

function rowHeight(row: WeekRow, lane: number): number {
  return row.lanes * lane + LANE_GAP;
}

/** A height in the list of rows, split into what a zoom leaves as it is and what it scales. */
type Spot = { fixed: number; lanes: number };

/** What lies `y` down the list: team headings and row margins keep their height, lanes grow. */
function spotAt(groups: readonly WeekGroup[], lane: number, y: number): Spot {
  let top = 0;
  let fixed = 0;
  let lanes = 0;
  for (const group of groups) {
    if (y < top + TEAM_HEIGHT) return { fixed: fixed + Math.max(0, y - top), lanes };
    top += TEAM_HEIGHT;
    fixed += TEAM_HEIGHT;
    for (const row of group.rows) {
      const height = row.lanes * lane;
      if (y < top + LANE_GAP + height) {
        const inside = y - top;
        if (inside < LANE_GAP) return { fixed: fixed + inside, lanes };
        return { fixed: fixed + LANE_GAP, lanes: lanes + (inside - LANE_GAP) / lane };
      }
      top += LANE_GAP + height;
      fixed += LANE_GAP;
      lanes += row.lanes;
    }
  }
  return { fixed: fixed + (y - top), lanes };
}

/** Distance between the two fingers of a pinch. */
function spread(touches: readonly { pageX: number; pageY: number }[]): number {
  return Math.hypot(touches[0].pageX - touches[1].pageX, touches[0].pageY - touches[1].pageY);
}

/** Minutes since midnight, for the « now » line of the day view. */
function minuteNow(): number {
  const now = new Date();
  return now.getHours() * 60 + now.getMinutes();
}

const STATUSES: TaskStatus[] = ['provisional', 'confirmed', 'done'];

/** What a long press was on: a task (bar or card), an absence, or an empty half-day of a row. */
type Menu =
  | { kind: 'task'; task: PlanningTask }
  | { kind: 'absence'; absenceId: string; label: string }
  | { kind: 'cell'; rowKey: string; start: number; end: number | null };

export default function PlanningScreen() {
  const { status } = useAuth();
  const { settings, loaded, save } = useSettings();
  const [choosingPeople, setChoosingPeople] = useState(false);
  const planning = usePlanning();
  const { data, loading, error, loadedAt, load, notice, clearNotice, undo, runUndo, apply, author, editBlocker } = planning;
  const [view, setView] = useState<ViewKind>('week');
  const [anchor, setAnchor] = useState(() => shownDay(todayNumber()));
  const [actionError, setActionError] = useState('');
  const [menu, setMenu] = useState<Menu | null>(null);
  const dossier = useDossierActions();
  const { restricted } = useRestriction();
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

  // Zoom, one per view. Unzoomed, the day and the week fill the width of the screen and the
  // month gives 34 px to a day; the tracks are `base × zoom` wide and scroll once wider.
  const { width: windowWidth } = useWindowDimensions();
  const [zooms, setZooms] = useState<Record<ViewKind, number>>({ day: 1, week: 1, month: 1 });
  const zoomRef = useRef(zooms);
  const [pinching, setPinching] = useState(false);
  const viewport = Math.max(0, windowWidth - 2 * CONTENT_PAD - NAME_WIDTH);
  const base = view === 'month' && board ? board.columns.length * MONTH_DAY_WIDTH : viewport;
  // The month can also be pinched in until all of it fits the screen.
  const minZoom = base > viewport && base > 0 ? viewport / base : 1;
  const maxZoom = ZOOM_MAX[view];
  const zoom = zooms[view];
  const trackWidth = Math.round(base * zoom);
  const scrollable = trackWidth > viewport + 1;
  const lane = laneHeight(view, zoom);
  const columnCount = board?.columns.length || 1;
  const dayWidth = view === 'day' ? trackWidth : trackWidth / columnCount;

  const boardRef = useRef<HostInstance>(null);
  const sideways = useRef<ComponentRef<typeof Animated.ScrollView>>(null);
  const scrollX = useRef(0);
  const scrollAfterZoom = useRef<number | null>(null);
  const firstDay = board?.columns[0]?.day;
  const page = `${view}:${firstDay}`;
  const maxScroll = Math.max(0, trackWidth - viewport);

  // The sideways scroll, as a value the frozen heading follows without a round trip
  // through React: on a phone it is moved on the thread that scrolls.
  const [scrollAnim] = useState(() => new Animated.Value(0));
  const headingShift = useMemo(() => Animated.multiply(scrollAnim, -1), [scrollAnim]);
  const onScrollEvent = useMemo(
    () => Animated.event([{ nativeEvent: { contentOffset: { x: scrollAnim } } }], { useNativeDriver: Platform.OS !== 'web' }),
    [scrollAnim],
  );
  // Where that scroll came to rest: a bar that begins off the left edge then writes its
  // text from the edge, not from its hidden start. Following every frame of the scroll
  // from here would lag behind the finger; the text moves once the scroll settles.
  const [settled, setSettled] = useState({ page, x: 0 });
  useEffect(() => {
    let settling: ReturnType<typeof setTimeout> | undefined;
    const listener = scrollAnim.addListener(({ value }) => {
      scrollX.current = value;
      clearTimeout(settling);
      settling = setTimeout(() => setSettled({ page, x: value }), 120);
    });
    return () => {
      clearTimeout(settling);
      scrollAnim.removeListener(listener);
    };
  }, [scrollAnim, page]);
  /** Put the tracks at `x`, and the heading with them without waiting for the scroll's echo. */
  const scrollTracksTo = useCallback(
    (x: number) => {
      sideways.current?.scrollTo({ x, animated: false });
      scrollAnim.setValue(x);
      scrollX.current = x;
    },
    [scrollAnim],
  );

  // A pinch in progress. The scroll view stays where it was (`from`) and the tracks are
  // shifted so that they show from `x`: width and shift then change in one and the same
  // frame, where moving the scroll view itself would arrive a frame late and shake them.
  // The same goes for the list of rows, which grow with the zoom: the row under the
  // fingers stays under them (`y`, `fromY`).
  const [pinchAt, setPinchAt] = useState<{ x: number; from: number; y: number; fromY: number; holdY: number } | null>(null);
  const pinch = useRef<{ distance: number; zoom: number; instant: number; spot: Spot; lane: number; contentHeight: number } | null>(null);
  const listBox = useRef<HostInstance>(null);
  const list = useRef<ComponentRef<typeof ScrollView>>(null);
  const listAt = useRef({ top: 0, height: 0 });
  const scrollY = useRef(0);
  const contentHeight = useRef(0);
  const pinchFromY = useRef(0);
  const pinchY = useRef(0);
  const scrollAfterZoomY = useRef<number | null>(null);
  const pinched = useRef(false);
  const pinchFrom = useRef(0);
  const pinchX = useRef(0);
  const pinchNext = useRef<{ distance: number; focalX: number; focalY: number } | null>(null);
  const pinchFrame = useRef<number | null>(null);

  // Who is shown: everyone but the people left out on this phone (kept with its settings).
  const groups = useMemo(() => {
    const hidden = new Set(settings.planningHidden);
    return board
      ? board.groups.map((group) => ({ ...group, rows: group.rows.filter((row) => !hidden.has(row.key)) })).filter((group) => group.rows.length)
      : [];
  }, [board, settings.planningHidden]);
  const laneCount = groups.reduce((sum, group) => sum + group.rows.reduce((lanes, row) => lanes + row.lanes, 0), 0);

  /** Zoom to `value`, keeping the instant under `focalX` (from the left of the tracks) in place. */
  const zoomTo = useCallback(
    (value: number, focalX: number) => {
      const current = zoomRef.current[view];
      const next = clamp(Math.round(value / ZOOM_STEP) * ZOOM_STEP, minZoom, maxZoom);
      if (next === current || base <= 0) return;
      const instant = (scrollX.current + focalX) / (base * current);
      scrollAfterZoom.current = clamp(instant * base * next - focalX, 0, Math.max(0, base * next - viewport));
      zoomRef.current = { ...zoomRef.current, [view]: next };
      setZooms(zoomRef.current);
      setSettled({ page, x: scrollAfterZoom.current });
    },
    [view, page, base, viewport, minZoom, maxZoom, setSettled],
  );
  useLayoutEffect(() => {
    if (pinchAt) return;
    if (scrollAfterZoom.current !== null) scrollTracksTo(scrollAfterZoom.current);
    scrollAfterZoom.current = null;
    if (scrollAfterZoomY.current !== null) {
      list.current?.scrollTo({ y: scrollAfterZoomY.current, animated: false });
      scrollY.current = scrollAfterZoomY.current;
    }
    scrollAfterZoomY.current = null;
  }, [zoom, pinchAt, scrollTracksTo]);

  // A new page opens with today a column in from the left edge, when today is on it; one
  // reached by pulling past an edge opens at the edge it was entered by.
  const [entry, setEntry] = useState<'start' | 'end' | null>(null);
  const pageStart = useMemo(() => {
    const index = view === 'day' || !board ? -1 : board.columns.findIndex((column) => column.day >= todayNumber());
    const today = index > 1 ? clamp((index - 1) * dayWidth, 0, maxScroll) : 0;
    return { x: entry === 'end' ? maxScroll : entry === 'start' ? 0 : today, y: 0 };
    // Only where a page opens: zooming must not send the board back to today.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);
  useLayoutEffect(() => {
    // The browser's scroll view does not read `contentOffset`.
    scrollTracksTo(pageStart.x);
  }, [pageStart, scrollTracksTo]);

  /** The previous or next day, week or month, as the arrows, a swipe or a pull past an edge ask. */
  const lastTurn = useRef(0);
  const turnPage = (steps: number, from: 'arrow' | 'drag') => {
    // A pull past the edge can be seen twice (by the scroll and by the board): once is enough.
    const now = Date.now();
    if (from === 'drag' && now - lastTurn.current < 400) return;
    lastTurn.current = now;
    if (from === 'drag') selectionFeedback();
    setEntry(from === 'drag' ? (steps < 0 ? 'end' : 'start') : null);
    setAnchor(shiftView(view, anchor, steps));
  };

  // Two fingers zoom. One finger dragged sideways turns the page, as the arrows do: at once
  // when the tracks fit the screen; zoomed in, that drag scrolls them, and turns the page
  // only when it pulls past the first or the last instant shown.
  // The board takes a touch over from the bars and rows under it (the « capture » half of
  // the responder system) once it is a second finger, or one finger gone sideways.
  const touchStart = useRef({ x: 0, y: 0 });
  /** One step of a pinch, at most once a frame however many moves the fingers reported. */
  const applyPinch = () => {
    pinchFrame.current = null;
    const start = pinch.current;
    const now = pinchNext.current;
    if (!start || !now || start.distance < 1 || base <= 0) return;
    const next = clamp(Math.round((start.zoom * now.distance) / start.distance / ZOOM_STEP) * ZOOM_STEP, minZoom, maxZoom);
    const x = clamp(start.instant * base * next - now.focalX, 0, Math.max(0, base * next - viewport));
    // The rows grow with the zoom: the one under the fingers is kept under them.
    const nextLane = laneHeight(view, next);
    const height = start.contentHeight + laneCount * (nextLane - start.lane);
    const y = clamp(start.spot.fixed + start.spot.lanes * nextLane - now.focalY, 0, Math.max(0, height - listAt.current.height));
    if (next === zoomRef.current[view] && x === pinchX.current && y === pinchY.current) return;
    pinchX.current = x;
    pinchY.current = y;
    zoomRef.current = { ...zoomRef.current, [view]: next };
    setZooms(zoomRef.current);
    // `holdY`: the list keeps at least the height it is scrolled to, however far the rows shrink.
    setPinchAt({ x, from: pinchFrom.current, y, fromY: pinchFromY.current, holdY: pinchFromY.current + listAt.current.height });
  };
  const endPinch = () => {
    // The last move may still be waiting for its frame: it counts.
    if (pinchFrame.current !== null) {
      cancelAnimationFrame(pinchFrame.current);
      applyPinch();
    }
    pinchNext.current = null;
    if (pinched.current) {
      scrollAfterZoom.current = pinchX.current;
      scrollAfterZoomY.current = pinchY.current;
      setSettled({ page, x: pinchX.current });
    }
    pinch.current = null;
    pinched.current = false;
    setPinchAt(null);
    setPinching(false);
  };
  /** Which way a sideways drag pulls past an edge of the zoomed tracks: -1, 1, or 0 when it does not. */
  const pullsPast = (dx: number): number => {
    if (!scrollable) return 0;
    if (dx > 0 && scrollX.current <= 1) return -1;
    if (dx < 0 && scrollX.current >= maxScroll - 1) return 1;
    return 0;
  };
  const gestures = {
    onStartShouldSetResponderCapture: (event: GestureResponderEvent) => {
      const { touches, pageX, pageY } = event.nativeEvent;
      if (touches.length === 1) touchStart.current = { x: pageX, y: pageY };
      return touches.length === 2;
    },
    onMoveShouldSetResponderCapture: (event: GestureResponderEvent) => {
      const { touches, pageX, pageY } = event.nativeEvent;
      if (touches.length === 2) return true;
      const dx = pageX - touchStart.current.x;
      const dy = pageY - touchStart.current.y;
      if (Math.abs(dx) <= SWIPE_START || Math.abs(dx) <= 2 * Math.abs(dy)) return false;
      return !scrollable || pullsPast(dx) !== 0;
    },
    onResponderMove: (event: GestureResponderEvent) => {
      const touches = event.nativeEvent.touches;
      if (touches.length !== 2) {
        pinch.current = null;
        return;
      }
      const distance = spread(touches);
      // The middle of the two fingers, from the left of the tracks and from the top of the list.
      const focalX = (touches[0].pageX + touches[1].pageX) / 2 - TRACK_LEFT;
      const focalY = (touches[0].pageY + touches[1].pageY) / 2 - listAt.current.top;
      if (!pinch.current) {
        if (!pinched.current) {
          pinchFrom.current = scrollX.current;
          pinchX.current = scrollX.current;
          pinchFromY.current = scrollY.current;
          pinchY.current = scrollY.current;
          setPinching(true);
        }
        pinched.current = true;
        const current = zoomRef.current[view];
        const currentLane = laneHeight(view, current);
        pinch.current = {
          distance,
          zoom: current,
          instant: base > 0 ? (pinchX.current + focalX) / (base * current) : 0,
          // What is under the fingers, in the list: so much that does not grow, so many lanes that do.
          spot: spotAt(groups, currentLane, pinchY.current + focalY),
          lane: currentLane,
          contentHeight: contentHeight.current,
        };
        return;
      }
      pinchNext.current = { distance, focalX, focalY };
      if (pinchFrame.current === null) pinchFrame.current = requestAnimationFrame(applyPinch);
    },
    // The lists under the fingers would otherwise take a pinch for a scroll.
    onResponderTerminationRequest: () => !pinched.current,
    onResponderTerminate: endPinch,
    onResponderRelease: (event: GestureResponderEvent) => {
      if (pinched.current) {
        endPinch();
        return;
      }
      const dx = event.nativeEvent.pageX - touchStart.current.x;
      if (Math.abs(dx) < SWIPE_TURN) return;
      if (!scrollable) turnPage(dx < 0 ? 1 : -1, 'drag');
      else if (pullsPast(dx) !== 0) turnPage(pullsPast(dx), 'drag');
    },
  };
  /** On an iPhone the scroll itself stretches past its edges: let go far enough out, it turns the page. */
  const onScrollEndDrag = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
    if (!scrollable || pinched.current) return;
    if (contentOffset.x < -SWIPE_TURN) turnPage(-1, 'drag');
    else if (contentOffset.x > contentSize.width - layoutMeasurement.width + SWIPE_TURN) turnPage(1, 'drag');
  };

  // In a browser there are no two fingers: Ctrl + wheel (or a trackpad pinch) zooms.
  const hasBoard = board !== null && ready;
  useEffect(() => {
    if (Platform.OS !== 'web' || !hasBoard) return undefined;
    const node = boardRef.current as unknown as HTMLElement | null;
    if (!node) return undefined;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      zoomTo(zoomRef.current[view] * Math.exp(-event.deltaY / 300), event.clientX - TRACK_LEFT);
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [hasBoard, view, zoomTo]);

  /** Back to the view as it opens, when its tab is touched again. */
  const resetZoom = () => {
    zoomRef.current = { ...zoomRef.current, [view]: 1 };
    setZooms(zoomRef.current);
    scrollTracksTo(0);
    setSettled({ page, x: 0 });
  };
  const scrolledX = !scrollable ? 0 : pinchAt ? pinchAt.x : settled.page === page ? settled.x : pageStart.x;
  // While pinching, the tracks slide inside a scroll view that does not move.
  const pinchShift = pinchAt ? pinchAt.from - pinchAt.x : 0;
  const scrollWidth = pinchAt ? Math.max(trackWidth, pinchAt.from + viewport) : trackWidth;
  const grain: Grain = dayWidth < TINY_DAY ? 'tiny' : dayWidth < NARROW_DAY ? 'narrow' : dayWidth >= WIDE_DAY ? 'wide' : 'normal';
  const hourly = view === 'day' && (dayWidth / (DAY_END - DAY_START)) * 60 >= HOUR_TICKS_FROM;

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

  // « Accès limité »: the planning is only looked at, an empty place does nothing.
  const onCell = (rowKey: string, unit: number) => {
    const slot = restricted ? null : slotAt(rowKey, unit);
    if (slot) newTaskAt(rowKey, slot);
  };

  const onCellLongPress = (rowKey: string, unit: number) => {
    const slot = restricted ? null : slotAt(rowKey, unit);
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
      const dossierSection: SheetSection = {
        title: number ? `Dossier ${number}` : undefined,
        actions: number ? dossier.actions(number, ['folder', 'fiche', 'repertoire', 'creation']) : [],
      };
      const heading = {
        kicker: number || 'Sans dossier',
        title: (task.title ?? '').trim() || task.project_cache?.designation || label || 'Sans titre',
        subtitle: [when, people].filter(Boolean).join(' · '),
        accent: taskTypeOf(data.meta, task.task_type).color,
      };
      if (restricted) {
        return {
          ...heading,
          sections: [
            {
              actions: [
                {
                  key: 'view',
                  label: 'Voir la tâche',
                  icon: (c) => <Eye size={20} color={c} strokeWidth={iconStroke} />,
                  onPress: () => router.push({ pathname: '/tache', params: { id: task.id } }),
                },
              ],
            },
            dossierSection,
          ],
        };
      }
      return {
        ...heading,
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
          dossierSection,
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
              ...(restricted
                ? []
                : [
                    {
                      key: 'delete',
                      label: 'Supprimer l’absence',
                      detail: 'Annulable juste après',
                      destructive: true,
                      disabled: locked,
                      icon: (c: string) => <Trash2 size={20} color={c} strokeWidth={iconStroke} />,
                      onPress: change((c) => deleteAbsence(c, menu.absenceId), `Absence supprimée : ${menu.label}`),
                    },
                  ]),
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

  const hidden = new Set(settings.planningHidden);
  const teams = board ? board.groups.filter((group) => group.key !== UNASSIGNED) : [];
  const people = teams.flatMap((group) => group.rows);
  const hiddenCount = people.filter((person) => hidden.has(person.key)).length;
  const showOnly = (ids: string[]) => save({ ...settings, planningHidden: people.map((person) => person.key).filter((id) => !ids.includes(id)) });
  const me = settings.initials ? people.find((person) => person.label.toUpperCase() === settings.initials.toUpperCase()) : undefined;
  const shownIds = people.filter((person) => !hidden.has(person.key)).map((person) => person.key);
  const peopleSections: SheetSection[] = [
    {
      actions: [
        {
          key: 'all',
          label: 'Tout le monde',
          selected: hiddenCount === 0,
          keepOpen: true,
          onPress: () => showOnly(people.map((person) => person.key)),
        },
        ...(me
          ? [
              {
                key: 'me',
                label: 'Moi seulement',
                detail: me.name,
                selected: shownIds.length === 1 && shownIds[0] === me.key,
                keepOpen: true,
                onPress: () => showOnly([me.key]),
              },
            ]
          : []),
      ],
    },
    ...teams.map((group) => ({
      title: group.name,
      actions: group.rows.map((row) => ({
        key: row.key,
        label: row.name,
        detail: row.label !== row.name ? row.label : undefined,
        selected: !hidden.has(row.key),
        keepOpen: true,
        onPress: () => {
          selectionFeedback();
          return showOnly(hidden.has(row.key) ? [...shownIds, row.key] : shownIds.filter((id) => id !== row.key));
        },
      })),
    })),
  ];

  /** What a bar adds under its title as it grows: project, client, hours, people, notes. */
  const infoOf = (task: PlanningTask): string[] => {
    const title = (task.title ?? '').trim();
    const designation = (task.project_cache?.designation ?? '').trim();
    const start = parseInstant(task.start ?? '');
    const end = parseInstant(task.end ?? '');
    const people = Object.keys(task.assignments ?? {})
      .map((id) => data?.resources.find((resource) => resource.id === id)?.initials ?? '')
      .filter(Boolean);
    return [
      title && designation && designation !== title ? designation : '',
      (task.project_cache?.client ?? '').trim(),
      start !== null && end !== null ? `${describeInstant(start)} → ${describeInstant(end)}` : '',
      people.length > 1 ? people.join(' ') : '',
      (task.notes ?? '').trim().replace(/\s+/g, ' '),
    ].filter(Boolean);
  };

  return (
    <Screen>
      <View style={styles.toolbar}>
        <View style={styles.segments} accessibilityRole="tablist">
          {(['day', 'week', 'month'] as ViewKind[]).map((kind) => (
            <Pressable
              key={kind}
              accessibilityRole="tab"
              accessibilityState={{ selected: view === kind }}
              accessibilityHint={view === kind && zoom !== 1 ? 'Revenir à l’affichage sans zoom' : undefined}
              onPress={() => (view === kind ? resetZoom() : setView(kind))}
              style={[styles.segment, view === kind && styles.segmentOn]}
            >
              <Text style={[styles.segmentText, view === kind && styles.segmentTextOn]}>{VIEW_LABELS[kind]}</Text>
            </Pressable>
          ))}
        </View>
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            setEntry(null);
            setAnchor(shownDay(todayNumber()));
          }}
          style={styles.todayButton}
        >
          <Text style={styles.todayText}>Aujourd’hui</Text>
        </Pressable>
        {people.length ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={
              hiddenCount ? `Personnes affichées : ${people.length - hiddenCount} sur ${people.length}` : 'Choisir les personnes affichées'
            }
            onPress={() => setChoosingPeople(true)}
            style={[styles.peopleButton, hiddenCount > 0 && styles.peopleButtonOn]}
          >
            <Users size={18} color={colors.accent700} strokeWidth={iconStroke} />
            {hiddenCount ? (
              <Text style={styles.peopleCount}>
                {people.length - hiddenCount}/{people.length}
              </Text>
            ) : null}
          </Pressable>
        ) : null}
      </View>
      <View style={styles.periodRow}>
        <Pressable accessibilityRole="button" accessibilityLabel="Précédent" onPress={() => turnPage(-1, 'arrow')} style={styles.arrow}>
          <ChevronLeft size={22} color={colors.accent700} strokeWidth={iconStroke} />
        </Pressable>
        <View style={styles.period}>
          <Text style={styles.periodText} numberOfLines={1}>
            {periodText(view, anchor)}
          </Text>
          <Muted>{loading ? 'Actualisation…' : settings.demo ? 'Mode démo' : loadedAt ? `À jour, ${clock(loadedAt)}` : ''}</Muted>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Suivant" onPress={() => turnPage(1, 'arrow')} style={styles.arrow}>
          <ChevronRight size={22} color={colors.accent700} strokeWidth={iconStroke} />
        </Pressable>
      </View>

      {/* Messages sit over the days' heading, which stays in place while the rows scroll under it. */}
      {error || actionError || notice || undo || focus ? (
        <View style={styles.notices}>
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
        </View>
      ) : null}

      {/* The days (or the hours) stay on screen: outside the list of rows, and carried
          sideways with the tracks by the scroll itself. */}
      {board && groups.length ? (
        <View style={styles.heading} {...gestures}>
          <View style={[styles.headerCell, { width: NAME_WIDTH }]} />
          <View style={styles.headingClip}>
            <Animated.View style={{ transform: [{ translateX: headingShift }] }}>
              <View style={{ width: trackWidth, transform: [{ translateX: pinchShift }] }}>
                <Header board={board} view={view} grain={grain} hourly={hourly} />
              </View>
            </Animated.View>
          </View>
        </View>
      ) : null}

      <View
        ref={listBox}
        style={styles.flex}
        onLayout={() =>
          listBox.current?.measure((_x, _y, _width, height, _left, top) => {
            listAt.current = { top, height };
          })
        }
      >
      <ScrollView
        ref={list}
        scrollEnabled={!pinching}
        onScroll={(event) => {
          scrollY.current = event.nativeEvent.contentOffset.y;
        }}
        onContentSizeChange={(_width, height) => {
          if (!pinched.current) contentHeight.current = height;
        }}
        scrollEventThrottle={16}
        refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load()} tintColor={colors.accent700} />}
      >
        {/* While pinching, the rows slide inside a list that does not move, as the tracks do sideways. */}
        <View style={[styles.content, pinchAt ? { minHeight: pinchAt.holdY, transform: [{ translateY: pinchAt.fromY - pinchAt.y }] } : null]}>
        {board ? (
          <View ref={boardRef} style={styles.board} {...gestures}>
            <View style={{ width: NAME_WIDTH }}>
              {groups.map((group) => (
                <View key={group.key}>
                  <View style={styles.teamCell}>
                    <Caps style={styles.team} numberOfLines={1}>
                      {group.name}
                    </Caps>
                  </View>
                  {group.rows.map((row) => (
                    <View key={row.key} style={[styles.nameCell, { height: rowHeight(row, lane) }]}>
                      <Text style={styles.name} numberOfLines={1} accessibilityLabel={row.name}>
                        {row.label}
                      </Text>
                    </View>
                  ))}
                </View>
              ))}
            </View>
            <Animated.ScrollView
              ref={sideways}
              key={page}
              horizontal
              // Fitting the screen, the tracks stay put and a sideways drag turns the page.
              scrollEnabled={scrollable && !pinching}
              showsHorizontalScrollIndicator={scrollable}
              contentOffset={pageStart}
              onScroll={onScrollEvent}
              onScrollEndDrag={onScrollEndDrag}
              scrollEventThrottle={16}
              style={styles.flex}
            >
              <View style={{ width: scrollWidth }}>
                <View style={{ width: trackWidth, transform: [{ translateX: pinchShift }] }}>
                  {groups.map((group) => (
                    <View key={group.key}>
                      <View style={styles.teamCell} />
                      {group.rows.map((row) => (
                        <Track
                          key={row.key}
                          row={row}
                          board={board}
                          view={view}
                          lane={lane}
                          wide={grain === 'wide'}
                          trackWidth={trackWidth}
                          scrolledX={scrolledX}
                          scroll={pinchAt ? null : scrollAnim}
                          infoOf={infoOf}
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
              </View>
            </Animated.ScrollView>
          </View>
        ) : null}
        {board && board.groups.length === 0 ? <Muted style={styles.none}>Personne dans le planning.</Muted> : null}
        {board && board.groups.length > 0 && groups.length === 0 ? (
          <Notice
            message="Toutes les personnes sont masquées."
            action={{ label: 'Choisir les personnes', onPress: () => setChoosingPeople(true) }}
          />
        ) : null}

        {board ? <Backlog cards={board.backlog} data={data} focus={focus} onLongPress={onCardLongPress} /> : null}
        {board ? (
          <Muted style={styles.hint}>
            {restricted
              ? 'Planning en consultation (accès limité). Touchez une tâche pour la voir.'
              : 'Touchez une tâche pour la modifier, restez appuyé pour ses actions.'}{' '}
            Écartez deux doigts sur la grille pour l’agrandir et lire plus de détails.
          </Muted>
        ) : null}
        </View>
      </ScrollView>
      </View>

      <ActionSheet
        visible={sheet !== null}
        onClose={() => setMenu(null)}
        kicker={sheet?.kicker}
        title={sheet?.title}
        subtitle={sheet?.subtitle}
        accent={sheet?.accent}
        sections={sheet?.sections ?? []}
      />
      <ActionSheet
        visible={choosingPeople}
        onClose={() => setChoosingPeople(false)}
        kicker="Planning"
        title="Personnes affichées"
        subtitle={hiddenCount ? `${people.length - hiddenCount} sur ${people.length}, sur ce téléphone` : 'Tout le monde, sur ce téléphone'}
        sections={peopleSections}
        closeLabel="Terminé"
      />

      {restricted ? null : (
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
      )}
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

// The heading, the shading and the bars are told what changes them (a grain, a height),
// not the zoom itself: a pinch redraws the board at every frame, and all it then has to
// touch is the width of the tracks and the few bars that gain or lose a line.
const Header = memo(function Header({ board, view, grain, hourly }: { board: Board; view: ViewKind; grain: Grain; hourly: boolean }) {
  if (view === 'day') {
    const span = DAY_END - DAY_START;
    // Zoomed in, the five marks of the day make room for one per hour.
    const ticks = hourly ? HOUR_TICKS : DAY_TICKS;
    return (
      <View style={styles.headerCell}>
        {ticks.map((minute) => (
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
  // The heading says as much as the day is wide: « 9 » under its weekday letter, « Lu 9 »,
  // then « lundi 09.11 ». A whole month on one screen only dates its Mondays.
  const tiny = grain === 'tiny';
  const compact = tiny || grain === 'narrow';
  const wide = grain === 'wide';
  const today = todayNumber();
  return (
    <View style={[styles.headerCell, styles.headerDays]}>
      {board.columns.map((column) => {
        if (tiny) {
          return (
            <View key={column.day} style={styles.flex} accessibilityLabel={describeDay(column.day)}>
              {weekday(column.day) === 0 ? (
                <Caps style={styles.mondayDate} numberOfLines={1}>
                  {columnHeading(column.day, true)}
                </Caps>
              ) : null}
              <View style={[styles.todayMark, column.day === today && styles.todayMarkOn]} />
            </View>
          );
        }
        return (
          <View key={column.day} style={styles.flex} accessibilityLabel={column.label || undefined}>
            {compact ? <Text style={styles.weekdayLetter}>{WEEKDAY_LETTERS[weekday(column.day)]}</Text> : null}
            {column.label && !compact ? (
              <Text style={styles.holiday} numberOfLines={1}>
                {column.label}
              </Text>
            ) : null}
            <Caps style={[styles.dayHeading, column.day === today && styles.today]} numberOfLines={1}>
              {wide ? describeDay(column.day) : columnHeading(column.day, compact)}
            </Caps>
          </View>
        );
      })}
    </View>
  );
});

/** `minute`: the time of day, so that the « now » line of the day view moves with it. */
const Shading = memo(function Shading({ columns, view, wide, minute }: { columns: Column[]; view: ViewKind; wide: boolean; minute: number }) {
  if (view === 'day') {
    const column = columns[0];
    const span = DAY_END - DAY_START;
    const pct = (minute: number) => `${((minute - DAY_START) / span) * 100}%` as const;
    if (!column) return null;
    if (column.closed) return <View style={[styles.closed, styles.fill]} />;
    const now = column.day === todayNumber() ? minute : null;
    return (
      <>
        <View style={[styles.closed, { left: pct(NOON), width: pct(DAY_START + AFTERNOON - NOON) }]} />
        {!column.worked[1] ? <View style={[styles.closed, { left: pct(NOON), right: 0 }]} /> : null}
        {now !== null && now > DAY_START && now < DAY_END ? <View style={[styles.nowLine, { left: pct(now) }]} /> : null}
      </>
    );
  }
  const units = columns.length * 2;
  const pct = (unit: number) => `${(unit / units) * 100}%` as const;
  const today = todayNumber();
  return (
    <>
      {columns.map((column, index) => {
        // A firmer line where a week begins, to read the month by weeks; and between days
        // once they are wide enough to be halved by a lighter one at noon.
        const firm = wide || (view === 'month' && index > 0 && weekday(column.day) === 0);
        return (
          <Fragment key={column.day}>
            {column.day === today ? <View style={[styles.todayColumn, { left: pct(index * 2), width: pct(2) }]} /> : null}
            {!column.worked[0] ? <View style={[styles.closed, { left: pct(index * 2), width: pct(1) }]} /> : null}
            {!column.worked[1] ? <View style={[styles.closed, { left: pct(index * 2 + 1), width: pct(1) }]} /> : null}
            <View style={[styles.dayLine, firm && styles.weekLine, { left: pct(index * 2) }]} />
            {wide ? <View style={[styles.dayLine, { left: pct(index * 2 + 1) }]} /> : null}
          </Fragment>
        );
      })}
    </>
  );
});

function Track({
  row,
  board,
  view,
  lane,
  wide,
  trackWidth,
  scrolledX,
  scroll,
  infoOf,
  focus,
  onCell,
  onCellLongPress,
  onBar,
  onBarLongPress,
}: {
  row: WeekRow;
  board: Board;
  view: ViewKind;
  lane: number;
  wide: boolean;
  trackWidth: number;
  scrolledX: number;
  /** The sideways scroll as it moves, for the bars' texts to follow; none while a pinch moves the tracks itself. */
  scroll: Animated.Value | null;
  infoOf: (task: PlanningTask) => string[];
  focus: string;
  onCell: (rowKey: string, unit: number) => void;
  onCellLongPress: (rowKey: string, unit: number) => void;
  onBar: (segment: Segment, rowKey: string) => void;
  onBarLongPress: (segment: Segment) => void;
}) {
  const track = useRef<HostInstance>(null);
  const height = rowHeight(row, lane);
  /** Where a bar starts on the tracks, and how far its text can slide before leaving it. */
  const reach = (segment: Segment) => ({
    left: Math.round((segment.startSlot / board.units) * trackWidth),
    slack: Math.max(0, Math.round((segment.span / board.units) * trackWidth) - 48),
  });
  /** How far a bar's start is hidden past the left edge, short of pushing its text out of it. */
  const hidden = (segment: Segment) => {
    const { left, slack } = reach(segment);
    return clamp(Math.round(scrolledX) - left, 0, slack);
  };
  /** Lines given to each text of a bar, for the room it has: its height, and its width past that inset. */
  const fit = (segment: Segment) => {
    const width = (segment.span / board.units) * trackWidth - hidden(segment) - BAR_PADDING;
    return fitBar(barLines(segment, infoOf), segment.badge, lane - LANE_GAP, width);
  };
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
      <Shading columns={board.columns} view={view} wide={wide} minute={view === 'day' ? minuteNow() : 0} />
      {row.segments.map((segment) => (
        <Bar
          key={`${segment.id}:${segment.lane}`}
          segment={segment}
          rowKey={row.key}
          units={board.units}
          focused={Boolean(focus) && (segment.task?.project_number ?? '').trim() === focus}
          dimmed={Boolean(focus) && segment.kind === 'task' && (segment.task?.project_number ?? '').trim() !== focus}
          barHeight={lane - LANE_GAP}
          inset={hidden(segment)}
          scroll={scroll}
          left={scroll ? reach(segment).left : 0}
          slack={scroll ? reach(segment).slack : 0}
          fit={fit(segment)}
          infoOf={infoOf}
          onBar={onBar}
          onBarLongPress={onBarLongPress}
        />
      ))}
    </Pressable>
  );
}

/** A text of a bar: its first line (the dossier, or the title of a task without one), the title, or a detail. */
type BarLine = { key: string; kind: 'head' | 'title' | 'more'; text: string; wraps: boolean };

/** The texts a task's bar can show, most wanted first. */
function barLines(segment: Segment, infoOf: (task: PlanningTask) => string[]): BarLine[] {
  const task = segment.task;
  if (!task) return [{ key: 'head', kind: 'head', text: segment.label, wraps: true }];
  const { number, title } = barParts(task);
  const mark = segment.provisional ? ' ?' : '';
  const lines: BarLine[] = [];
  // A dossier number stands alone on the first line, and is cut rather than wrapped: a
  // half-day bar is just as wide as its four digits.
  if (number) {
    lines.push({ key: 'head', kind: 'head', text: number + mark, wraps: false });
    if (title || segment.badge) lines.push({ key: 'title', kind: 'title', text: title, wraps: true });
  } else lines.push({ key: 'head', kind: 'head', text: (title || segment.label) + mark, wraps: true });
  infoOf(task).forEach((text, index) => lines.push({ key: `more${index}`, kind: 'more', text, wraps: true }));
  return lines;
}

/**
 * How many lines each text of a bar gets, as « 1.2.1.0 », for a bar `barHeight` high whose
 * texts have `width` to run in. In turn: the first line; the title; one more piece of
 * information; a second line for the title if it needs one; the other pieces, a line
 * each; then, with what room is left, every text is wrapped as far as it needs, the title
 * first. A narrow or low bar thus says a little of everything before it says all of one
 * thing. Whether a text needs wrapping is judged from its length: nothing is measured,
 * so a text may be given a line it does not fill.
 */
function fitBar(lines: BarLine[], badge: string, barHeight: number, width: number): string {
  const counts = lines.map(() => 0);
  const lineOf = (line: BarLine) => (line.kind === 'head' ? NUMBER_LINE : DETAIL_LINE);
  const needs = lines.map((line) => {
    if (!line.wraps) return 1;
    const lead = line.kind !== 'more' && badge ? badge.length + 1 : 0;
    const run = (line.text.length + lead) * (line.kind === 'head' ? HEAD_CHAR : TEXT_CHAR);
    return clamp(Math.ceil(run / Math.max(1, width)), 1, MAX_WRAP);
  });
  const main = lines.findIndex((line) => line.wraps && line.kind !== 'more');
  const details = lines.map((_line, index) => index).filter((index) => lines[index].kind === 'more');
  const mainTurn = main >= 0 ? [main] : [];
  // A task without a dossier has only its title to be known by: its second line comes first.
  const order = main === 0 ? [0, 0, ...details] : [0, ...mainTurn, ...details.slice(0, 1), ...mainTurn, ...details.slice(1)];
  for (let pass = 2; pass < MAX_WRAP; pass += 1) order.push(...mainTurn, ...details);
  let left = barHeight - BAR_FRAME;
  for (const index of order) {
    const first = index === 0 && counts[0] === 0;
    if (counts[index] >= needs[index] || (!first && left < lineOf(lines[index]))) continue;
    // A piece of information is not shown after one that had to be left out.
    if (lines[index].kind === 'more' && counts[index] === 0 && details.some((other) => other < index && counts[other] === 0)) continue;
    counts[index] += 1;
    left -= lineOf(lines[index]);
  }
  return counts.join('.');
}

type BarProps = {
  segment: Segment;
  rowKey: string;
  units: number;
  focused: boolean;
  dimmed: boolean;
  barHeight: number;
  /** How far its text is pushed in, where the sideways scroll last came to rest. */
  inset: number;
  /** The scroll as it moves, and what `inset` is worked out from: the text follows the edge between two rests. */
  scroll: Animated.Value | null;
  left: number;
  slack: number;
  /** Lines of each of its texts, from `fitBar`. */
  fit: string;
  infoOf: (task: PlanningTask) => string[];
  onBar: (segment: Segment, rowKey: string) => void;
  onBarLongPress: (segment: Segment) => void;
};

/**
 * A bar is drawn again only when what it shows changes. Its three functions are new at
 * every render of the screen but always do the same for a given segment (open it, open
 * its menu, read its task), and a planning read again brings new segments: they are left
 * out of the comparison.
 */
function sameBar(a: BarProps, b: BarProps): boolean {
  return (
    a.segment === b.segment &&
    a.rowKey === b.rowKey &&
    a.units === b.units &&
    a.focused === b.focused &&
    a.dimmed === b.dimmed &&
    a.barHeight === b.barHeight &&
    a.inset === b.inset &&
    a.scroll === b.scroll &&
    a.left === b.left &&
    a.slack === b.slack &&
    a.fit === b.fit
  );
}

const Bar = memo(function Bar({
  segment,
  rowKey,
  units,
  focused,
  dimmed,
  barHeight,
  inset,
  scroll,
  left,
  slack,
  fit,
  infoOf,
  onBar,
  onBarLongPress,
}: BarProps) {
  // Between two rests of the scroll the text slides with the edge, moved where the scroll
  // is (no round trip through React, so no lag); at rest `inset` takes over, the text is
  // laid out again in the width it has left, and this slide is back to nothing.
  const slide = useMemo(
    () =>
      scroll && slack > 0
        ? scroll.interpolate({ inputRange: [left, left + slack], outputRange: [-inset, slack - inset], extrapolate: 'clamp' })
        : null,
    [scroll, left, slack, inset],
  );
  const style = {
    left: `${(segment.startSlot / units) * 100}%`,
    width: `${(segment.span / units) * 100}%`,
    top: segment.lane * (barHeight + LANE_GAP) + LANE_GAP,
  } as const;
  const onPress = () => onBar(segment, rowKey);
  const onLongPress = () => onBarLongPress(segment);
  const height = { height: barHeight, paddingLeft: 2 + inset };
  const sliding = slide ? { transform: [{ translateX: slide }] } : undefined;
  // Lines of detail a bar has room for under its first one.
  const room = Math.max(0, Math.floor((barHeight - 2 - NUMBER_LINE) / DETAIL_LINE));
  if (segment.kind === 'absence') {
    return (
      <Pressable
        accessibilityRole={segment.absenceId ? 'button' : undefined}
        accessibilityLabel={segment.label}
        disabled={!segment.absenceId}
        onPress={onPress}
        onLongPress={onLongPress}
        delayLongPress={350}
        style={[styles.bar, height, styles.absence, style]}
      >
        <Animated.View style={sliding}>
          <Text style={styles.absenceText} numberOfLines={clamp(room + 1, 1, 3)}>
            {segment.label}
          </Text>
        </Animated.View>
      </Pressable>
    );
  }
  const urgent = Boolean(segment.task?.urgent);
  // A bar tall enough stacks its texts, each on the lines `fit` gives it: the dossier, the
  // people badge and the title, then what `infoOf` adds as the zoom makes room for it.
  const stacked = barHeight >= STACKED_FROM;
  const lines = stacked ? barLines(segment, infoOf) : [];
  const counts = fit.split('.').map(Number);
  const shown = counts.reduce((sum, count) => sum + count, 0);
  // The badge leads the title, or the first line of a task without a dossier.
  const badged = lines.some((line) => line.kind === 'title') ? 'title' : 'head';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${segment.task ? taskLabel(segment.task) : segment.label}${segment.badge ? `, ${segment.badge}` : ''}${urgent ? ', urgent' : ''}`}
      accessibilityHint="Appui long : actions de la tâche"
      onPress={onPress}
      onLongPress={onLongPress}
      delayLongPress={350}
      style={[
        styles.bar,
        height,
        style,
        { borderColor: segment.color, backgroundColor: `${segment.color}33` },
        segment.provisional && styles.provisional,
        // A red edge says « urgent » at any width, where a « ! » would be cut off.
        urgent && styles.urgentEdge,
        segment.done && styles.done,
        focused && styles.focused,
        dimmed && styles.dimmed,
        stacked && shown > 2 && styles.barFromTop,
      ]}
    >
      <Animated.View style={sliding}>
      {stacked ? (
        lines.map((line, index) => {
          const count = counts[index] ?? 0;
          if (!count) return null;
          if (!line.wraps) {
            // One line high and cut, not « 49… ».
            return (
              <View key={line.key} style={styles.barNumberBox}>
                <Text style={styles.barNumber}>{line.text}</Text>
              </View>
            );
          }
          return (
            <Text
              key={line.key}
              style={line.kind === 'head' ? styles.barNumber : line.kind === 'title' ? styles.barDetail : styles.barMore}
              numberOfLines={count}
            >
              {segment.badge && line.kind === badged ? <Text style={styles.badge}>{segment.badge} </Text> : null}
              {line.text}
            </Text>
          );
        })
      ) : (
        <Text style={styles.barText} numberOfLines={1}>
          {/* The badge leads so that a narrow bar still shows « 1/2 » or « ×2 ». */}
          {segment.badge ? <Text style={styles.badge}>{segment.badge} </Text> : null}
          {urgent ? <Text style={styles.urgent}>! </Text> : null}
          {segment.label}
          {segment.provisional ? ' ?' : ''}
        </Text>
      )}
      </Animated.View>
    </Pressable>
  );
}, sameBar);

type BacklogProps = {
  cards: PlanningTask[];
  data: ReturnType<typeof usePlanning>['data'];
  focus: string;
  onLongPress: (task: PlanningTask) => void;
};

/** Not drawn again by a pinch: `onLongPress` always opens the menu of the card it is given. */
const Backlog = memo(function Backlog({ cards, data, focus, onLongPress }: BacklogProps) {
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
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}, (a, b) => a.cards === b.cards && a.data === b.data && a.focus === b.focus);

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
  peopleButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    minWidth: 36,
    minHeight: 36,
    paddingHorizontal: 8,
    borderWidth: 1,
    borderColor: colors.line,
  },
  // Some people are left out: the button says so, and how many are shown.
  peopleButtonOn: { borderColor: colors.accent700, backgroundColor: colors.accent100 },
  peopleCount: { fontFamily: fonts.headingBold, fontSize: 13, color: colors.accent900 },
  notices: { paddingHorizontal: CONTENT_PAD, paddingTop: 10, gap: 10 },
  heading: { flexDirection: 'row', paddingHorizontal: CONTENT_PAD, paddingTop: 6, backgroundColor: colors.bg },
  headingClip: { flex: 1, overflow: 'hidden' },
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
  // Narrow side margins: every pixel of width goes to the days.
  content: { paddingHorizontal: CONTENT_PAD, paddingBottom: 32, gap: 10 },
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
  weekdayLetter: { fontFamily: fonts.heading, fontSize: 9, lineHeight: 11, textAlign: 'center', color: colors.muted },
  today: { color: '#ffffff', backgroundColor: colors.accent700 },
  todayColumn: { position: 'absolute', top: 0, bottom: 0, backgroundColor: `${colors.accent}1f` },
  nowLine: { position: 'absolute', top: 0, bottom: 0, width: 2, marginLeft: -1, backgroundColor: colors.accent700 },
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
  weekLine: { backgroundColor: colors.line },
  closed: { position: 'absolute', top: 0, bottom: 0, backgroundColor: colors.closed },
  bar: {
    position: 'absolute',
    borderWidth: 1,
    paddingHorizontal: 2,
    justifyContent: 'center',
    overflow: 'hidden',
  },
  barText: { fontFamily: fonts.heading, fontSize: 12, color: colors.text },
  barNumberBox: { height: 16, overflow: 'hidden' },
  barNumber: { fontFamily: fonts.headingBold, fontSize: 13, lineHeight: 16, color: colors.text },
  barDetail: { fontFamily: fonts.body, fontSize: 11, lineHeight: DETAIL_LINE, color: colors.subtle },
  barMore: { fontFamily: fonts.body, fontSize: 11, lineHeight: DETAIL_LINE, color: colors.muted },
  barFromTop: { justifyContent: 'flex-start', paddingTop: 2 },
  mondayDate: { width: 40, fontSize: 10, letterSpacing: 0.4 },
  todayMark: { height: 3 },
  todayMarkOn: { backgroundColor: colors.accent700 },
  badge: { fontFamily: fonts.headingBold, color: colors.accent900 },
  urgent: { fontFamily: fonts.headingBold, color: colors.err },
  urgentEdge: { borderLeftWidth: 3, borderLeftColor: colors.err },
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
