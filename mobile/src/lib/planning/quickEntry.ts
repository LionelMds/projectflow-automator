/**
 * Quick entry (« saisie rapide », `core/planning/quick_entry.py`): one line of text turned
 * into a planning task or an absence.
 *
 * The desktop's Ctrl+K bar, typing on a grid cell, a range drawn over several rows and the
 * detail panel share this grammar, e.g. `2026-121 montage marquise jeu-ven LBI YCH`.
 * Elements come in any order. Matching ignores case and accents; the title keeps the typed
 * text. Days are working days, as people count them and as the bars read them back: a
 * morning start counts its day, an afternoon start half of it, and Friday is a day that
 * ends at noon (`jeu 2j` ends on Friday at 12:00, `jeu apm 2j` on Monday at 12:00, `lun 5j`
 * on Friday at 12:00); hours and minutes are worked time. A card without date keeps its
 * days (`ParsedEntry.estimateDays`) until it is placed. An absence counts working days too
 * (`/vac LBI lun 5j` is Monday to Friday).
 *
 * Token offsets are UTF-16 indices into the typed text (`text.slice(start, end)`).
 */

import { DAYS_PER_WEEK, SNAP_MINUTES, snap, WorkCalendar, type WorkPeriod } from './calendar';
import {
  dayStart,
  formatDate,
  formatHhmm,
  fromIsoCalendar,
  type Instant,
  instantDay,
  isoCalendar,
  makeDate,
  MINUTES_PER_DAY,
  MINUTES_PER_HOUR,
  minuteOfDay,
  mod,
  roundHalfEven,
  ValueError,
} from './instant';
import {
  DEFAULT_NIGHT_END,
  DEFAULT_NIGHT_START,
  elapsedText,
  MIN_NIGHT_MINUTES,
  MOON,
  NIGHT_EARLIEST_START,
  NIGHT_MORNING_START,
  NIGHT_WEEKEND_MESSAGE,
  nightAllowed,
  nightWindow,
} from './night';
import { dateParts, mondayOf, weekday } from './walltime';

// ------------------------------------------------------------------ shapes ----

export type Destination = 'grid' | 'unassigned' | 'backlog' | 'absence' | 'invalid';
export type TokenKind =
  | 'project'
  | 'person'
  | 'team'
  | 'type'
  | 'absence'
  | 'day'
  | 'date'
  | 'week'
  | 'part'
  | 'time'
  | 'duration'
  | 'provisional'
  | 'urgent'
  | 'deadline'
  | 'note'
  | 'night';
export type DayPart = 'day' | 'morning' | 'afternoon';
export type AbsenceReason =
  | 'vacances'
  | 'absent'
  | 'ecole'
  | 'cie'
  | 'militaire'
  | 'formation'
  | 'recuperation'
  | 'conge';

/** What the parser reads of a resource (`models.Resource`); missing fields take its defaults. */
export type EntryResource = {
  id: string;
  name: string;
  initials?: string;
  /** « atelier » when missing. */
  team?: string;
  kind_of_resource?: 'person' | 'subcontractor';
  apprentice?: boolean;
  canton?: string;
  /** true when missing. */
  active?: boolean;
  deleted_at?: string | null;
  recurring_off?: readonly { weekday: number; part?: DayPart; reason?: string }[];
};

export type EntryTeam = { key: string; name: string; alias?: string; order?: number };

export type EntryTaskType = {
  key: string;
  label: string;
  color?: string;
  color_dark?: string;
  keywords?: readonly string[];
  team?: string;
};

/** Dossier details found in the répertoire for a typed number. */
export type ProjectInfo = {
  number: string;
  client?: string;
  contact?: string;
  designation?: string;
  managedBy?: string;
};

/**
 * What the parser needs to know besides the text.
 *
 * `findProject` receives a normalised number (`2026-118` or `2026-118-1`). The `cell*`
 * fields describe the grid cell or the range the text was typed on. `calendarFor` gives a
 * resource's calendar (its canton): the first person of the entry sets the working days;
 * `calendar` serves when nobody is named.
 */
export type EntryContext = {
  today: number;
  weekStart: number;
  calendar: WorkCalendar;
  resources: readonly EntryResource[];
  teams: readonly EntryTeam[];
  taskTypes: readonly EntryTaskType[];
  findProject: (number: string) => ProjectInfo | null | undefined;
  defaultYear: number;
  cellResourceIds?: readonly string[];
  cellStart?: Instant | null;
  cellEnd?: Instant | null;
  calendarFor?: ((resourceId: string | null) => WorkCalendar) | null;
};

/**
 * A recognised part of the text, `start`/`end` being offsets in the typed text.
 *
 * `value` is the normalised meaning: dossier number, resource id (candidate ids joined by
 * commas when ambiguous), team key, type key, absence reason, ISO day or `first/last`
 * days, `HH:MM` or `HH:MM-HH:MM`, minutes, working days followed by `j` (`1.5j`), people
 * count, part of day.
 */
export type Token = {
  kind: TokenKind;
  text: string;
  value: string;
  start: number;
  end: number;
  ambiguous: boolean;
  message: string;
};

/** `note` gathers the leftover text and the quoted notes, for `Absence.note`. */
export type AbsenceDraft = {
  resourceIds: readonly string[];
  reason: AbsenceReason;
  firstDay: number;
  lastDay: number;
  part: DayPart;
  note: string;
};

/**
 * What the line asks for.
 *
 * `estimateMinutes` is the worked time between `start` and `end` for a dated task; for a
 * card it is the typed load, a day counting `DAY_MINUTES`. `estimateDays` gives the
 * working days: as typed when the duration has no hours and no typed range fixes the end
 * (`ven ½j` keeps ½ j), else those the dates cover (0 when they are not whole or half
 * days, or for a card with hours). `offHours` marks night work (`nuit`, or hours typed in
 * the evening such as `22h-5h`): its dates are exact and `estimateMinutes` is real time.
 */
export type ParsedEntry = {
  projectNumber: string;
  project: ProjectInfo | null;
  title: string;
  taskType: string;
  resourceIds: readonly string[];
  start: Instant | null;
  end: Instant | null;
  estimateMinutes: number;
  provisional: boolean;
  urgent: boolean;
  deadline: number | null;
  notes: string;
  absence: AbsenceDraft | null;
  destination: Destination;
  tokens: readonly Token[];
  warnings: readonly string[];
  interpretation: string;
  estimateDays: number;
  ignoredDuration: boolean;
  offHours: boolean;
};

// ---------------------------------------------------------------- settings ----

export const ABSENCE_LABELS: Readonly<Record<AbsenceReason, string>> = {
  vacances: 'Vacances',
  absent: 'Absent',
  ecole: 'École professionnelle',
  cie: 'Cours CIE',
  militaire: 'Militaire / PC',
  formation: 'Formation',
  recuperation: 'Récupération',
  conge: 'Congé',
};

export const DEFAULT_TEAMS: readonly EntryTeam[] = [
  { key: 'bureau', name: 'Bureau technique', alias: 'bureau', order: 0 },
  { key: 'atelier', name: 'Atelier', alias: 'atelier', order: 1 },
  { key: 'montage', name: 'Montage', alias: 'montage', order: 2 },
  { key: 'soustraitants', name: 'Sous-traitants', alias: 'soustraitance', order: 3 },
];

export const DEFAULT_TASK_TYPES: readonly EntryTaskType[] = [
  {
    key: 'releve',
    label: 'Relevé de cotes',
    color: '#009494',
    color_dark: '#36baba',
    keywords: ['releve', 'cotes', 'metre', 'mesure', 'mesures'],
    team: 'bureau',
  },
  {
    key: 'dessin',
    label: 'Dessin / plans',
    color: '#8572bb',
    color_dark: '#a996e2',
    keywords: ['dessin', 'plan', 'plans', 'dao', 'cao', 'calcul', 'statique'],
    team: 'bureau',
  },
  {
    key: 'debit',
    label: 'Débit',
    color: '#908126',
    color_dark: '#b5a64f',
    keywords: ['debit', 'decoupe', 'sciage', 'laser'],
    team: 'atelier',
  },
  {
    key: 'soudure',
    label: 'Soudure / assemblage',
    color: '#b7684c',
    color_dark: '#df8c6f',
    keywords: ['soudure', 'souder', 'assemblage', 'fab', 'fabrication', 'pliage'],
    team: 'atelier',
  },
  {
    key: 'finition',
    label: 'Finition / contrôle',
    color: '#aa732b',
    color_dark: '#d19853',
    keywords: ['finition', 'finitions', 'meulage', 'controle', 'ebavurage'],
    team: 'atelier',
  },
  {
    key: 'montage',
    label: 'Montage chantier',
    color: '#4a925c',
    color_dark: '#6fb880',
    keywords: ['montage', 'pose', 'poser', 'chantier'],
    team: 'montage',
  },
  {
    key: 'livraison',
    label: 'Livraison',
    color: '#b16389',
    color_dark: '#d887ae',
    keywords: ['livraison', 'livrer', 'transport', 'camion'],
    team: 'montage',
  },
  {
    key: 'sav',
    label: 'SAV / réparation',
    color: '#4086bd',
    color_dark: '#66abe5',
    keywords: ['sav', 'reparation', 'depannage'],
    team: 'montage',
  },
  {
    key: 'soustraitance',
    label: 'Sous-traitance',
    color: '#787a7e',
    color_dark: '#96989c',
    keywords: ['galva', 'galvanisation', 'thermolaquage', 'thermo', 'peinture'],
    team: 'soustraitants',
  },
  {
    key: 'interne',
    label: 'Interne',
    color: '#96989c',
    color_dark: '#b5b7bb',
    keywords: ['rangement', 'entretien', 'nettoyage', 'stock', 'interne'],
  },
];

/** Type of a new task when the shared list still has it, else the list's first type. */
export const DEFAULT_TASK_TYPE = 'soudure';

/** Type given to a new task: « soudure » when configured, else the first type. */
export function defaultTaskTypeKey(taskTypes: readonly EntryTaskType[]): string {
  if (!taskTypes.length || taskTypes.some((kind) => kind.key === DEFAULT_TASK_TYPE)) return DEFAULT_TASK_TYPE;
  return taskTypes[0].key;
}

// --------------------------------------------------------------- constants ----

/**
 * Load of a typed day without date (the Monday-Thursday day), for `estimateMinutes`.
 * A dated day follows the calendar instead (`WorkCalendar.addDays`).
 */
export const DAY_MINUTES = 540;
export const HALF_DAY_MINUTES = Math.floor(DAY_MINUTES / 2);
/** Longest duration a line may add up to, about a year of work. */
export const MAX_DURATION_MINUTES = 250 * DAY_MINUTES;
const MIN_PREFIX_LETTERS = 3;
// A date typed without year further back than this means the same date next year.
const PAST_DATE_DAYS = 30;
// A week number this far behind the current one means that week next year.
const PAST_WEEKS = 4;
// A range whose end comes before its start wraps to next year only if it stays this short.
const MAX_WRAP_DAYS = 92;
const YEARS_BACK = 1;
const YEARS_AHEAD = 5;
const WEEK_LAST_WEEKDAY = 4;
const CENTURY = 2000;
const TWO_DIGIT_YEARS = 100;
export const UNREADABLE_WARNING = 'Saisie non comprise : vérifier les dates et les durées';

// Initials equal to one of these words only count when typed in capitals ("relevé de
// cotes" keeps its "de", "DE" is a person).
const COMMON_WORDS: ReadonlySet<string> = new Set([
  'le', 'la', 'les', 'de', 'du', 'des', 'et', 'en', 'au', 'aux', 'un', 'une', 'sur', 'par', 'pour', 'avec',
  'a', 'ou', 'pas', 'ce', 'se', 'sa', 'son', 'ses', 'ne', 'il', 'on', 'y', 'dans', 'chez', 'sans', 'sous',
]);
// A number right after one of these words is a colour, a profile or a grade, never a dossier.
const MATERIAL_PREFIXES: ReadonlySet<string> = new Set([
  'ral', 'hea', 'heb', 'hem', 'ipe', 'ipn', 'inp', 'upn', 'unp', 'upe', 'inox', 'aisi', 'dn', 'x', 'ø',
]);
// Nor a number followed by one of these units: "150 pces", "200 x 100".
const UNIT_WORDS: ReadonlySet<string> = new Set([
  'mm', 'cm', 'm', 'ml', 'm2', 'm3', 'kg', 't', 'l', 'x', 'pc', 'pcs', 'pce', 'pces', 'piece', 'pieces', 'ex',
]);
// "1.5 jours" is a duration, not the 1st of May.
const DURATION_UNIT_WORDS: ReadonlySet<string> = new Set([
  'j', 'jr', 'jrs', 'jour', 'jours', 'journee', 'journees', 'h', 'hr', 'hrs', 'heure', 'heures', 'min',
  'minute', 'minutes', 'mn',
]);
const MEASURE_WORDS: ReadonlySet<string> = new Set([...UNIT_WORDS, ...DURATION_UNIT_WORDS]);
const ABSENCE_COMMANDS: ReadonlyMap<string, AbsenceReason> = new Map<string, AbsenceReason>([
  ['vac', 'vacances'],
  ['vacances', 'vacances'],
  ['absent', 'absent'],
  ['abs', 'absent'],
  ['absence', 'absent'],
  ['malade', 'absent'],
  ['ecole', 'ecole'],
  ['cie', 'cie'],
  ['militaire', 'militaire'],
  ['mil', 'militaire'],
  ['pc', 'militaire'],
  ['formation', 'formation'],
  ['recup', 'recuperation'],
  ['recuperation', 'recuperation'],
  ['conge', 'conge'],
]);
export const DAY_ABBREVIATIONS = ['lun', 'mar', 'mer', 'jeu', 'ven', 'sam', 'dim'] as const;
export const DAY_NAMES = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche'] as const;
export const PART_LABELS: Readonly<Record<'morning' | 'afternoon', string>> = {
  morning: 'matin',
  afternoon: 'après-midi',
};

const WEEKDAY_WORDS: ReadonlyMap<string, number> = new Map([
  ...DAY_ABBREVIATIONS.map((word, index): [string, number] => [word, index]),
  ...DAY_NAMES.map((word, index): [string, number] => [word, index]),
]);
// Number of working days after today.
const RELATIVE_DAYS: ReadonlyMap<string, number> = new Map([
  ['auj', 0],
  ["aujourd'hui", 0],
  ['aujourdhui', 0],
  ['demain', 1],
  ['apres-demain', 2],
  ['apres demain', 2],
]);
const CHAR_FIXES: ReadonlyMap<string, string> = new Map([
  ['‘', "'"],
  ['’', "'"],
  ['ʼ', "'"],
  ['“', '"'],
  ['”', '"'],
  ['„', '"'],
  ['‐', '-'],
  ['‑', '-'],
  ['–', '-'],
  ['—', '-'],
  ['×', 'x'],
  ['½', '½'],
]);

// ------------------------------------------------------------------ regexes ----
// Every pattern is sticky: `matchAt` anchors it at a position, like Python's `match(s, pos)`.

const END = String.raw`(?=$|[\s,;?!"«»])`;
const RANGE = String.raw`(?:\s*-\s*|\s+(?:a|au|jusqu'au|jusqu'a)\s+)`;
const DAY_WORD =
  String.raw`(?:aujourd'?hui|auj|apres[- ]demain|demain|lundi|lun|mardi|mar|mercredi|mer` +
  String.raw`|jeudi|jeu|vendredi|ven|samedi|sam|dimanche|dim)`;
const DATE_A = String.raw`(?<ad>\d{1,2})\.(?<am>\d{1,2})(?:\.(?<ay>\d{4}|\d{2}))?`;
const DATE_B = String.raw`(?<bd>\d{1,2})\.(?<bm>\d{1,2})(?:\.(?<by>\d{4}|\d{2}))?`;
const WEEK_PREFIX = String.raw`(?:semaine\s|sem\.?\s?|s)`;

function sticky(source: string): RegExp {
  return new RegExp(source, 'yu');
}

const SEPARATOR_RE = sticky(String.raw`[\s,;]+`);
const WORD_RE = sticky(String.raw`[^\s,;?!"«»+]+`);
const END_RE = sticky(END);
const NEXT_WORD_RE = sticky(String.raw`\s*([^\s,;?!"«»+]+)`);
const NOTE_RE = sticky(String.raw`"(?<q>[^"]*)"?|«(?<g>[^»]*)»?`);
const DEADLINE_RE = sticky(
  String.raw`(?:<\s?|avant\s+(?:le\s+)?)(?:(?<d>\d{1,2})\.(?<m>\d{1,2})(?:\.(?<y>\d{4}|\d{2}))?` +
    String.raw`|(?<w>${DAY_WORD}))${END}`,
);
const COMMAND_RE = sticky(String.raw`/(?<w>[^\s,;?!"«»+/]+)`);
const FLAGS_RE = sticky(String.raw`[?!]+`);
const WEEK_RE = sticky(
  String.raw`${WEEK_PREFIX}(?<a>\d{1,2})(?:\s*-\s*${WEEK_PREFIX}?(?<b>\d{1,2}))?${END}`,
);
const DATE_RE = sticky(String.raw`(?:du\s+)?${DATE_A}(?:${RANGE}${DATE_B})?${END}`);
const SHORT_DATE_RANGE_RE = sticky(String.raw`(?:du\s+)?(?<ad>\d{1,2})${RANGE}${DATE_B}${END}`);
const TIME_RANGE_RE = sticky(
  String.raw`(?:de\s+|des\s+)?(?<ah>\d{1,2})(?<au>\s?[h:](?<am>\d{2})?)?` +
    String.raw`(?:\s*-\s*|\s+(?:a|jusqu'a)\s+)(?<bh>\d{1,2})\s?[h:](?<bm>\d{2})?${END}`,
);
const START_TIME_RE = sticky(
  String.raw`(?:a\s+partir\s+de|a|des|vers)\s+(?<h>\d{1,2})\s?[h:](?<m>\d{2})?${END}`,
);
const CLOCK_RE = sticky(String.raw`(?<h>\d{1,2}):(?<m>\d{2})${END}`);
// "1j½", "1 j ½" (as formatDays writes it), "1 ½ j", "1 1/2 j", "1 jour et demi".
const DAYS_RE = sticky(
  String.raw`(?:(?<n>\d+(?:[.,]\d+)?)(?<h1>\s?½|\s1/2)?|(?<h0>½|1/2|demi-?))` +
    String.raw`\s?(?:journees?|jours?|jrs?|j)(?<h2>\s?½|\s?1/2|\s+et\s+demie?)?${END}`,
);
const HOURS_RE = sticky(
  String.raw`(?<n>\d+[.,]\d+)\s?(?:heures?|hrs?|h)${END}|(?<w>\d+)\s?(?:heures?|hrs?)${END}`,
);
const MINUTES_RE = sticky(String.raw`(?<n>\d+)\s?(?:minutes?|min|mn)${END}`);
const BARE_HOUR_RE = sticky(String.raw`(?<h>\d{1,2})\s?h(?<m>\d{2})?${END}`);
const PART_RE = sticky(String.raw`(?<w>matinee|matin|mat|apres[- ]?midi|aprem|apm|pm|am)${END}`);
const DAY_RE = sticky(
  String.raw`(?:du\s+)?(?<a>${DAY_WORD})(?:${RANGE}(?<b>${DAY_WORD})|\s+(?<next>prochaine?))?${END}`,
);
const FLAG_WORD_RE = sticky(String.raw`(?<w>urgente?|provisoire)${END}`);
const NIGHT_RE = sticky(String.raw`(?:de\s+)?nuit${END}`);
/** `118`, `2026-118-2`, `2024-4493-a1`, `5102-rev03` (a letter suffix holds a digit). */
const DOSSIER_RE = sticky(
  String.raw`(?:(?<y>\d{4})-)?(?<n>\d{3,4})(?:-(?<s>\d{1,2}|[a-z]*\d+[a-z0-9]*))?${END}`,
);
const BOLT_RE = /^m\d{1,2}$/u;
const SPACES_RE = /\s+/gu;
const SPACE_BEFORE_PUNCTUATION_RE = /\s+([,.])/gu;
const REPEATED_PUNCTUATION_RE = /([,;])(?:\s*[,;])+/gu;
const TITLE_EDGES = ' ,;:.-+/·';

type Groups = Record<string, string | undefined>;
type Found = { start: number; end: number; text: string; groups: Groups; first: string | undefined };

function matchAt(pattern: RegExp, text: string, position: number): Found | null {
  pattern.lastIndex = position;
  const match = pattern.exec(text);
  if (match === null) return null;
  return {
    start: match.index,
    end: match.index + match[0].length,
    text: match[0],
    groups: match.groups ?? {},
    first: match[1],
  };
}

// --------------------------------------------------------- unicode helpers ----

function unicodeTest(source: string, fallback: (char: string) => boolean): (char: string) => boolean {
  try {
    const pattern = new RegExp(source, 'u');
    return (char) => pattern.test(char);
  } catch {
    // An engine without Unicode property escapes: letters are the cased characters.
    return fallback;
  }
}

const isCased = (char: string) => char.toLowerCase() !== char.toUpperCase();
/** Python's `str.isalnum()` for one character. */
const isAlnumChar = unicodeTest(String.raw`^[\p{L}\p{N}]$`, (char) => /^[0-9]$/.test(char) || isCased(char));
const isAlphaChar = unicodeTest(String.raw`^\p{L}$`, isCased);
const isDigitChar = unicodeTest(String.raw`^\p{Nd}$`, (char) => /^[0-9]$/.test(char));

function everyChar(text: string, test: (char: string) => boolean): boolean {
  if (!text) return false;
  for (const char of text) if (!test(char)) return false;
  return true;
}

/** Python's `str.isupper()`: some cased character and none in lower case. */
function isUpper(text: string): boolean {
  return text.toUpperCase() === text && text.toLowerCase() !== text;
}

/** Combining marks dropped by the folding (Python's `unicodedata.combining`). */
function isCombining(char: string): boolean {
  const code = char.codePointAt(0) ?? 0;
  return (
    (code >= 0x0300 && code <= 0x036f && code !== 0x034f) ||
    (code >= 0x1ab0 && code <= 0x1aff) ||
    (code >= 0x1dc0 && code <= 0x1dff) ||
    (code >= 0x20d0 && code <= 0x20ff) ||
    (code >= 0xfe20 && code <= 0xfe2f)
  );
}

function charAt(text: string, index: number): string {
  const code = text.codePointAt(index);
  return code === undefined ? '' : String.fromCodePoint(code);
}

function charBefore(text: string, index: number): string {
  const low = text.charCodeAt(index - 1);
  if (low >= 0xdc00 && low <= 0xdfff && index >= 2) {
    const high = text.charCodeAt(index - 2);
    if (high >= 0xd800 && high <= 0xdbff) return text.slice(index - 2, index);
  }
  return text.slice(index - 1, index);
}

function codePointLength(text: string): number {
  return Array.from(text).length;
}

function casefold(text: string): string {
  return text.toLowerCase().replace(/ß/gu, 'ss').replace(/ς/gu, 'σ');
}

const FOLD_CACHE_SIZE = 4096;
const foldCache = new Map<string, string>();

function foldChar(char: string): string {
  let folded = foldCache.get(char);
  if (folded === undefined) {
    folded = CHAR_FIXES.get(char);
    if (folded === undefined) {
      let kept = '';
      for (const part of char.normalize('NFKD')) if (!isCombining(part)) kept += part;
      folded = casefold(kept);
    }
    if (foldCache.size >= FOLD_CACHE_SIZE) foldCache.clear();
    foldCache.set(char, folded);
  }
  return folded;
}

/** Text for matching: no accents, case folded, typographic quotes and dashes made plain. */
export function foldText(text: string): string {
  let folded = '';
  for (const char of text) folded += foldChar(char);
  return folded;
}

/** Folded text and, for each of its UTF-16 units, the index of the typed character it comes from. */
function foldWithOrigin(text: string): { folded: string; origin: number[] } {
  let folded = '';
  const origin: number[] = [];
  let index = 0;
  for (const char of text) {
    const part = foldChar(char);
    folded += part;
    for (let unit = 0; unit < part.length; unit += 1) origin.push(index);
    index += char.length;
  }
  return { folded, origin };
}

/** `/command` of a type label: folded, letters and digits only (« Sous-traitance »). */
function commandWord(label: string): string {
  let word = '';
  for (const char of foldText(label)) if (isAlnumChar(char)) word += char;
  return word;
}

/** Words of letters and digits (Python's `[^\W_]+`). */
function alnumWords(text: string): string[] {
  const words: string[] = [];
  let current = '';
  for (const char of text) {
    if (isAlnumChar(char)) {
      current += char;
    } else if (current) {
      words.push(current);
      current = '';
    }
  }
  if (current) words.push(current);
  return words;
}

function splitWords(text: string): string[] {
  return text.split(/\s+/u).filter(Boolean);
}

function stripChars(text: string, chars: string, left = true): string {
  let begin = 0;
  let end = text.length;
  if (left) while (begin < end && chars.includes(text[begin])) begin += 1;
  while (end > begin && chars.includes(text[end - 1])) end -= 1;
  return text.slice(begin, end);
}

function rstrip(text: string, chars: string): string {
  return stripChars(text, chars, false);
}

/** Python's `str.splitlines()`. */
function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/u);
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
}

function unique<T>(values: Iterable<T>): T[] {
  return [...new Set(values)];
}

// ----------------------------------------------------------------- helpers ----

/** Worked minutes in hours: `870` -> « 14 h 30 », `540` -> « 9 h », `30` -> « 30 min ». */
export function formatDuration(minutes: number): string {
  const total = Math.max(minutes, 0);
  const hours = Math.floor(total / MINUTES_PER_HOUR);
  const rest = total % MINUTES_PER_HOUR;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${String(rest).padStart(2, '0')}` : `${hours} h`;
}

/** Count the half days in `days`, rounded half up. */
function halfDays(days: number): number {
  return Math.floor(days * 2 + 0.5);
}

/** Working days to the half day, halves up as `formatDays` shows them: 1.25 -> 1.5. */
export function roundDays(days: number): number {
  return halfDays(days) / 2;
}

/** Working days to the half day: `2` -> « 2 j », `1.5` -> « 1 j ½ », `0.5` -> « ½ j ». */
export function formatDays(days: number): string {
  const halves = Math.max(halfDays(days), 0);
  const whole = Math.floor(halves / 2);
  const half = halves % 2;
  if (!whole) return half ? '½ j' : '0 j';
  return half ? `${whole} j ½` : `${whole} j`;
}

/** Length of a dated task: its working days when whole or half (« 2 j »), else hours. */
export function formatSpan(calendar: WorkCalendar, start: Instant, end: Instant): string {
  const days = calendar.wholeDays(start, end);
  if (days !== null) return formatDays(days);
  return formatDuration(calendar.workBetween(start, end));
}

function nextYear(day: number): number {
  const { year, month, date } = dateParts(day);
  const later = makeDate(year + 1, month, date) ?? makeDate(year + 1, month, date - 1); // 29 February
  if (later === null) throw new ValueError('year is out of range');
  return later;
}

/** End of a range typed before its start: next year for `28.12-05.01`, else as typed. */
function wrapped(day: number, after: number): number {
  if (day >= after) return day;
  const later = nextYear(day);
  return later - after <= MAX_WRAP_DAYS ? later : day;
}

/** Start of `29-02.11`: in the month of the end, or the month before. */
function dayBefore(dayOfMonth: number, last: number): number | null {
  const parts = dateParts(last);
  let { year, month } = parts;
  if (dayOfMonth > parts.date) {
    if (month > 1) {
      month -= 1;
    } else {
      year -= 1;
      month = 12;
    }
  }
  return makeDate(year, month, dayOfMonth);
}

function clock(hourText: string | undefined, minuteText: string | undefined): number | null {
  const hour = Number(hourText);
  const minute = Number(minuteText || 0);
  const value = hour * MINUTES_PER_HOUR + minute;
  if (minute >= MINUTES_PER_HOUR || value > MINUTES_PER_DAY) return null;
  return value;
}

function roundDuration(minutes: number): number {
  return Math.max(
    SNAP_MINUTES,
    Math.floor((minutes + Math.floor(SNAP_MINUTES / 2)) / SNAP_MINUTES) * SNAP_MINUTES,
  );
}

function typedDays(groups: Groups): number {
  const count = groups.n ? Number(groups.n.replace(',', '.')) : 0;
  const halves = ['h0', 'h1', 'h2'].filter((name) => groups[name]).length;
  return count + halves / 2;
}

function hoursMinutes(groups: Groups): number {
  if (groups.n) return roundHalfEven(Number(groups.n.replace(',', '.')) * MINUTES_PER_HOUR);
  return Number(groups.w) * MINUTES_PER_HOUR;
}

function plainMinutes(groups: Groups): number {
  return Number(groups.n);
}

const DURATION_READERS: readonly [RegExp, (groups: Groups) => number][] = [
  [HOURS_RE, hoursMinutes],
  [MINUTES_RE, plainMinutes],
];

function spanValue(first: number, last: number): string {
  return first === last ? formatDate(first) : `${formatDate(first)}/${formatDate(last)}`;
}

function cleanTitle(text: string): string {
  let cleaned = text.replace(SPACES_RE, ' ');
  cleaned = cleaned.replace(SPACE_BEFORE_PUNCTUATION_RE, '$1');
  cleaned = cleaned.replace(REPEATED_PUNCTUATION_RE, '$1');
  return stripChars(cleaned, TITLE_EDGES);
}

function capitalised(text: string): string {
  const first = charAt(text, 0);
  return first.toUpperCase() + text.slice(first.length);
}

// --------------------------------------------------------------- directory ----

type Span = { first: number; last: number; isRange: boolean; weekReference: boolean };
type DayWord = { first: string; last: string | undefined; nextWeek: boolean; tokenIndex: number };

function isActive(resource: EntryResource): boolean {
  return (resource.active ?? true) && resource.deleted_at == null;
}

function pushTo(index: Map<string, string[]>, key: string, value: string): void {
  const list = index.get(key);
  if (list) list.push(value);
  else index.set(key, [value]);
}

/** Resources, teams and task types, indexed by their folded names. */
class Directory {
  readonly resources = new Map<string, EntryResource>();
  readonly active: readonly EntryResource[];
  readonly teams: readonly EntryTeam[];
  readonly taskTypes: readonly EntryTaskType[];
  readonly defaultType: string;
  readonly initials = new Map<string, string[]>();
  readonly inactive = new Map<string, string[]>();
  readonly firstNames: readonly [string, string][];
  readonly fullNames: readonly [string, string][];
  readonly keywordTypes = new Map<string, string>();

  constructor(context: EntryContext) {
    for (const resource of context.resources) this.resources.set(resource.id, resource);
    this.active = context.resources.filter(isActive);
    this.teams = [...context.teams];
    this.taskTypes = [...context.taskTypes];
    this.defaultType = defaultTaskTypeKey(this.taskTypes);
    for (const resource of this.active) {
      const initials = (resource.initials ?? '').trim();
      if (initials) pushTo(this.initials, foldText(initials), resource.id);
    }
    for (const resource of context.resources) {
      if ((resource.active ?? true) || resource.deleted_at != null) continue;
      const names = [(resource.initials ?? '').trim(), ...splitWords(resource.name).slice(0, 1)];
      for (const key of unique(names.filter(Boolean).map(foldText))) pushTo(this.inactive, key, resource.id);
    }
    this.firstNames = this.active
      .filter((resource) => splitWords(resource.name).length)
      .map((resource): [string, string] => [splitWords(foldText(resource.name))[0] ?? '', resource.id]);
    this.fullNames = this.active
      .filter((resource) => splitWords(resource.name).length > 1)
      .map((resource): [string, string] => [splitWords(foldText(resource.name)).join(' '), resource.id])
      .sort((a, b) => b[0].length - a[0].length);
    for (const kind of this.taskTypes) {
      for (const keyword of kind.keywords ?? []) {
        const key = foldText(keyword);
        if (!this.keywordTypes.has(key)) this.keywordTypes.set(key, kind.key);
      }
    }
    for (const kind of this.taskTypes) {
      const key = foldText(kind.key);
      if (!this.keywordTypes.has(key)) this.keywordTypes.set(key, kind.key);
    }
  }

  /** Resource ids that `word` names: initials, first name, then first-name prefix. */
  people(word: string, original: string, forced: boolean): string[] {
    if (COMMON_WORDS.has(word) && !isUpper(original) && !forced) return [];
    const byInitials = this.initials.get(word) ?? [];
    if (byInitials.length) return [...byInitials];
    if (this.keywordTypes.has(word) && !forced) return [];
    const found = this.firstNames.filter(([name]) => name === word).map(([, id]) => id);
    if (found.length || codePointLength(word) < MIN_PREFIX_LETTERS || !(forced || everyChar(word, isAlphaChar))) {
      return found;
    }
    return this.firstNames.filter(([name]) => name.startsWith(word)).map(([, id]) => id);
  }

  /** Inactive resources whose initials or first name are `word`. */
  inactivePeople(word: string, original: string, forced: boolean): string[] {
    if (!forced && (this.keywordTypes.has(word) || (COMMON_WORDS.has(word) && !isUpper(original)))) return [];
    return [...(this.inactive.get(word) ?? [])];
  }

  /** End and ids of the longest full name typed at `position`. */
  fullNameAt(folded: string, position: number): { end: number; ids: string[] } | null {
    for (const [name] of this.fullNames) {
      const end = position + name.length;
      if (folded.startsWith(name, position) && matchAt(END_RE, folded, end) !== null) {
        return { end, ids: this.fullNames.filter(([other]) => other === name).map(([, id]) => id) };
      }
    }
    return null;
  }

  team(word: string): EntryTeam | null {
    const names = (team: EntryTeam) => [team.key, team.alias ?? '', team.name].filter(Boolean).map(foldText);
    const exact = this.teams.filter((team) => names(team).includes(word));
    if (exact.length) return exact[0];
    if (codePointLength(word) < MIN_PREFIX_LETTERS) return null;
    const prefixed = this.teams.filter((team) => names(team).some((name) => name.startsWith(word)));
    return prefixed.length === 1 ? prefixed[0] : null;
  }

  members(team: EntryTeam): string[] {
    return this.active.filter((resource) => (resource.team ?? 'atelier') === team.key).map((resource) => resource.id);
  }

  typesForCommand(word: string): string[] {
    let exact = this.taskTypes.filter((kind) => foldText(kind.key) === word).map((kind) => kind.key);
    if (!exact.length) {
      const wanted = commandWord(word);
      exact = this.taskTypes.filter((kind) => commandWord(kind.label) === wanted).map((kind) => kind.key);
    }
    if (exact.length) return exact.slice(0, 1);
    const team = this.teams.find((other) => word === foldText(other.key) || word === foldText(other.alias ?? ''));
    const firstOfTeam = team !== undefined ? this.typeOfTeam(team.key) : null;
    if (firstOfTeam !== null) return [firstOfTeam];
    if (codePointLength(word) < MIN_PREFIX_LETTERS) return [];
    return this.taskTypes
      .filter((kind) => foldText(kind.key).startsWith(word) || foldText(kind.label).startsWith(word))
      .map((kind) => kind.key);
  }

  /** Type of the first task-type keyword in `text` (a final `s` is ignored). */
  typeFromText(text: string): string | null {
    for (const word of alnumWords(foldText(text))) {
      const candidates = word.endsWith('s') ? [word, word.slice(0, -1)] : [word];
      for (const candidate of candidates) {
        const key = this.keywordTypes.get(candidate);
        if (key !== undefined) return key;
      }
    }
    return null;
  }

  typeOfTeam(teamKey: string): string | null {
    return this.taskTypes.find((kind) => (kind.team ?? '') === teamKey)?.key ?? null;
  }

  typeOfResource(resourceId: string): string | null {
    const resource = this.resources.get(resourceId);
    return resource !== undefined ? this.typeOfTeam(resource.team ?? 'atelier') : null;
  }

  typeLabel(key: string): string {
    for (const kind of [...this.taskTypes, ...DEFAULT_TASK_TYPES]) {
      if (kind.key === key) return kind.label;
    }
    return key;
  }

  label(resourceId: string): string {
    const resource = this.resources.get(resourceId);
    return resource !== undefined ? resource.initials || resource.name : resourceId;
  }

  longLabel(resourceId: string): string {
    const resource = this.resources.get(resourceId);
    if (resource === undefined) return resourceId;
    return `${resource.initials ?? ''} ${resource.name}`.trim();
  }
}

// ------------------------------------------------------------------ parser ----

type AddOptions = { ambiguous?: boolean; message?: string };
type Step = (position: number) => number | null;

class Parser {
  private readonly text: string;
  private readonly context: EntryContext;
  private calendar: WorkCalendar;
  private readonly directory: Directory;
  private readonly folded: string;
  private readonly origin: number[];
  private tokens: Token[] = [];
  private readonly spans: [number, number][] = [];
  private readonly warnings: string[] = [];
  private previousWord = '';
  // The word just skipped was a number after RAL, HEA...: "RAL 7016 mat" is a finish.
  private materialValue = false;
  private readonly people: string[] = [];
  private readonly notes: string[] = [];
  private taskType = '';
  private absenceReason: AbsenceReason | null = null;
  private projectNumber = '';
  private project: ProjectInfo | null = null;
  private dossierSeen = false;
  private readonly typedSpans: Span[] = [];
  private readonly dayWords: DayWord[] = [];
  private part: DayPart | null = null;
  private startMinute: number | null = null;
  private endMinute: number | null = null;
  private readonly bareHours: [number, number][] = [];
  // Typed durations: worked minutes of the hours, and working days as half days.
  private readonly durations: number[] = [];
  private halfDays = 0;
  private provisional = false;
  private urgent = false;
  private deadline: number | null = null;
  private ignoredDuration = false;
  private nightWord = false;
  private offHours = false;

  constructor(text: string, context: EntryContext) {
    this.text = text;
    this.context = context;
    this.calendar = context.calendar;
    this.directory = new Directory(context);
    const { folded, origin } = foldWithOrigin(text);
    this.folded = folded;
    this.origin = origin;
  }

  // ------------------------------------------------------------- result ----

  parse(): ParsedEntry {
    this.scan();
    const people = this.resolvedPeople();
    if (this.context.calendarFor && people.length) this.calendar = this.context.calendarFor(people[0]);
    this.resolveBareHours();
    const span = this.resolveSpan();
    const typedTitle = cleanTitle(this.leftover());
    const taskType = this.resolveTaskType(typedTitle, people);
    let absence: AbsenceDraft | null = null;
    if (this.absenceReason !== null) absence = this.makeAbsence(span, people, typedTitle);
    const night = absence === null ? this.night(span) : null;
    let start: Instant | null = null;
    let end: Instant | null = null;
    if (night !== null) {
      [start, end] = night;
      this.offHours = true;
    } else if (absence === null) {
      [start, end] = this.schedule(span);
    }
    if (start !== null) this.warnIfPast(instantDay(start));
    const destination = this.destination(typedTitle, absence, start, people);
    let title = typedTitle;
    if (absence === null && destination !== 'invalid') title = this.title(typedTitle, taskType);
    const [estimate, estimateDays] = this.estimate(start, end, absence);
    const entry: ParsedEntry = {
      projectNumber: this.projectNumber,
      project: this.project,
      title,
      taskType,
      resourceIds: people,
      start,
      end,
      estimateMinutes: estimate,
      provisional: this.provisional,
      urgent: this.urgent,
      deadline: this.deadline,
      notes: this.notes.join('\n'),
      absence,
      destination,
      tokens: [...this.tokens],
      warnings: [],
      interpretation: '',
      estimateDays,
      ignoredDuration: this.ignoredDuration,
      offHours: this.offHours,
    };
    const warnings = unique(this.warnings);
    return { ...entry, warnings, interpretation: this.describe(entry) };
  }

  /**
   * `estimateMinutes` and `estimateDays` of the entry.
   *
   * Days typed alone keep their count once dated: `ven ½j` and `jeu 1j½` both end on
   * Friday at noon, which `wholeDays` would read as 1 j and 2 j.
   */
  private estimate(start: Instant | null, end: Instant | null, absence: AbsenceDraft | null): [number, number] {
    if (absence !== null) return [0, 0];
    if (this.offHours && start !== null && end !== null) return [end - start, 0];
    const typed = !this.durations.length ? this.halfDays / 2 : 0;
    if (start === null || end === null) return [this.durationMinutes(), typed];
    const minutes = this.calendar.workBetween(start, end);
    if (typed && !this.ignoredDuration) return [minutes, typed];
    return [minutes, this.calendar.wholeDays(start, end) || 0];
  }

  /** Typed durations as a load: the hours as typed, a day as `DAY_MINUTES`. */
  private durationMinutes(): number {
    return this.durations.reduce((total, minutes) => total + minutes, 0) + this.halfDays * HALF_DAY_MINUTES;
  }

  /** Typed people; the cell's rows only when nobody, even unknown, was typed. */
  private resolvedPeople(): string[] {
    const typed = unique(this.people);
    if (typed.length || this.tokens.some((token) => token.kind === 'person' || token.kind === 'team')) return typed;
    return [...(this.context.cellResourceIds ?? [])];
  }

  private warnIfPast(day: number): void {
    if (day < this.context.today) this.warn(`Date passée : ${this.dayLabel(day)}`);
  }

  private title(typedTitle: string, taskType: string): string {
    if (typedTitle) return typedTitle;
    if (this.project !== null && this.project.designation) return this.project.designation;
    return this.directory.typeLabel(taskType);
  }

  private resolveTaskType(title: string, people: readonly string[]): string {
    if (this.taskType) return this.taskType;
    const designation = this.project !== null ? (this.project.designation ?? '') : '';
    let found = this.directory.typeFromText(title || designation);
    if (found === null && people.length) found = this.directory.typeOfResource(people[0]);
    return found || this.directory.defaultType;
  }

  private destination(
    title: string,
    absence: AbsenceDraft | null,
    start: Instant | null,
    people: readonly string[],
  ): Destination {
    const meaningful =
      Boolean(title || this.projectNumber) ||
      this.tokens.some((token) => token.kind !== 'provisional' && token.kind !== 'urgent' && !token.ambiguous);
    if (!meaningful) return 'invalid';
    if (absence !== null) {
      if (absence.resourceIds.length) return 'absence';
      this.warn('Indiquer qui est absent');
      return 'invalid';
    }
    if (start === null) return 'backlog';
    return people.length ? 'grid' : 'unassigned';
  }

  // ------------------------------------------------------------ scanning ----

  private scan(): void {
    const steps: Step[] = [
      (at) => this.scanNote(at),
      (at) => this.scanUpperInitials(at),
      (at) => this.scanDeadline(at),
      (at) => this.scanPrefixed(at),
      (at) => this.scanCommand(at),
      (at) => this.scanFlags(at),
      (at) => this.scanWeek(at),
      (at) => this.scanDate(at),
      (at) => this.scanTimeRange(at),
      (at) => this.scanStartTime(at),
      (at) => this.scanDuration(at),
      (at) => this.scanBareHour(at),
      (at) => this.scanPart(at),
      (at) => this.scanNight(at),
      (at) => this.scanDay(at),
      (at) => this.scanFlagWord(at),
      (at) => this.scanDossier(at),
      (at) => this.scanPerson(at),
    ];
    let position = 0;
    while (position < this.folded.length) {
      const separator = matchAt(SEPARATOR_RE, this.folded, position);
      if (separator !== null) {
        position = separator.end;
        continue;
      }
      let found: number | null = null;
      for (const step of steps) {
        found = step(position);
        if (found !== null) break;
      }
      position = found ?? this.skipWord(position);
    }
  }

  private originalSpan(start: number, end: number): [number, number] {
    const begin = start < this.origin.length ? this.origin[start] : this.text.length;
    let finish = begin;
    if (end > start) {
      const last = this.origin[end - 1];
      finish = last + charAt(this.text, last).length;
    }
    while (finish < this.text.length) {
      const next = charAt(this.text, finish);
      if (!isCombining(next)) break;
      finish += next.length;
    }
    return [begin, finish];
  }

  private source(start: number, end: number): string {
    const [begin, finish] = this.originalSpan(start, end);
    return this.text.slice(begin, finish);
  }

  private warn(message: string): void {
    this.warnings.push(message);
  }

  private add(kind: TokenKind, start: number, end: number, value = '', options: AddOptions = {}): number {
    const { ambiguous = false, message = '' } = options;
    const [begin, finish] = this.originalSpan(start, end);
    this.tokens.push({
      kind,
      text: this.text.slice(begin, finish),
      value,
      start: begin,
      end: finish,
      ambiguous,
      message,
    });
    this.spans.push([begin, finish]);
    if (message) this.warn(message);
    this.previousWord = '';
    this.materialValue = false;
    return end;
  }

  private skipWord(position: number): number {
    const match = matchAt(WORD_RE, this.folded, position);
    const end = match !== null ? match.end : position + 1;
    const word = rstrip(this.folded.slice(position, end), '.:');
    this.materialValue = this.afterMaterial() && everyChar(word, isDigitChar);
    this.previousWord = word;
    return end;
  }

  private scanNote(position: number): number | null {
    if (this.folded[position] === '"' && position && isAlnumChar(charBefore(this.folded, position))) {
      return null; // an inch mark: tube 2"
    }
    const match = matchAt(NOTE_RE, this.folded, position);
    if (match === null) return null;
    const quoted = match.groups.q !== undefined;
    const inner = (quoted ? match.groups.q : match.groups.g) ?? '';
    const innerStart = match.start + 1;
    const note = this.source(innerStart, innerStart + inner.length).trim();
    if (note) this.notes.push(note);
    const closing = quoted ? '"' : '»';
    const closed = match.text.length > 1 && match.text.endsWith(closing);
    const message = closed ? '' : 'Guillemet non fermé : la suite est une note';
    return this.add('note', match.start, match.end, note, { message });
  }

  private scanUpperInitials(position: number): number | null {
    const match = matchAt(WORD_RE, this.folded, position);
    if (match === null) return null;
    const word = rstrip(match.text, '.:');
    const end = position + word.length;
    if (!word || MATERIAL_PREFIXES.has(word) || !isUpper(this.source(position, end))) return null;
    const ids = this.directory.initials.get(word) ?? [];
    return ids.length ? this.addPeople(position, end, ids) : null;
  }

  private scanDeadline(position: number): number | null {
    const match = matchAt(DEADLINE_RE, this.folded, position);
    if (match === null) return null;
    const { d, m, y, w } = match.groups;
    const day =
      w !== undefined ? this.resolveDayWord(w, null, false) : this.makeTypedDate(d ?? '', m ?? '', y);
    if (day === null) {
      const text = this.source(match.start, match.end);
      return this.add('deadline', match.start, match.end, '', {
        ambiguous: true,
        message: `Date invalide : ${text}`,
      });
    }
    this.deadline = this.deadline ?? day;
    return this.add('deadline', match.start, match.end, formatDate(day));
  }

  private scanPrefixed(position: number): number | null {
    const char = this.folded[position];
    if (char !== '+' && char !== '@') return null;
    const inner = position + 1;
    const full = this.directory.fullNameAt(this.folded, inner);
    if (full !== null) return this.addPeople(position, full.end, full.ids);
    const match = matchAt(WORD_RE, this.folded, inner);
    if (match === null) {
      this.spans.push(this.originalSpan(position, inner));
      return inner;
    }
    const word = rstrip(match.text, '.:');
    const end = inner + word.length;
    const ids = this.directory.people(word, this.source(inner, end), true);
    if (ids.length) return this.addPeople(position, end, ids);
    const team = this.directory.team(word);
    if (team === null) return this.addUnknown(position, inner, end);
    const members = this.directory.members(team);
    this.people.push(...members);
    const message = members.length ? '' : `L'équipe ${team.name} n'a aucun membre actif`;
    return this.add('team', position, end, team.key, { message });
  }

  private addUnknown(position: number, inner: number, end: number): number {
    const word = this.folded.slice(inner, end);
    const inactive = this.directory.inactivePeople(word, this.source(inner, end), true);
    if (inactive.length) return this.addInactive(position, end, inactive);
    const message = `« ${this.source(position, end)} » : personne ou équipe inconnue`;
    return this.add('person', position, end, '', { ambiguous: true, message });
  }

  private scanCommand(position: number): number | null {
    const match = matchAt(COMMAND_RE, this.folded, position);
    if (match === null) return null;
    const word = match.groups.w ?? '';
    const reason = ABSENCE_COMMANDS.get(word);
    if (reason !== undefined) {
      this.absenceReason = this.absenceReason ?? reason;
      return this.add('absence', match.start, match.end, reason);
    }
    const keys = this.directory.typesForCommand(word);
    if (keys.length === 1) {
      this.taskType = this.taskType || keys[0];
      return this.add('type', match.start, match.end, keys[0]);
    }
    const text = this.source(match.start, match.end);
    let message: string;
    if (keys.length) {
      const labels = keys.map((key) => this.directory.typeLabel(key)).join(', ');
      message = `« ${text} » : plusieurs types possibles (${labels})`;
    } else {
      message = `Commande « ${text} » inconnue`;
    }
    return this.add('type', match.start, match.end, keys.join(','), { ambiguous: true, message });
  }

  private scanFlags(position: number): number | null {
    const match = matchAt(FLAGS_RE, this.folded, position);
    if (match === null) return null;
    for (let offset = 0; offset < match.text.length; offset += 1) {
      const at = match.start + offset;
      if (match.text[offset] === '?') {
        this.provisional = true;
        this.add('provisional', at, at + 1);
      } else {
        this.urgent = true;
        this.add('urgent', at, at + 1);
      }
    }
    return match.end;
  }

  private scanWeek(position: number): number | null {
    const match = matchAt(WEEK_RE, this.folded, position);
    if (match === null) return null;
    const text = this.source(match.start, match.end);
    const { a, b } = match.groups;
    const first = this.isoMonday(Number(a));
    let lastMonday = first;
    if (b !== undefined && first !== null) lastMonday = this.isoMonday(Number(b), first);
    if (first === null || lastMonday === null) {
      const message = `Semaine invalide : ${text}`;
      return this.add('week', match.start, match.end, '', { ambiguous: true, message });
    }
    if (lastMonday < first) {
      const message = `Fin avant le début : ${text}`;
      return this.add('week', match.start, match.end, '', { ambiguous: true, message });
    }
    const last = lastMonday + WEEK_LAST_WEEKDAY;
    this.typedSpans.push({ first, last, isRange: true, weekReference: b === undefined });
    return this.add('week', match.start, match.end, spanValue(first, last));
  }

  private scanDate(position: number): number | null {
    let match = matchAt(SHORT_DATE_RANGE_RE, this.folded, position);
    const short = match !== null;
    if (match === null) match = matchAt(DATE_RE, this.folded, position);
    if (match === null) return null;
    const { ad, am, ay, bd, bm, by } = match.groups;
    if (ay === undefined && by === undefined && this.isMeasure(match.end)) return null;
    let first: number | null;
    let last: number | null;
    if (!short) {
      first = this.makeTypedDate(ad ?? '', am ?? '', ay);
      last = first;
      if (bd !== undefined && first !== null) last = this.makeTypedDate(bd, bm ?? '', by, first);
    } else {
      last = this.makeTypedDate(bd ?? '', bm ?? '', by);
      first = last !== null ? dayBefore(Number(ad), last) : null;
    }
    const text = this.source(match.start, match.end);
    if (first === null || last === null) {
      return this.add('date', match.start, match.end, '', { ambiguous: true, message: `Date invalide : ${text}` });
    }
    if (last < first) {
      const message = `Fin avant le début : ${text}`;
      return this.add('date', match.start, match.end, '', { ambiguous: true, message });
    }
    this.typedSpans.push({ first, last, isRange: last > first, weekReference: false });
    return this.add('date', match.start, match.end, spanValue(first, last));
  }

  private scanTimeRange(position: number): number | null {
    const match = matchAt(TIME_RANGE_RE, this.folded, position);
    if (match === null) return null;
    const { ah, au, am, bh, bm } = match.groups;
    const first = clock(ah, am);
    const last = clock(bh, bm);
    if (first === null || last === null) return this.invalidTime(match);
    if (au === undefined && Math.max(first, last) < this.calendar.dayStartMinute) {
      // "soudure 2 à 3h" is an estimate, not a night shift.
      const minutes = Math.max(first, last);
      if (minutes > 0) {
        const text = this.source(match.start, match.end);
        const duration = formatDuration(roundDuration(minutes));
        this.warn(`« ${text} » compris comme une durée de ${duration}`);
      }
      return this.addDuration(match.start, match.end, minutes);
    }
    return this.setTimes(match, snap(first), snap(last), [first, last]);
  }

  private scanStartTime(position: number): number | null {
    const match = matchAt(START_TIME_RE, this.folded, position) ?? matchAt(CLOCK_RE, this.folded, position);
    if (match === null) return null;
    const value = clock(match.groups.h, match.groups.m);
    if (value === null) return this.invalidTime(match);
    return this.setTimes(match, snap(value), null, [value, null]);
  }

  private invalidTime(match: Found): number {
    const message = `Heure invalide : ${this.source(match.start, match.end)}`;
    return this.add('time', match.start, match.end, '', { ambiguous: true, message });
  }

  private setTimes(match: Found, first: number, last: number | null, raw: [number, number | null]): number {
    const text = this.source(match.start, match.end);
    const value = last === null ? formatHhmm(first) : `${formatHhmm(first)}-${formatHhmm(last)}`;
    let message = first === raw[0] && last === raw[1] ? '' : `${text} arrondi à ${value}`;
    if (this.startMinute !== null) {
      message = 'Plusieurs heures de début : la première est retenue';
    } else {
      this.startMinute = first;
      this.endMinute = last;
    }
    return this.add('time', match.start, match.end, value, { message });
  }

  private scanDuration(position: number): number | null {
    const days = matchAt(DAYS_RE, this.folded, position);
    if (days !== null) return this.addDays(days.start, days.end, typedDays(days.groups));
    for (const [pattern, reader] of DURATION_READERS) {
      const match = matchAt(pattern, this.folded, position);
      if (match !== null) return this.addDuration(match.start, match.end, reader(match.groups));
    }
    return null;
  }

  private addDays(start: number, end: number, days: number): number {
    const text = this.source(start, end);
    if (days <= 0) {
      return this.add('duration', start, end, '', { ambiguous: true, message: `Durée nulle : ${text}` });
    }
    if (this.tooLong(days * DAY_MINUTES)) {
      return this.add('duration', start, end, '', { ambiguous: true, message: `Durée trop longue : ${text}` });
    }
    const halves = Math.max(halfDays(days), 1);
    const message = halves === days * 2 ? '' : `${text} arrondi à ${formatDays(halves / 2)}`;
    this.halfDays += halves;
    return this.add('duration', start, end, `${halves / 2}j`, { message });
  }

  private addDuration(start: number, end: number, minutes: number): number {
    const text = this.source(start, end);
    if (minutes <= 0) {
      return this.add('duration', start, end, '', { ambiguous: true, message: `Durée nulle : ${text}` });
    }
    if (this.tooLong(minutes)) {
      return this.add('duration', start, end, '', { ambiguous: true, message: `Durée trop longue : ${text}` });
    }
    const rounded = roundDuration(minutes);
    const message = rounded === minutes ? '' : `${text} arrondi à ${formatDuration(rounded)}`;
    this.durations.push(rounded);
    return this.add('duration', start, end, String(rounded), { message });
  }

  private tooLong(minutes: number): boolean {
    return this.durationMinutes() + minutes > MAX_DURATION_MINUTES;
  }

  private scanBareHour(position: number): number | null {
    const match = matchAt(BARE_HOUR_RE, this.folded, position);
    if (match === null) return null;
    const value = clock(match.groups.h, match.groups.m);
    if (value === null) return this.invalidTime(match);
    this.bareHours.push([this.tokens.length, value]);
    return this.add('time', match.start, match.end);
  }

  private scanPart(position: number): number | null {
    const match = matchAt(PART_RE, this.folded, position);
    if (match === null) return null;
    const word = match.groups.w ?? '';
    if (word === 'am') {
      const message = '« am » est ambigu : écrire « matin » ou « aprem »';
      return this.add('part', match.start, match.end, '', { ambiguous: true, message });
    }
    if (word === 'mat' && this.materialValue) return this.skipWord(position);
    const part: DayPart = word.startsWith('mat') ? 'morning' : 'afternoon';
    this.part = this.part ?? part;
    return this.add('part', match.start, match.end, part);
  }

  private scanNight(position: number): number | null {
    const match = matchAt(NIGHT_RE, this.folded, position);
    if (match === null) return null;
    this.nightWord = true;
    return this.add('night', match.start, match.end, 'nuit');
  }

  private scanDay(position: number): number | null {
    const match = matchAt(DAY_RE, this.folded, position);
    if (match === null) return null;
    this.dayWords.push({
      first: match.groups.a ?? '',
      last: match.groups.b,
      nextWeek: match.groups.next !== undefined,
      tokenIndex: this.tokens.length,
    });
    return this.add('day', match.start, match.end);
  }

  private scanFlagWord(position: number): number | null {
    const match = matchAt(FLAG_WORD_RE, this.folded, position);
    if (match === null) return null;
    if (match.groups.w === 'provisoire') {
      this.provisional = true;
      return this.add('provisional', match.start, match.end);
    }
    this.urgent = true;
    return this.add('urgent', match.start, match.end);
  }

  private scanDossier(position: number): number | null {
    if (this.dossierSeen || this.afterMaterial()) return null;
    const match = matchAt(DOSSIER_RE, this.folded, position);
    if (match === null || this.unitFollows(match.end)) return null;
    this.dossierSeen = true;
    const [number, project] = this.findProject(match.groups.y, match.groups.n ?? '', match.groups.s);
    this.projectNumber = number;
    this.project = project;
    if (project !== null) return this.add('project', match.start, match.end, number);
    const message = `${number} introuvable dans le répertoire`;
    return this.add('project', match.start, match.end, number, { ambiguous: true, message });
  }

  private scanPerson(position: number): number | null {
    const full = this.directory.fullNameAt(this.folded, position);
    if (full !== null) return this.addPeople(position, full.end, full.ids);
    const match = matchAt(WORD_RE, this.folded, position);
    if (match === null) return null;
    const word = rstrip(match.text, '.:');
    const end = position + word.length;
    if (!word) return null;
    const original = this.source(position, end);
    const ids = this.directory.people(word, original, false);
    if (ids.length) return this.addPeople(position, end, ids);
    const inactive = this.directory.inactivePeople(word, original, false);
    return inactive.length ? this.addInactive(position, end, inactive) : null;
  }

  private addPeople(start: number, end: number, ids: readonly string[]): number {
    if (ids.length === 1) {
      this.people.push(ids[0]);
      return this.add('person', start, end, ids[0]);
    }
    const names = ids.map((id) => this.directory.longLabel(id)).join(', ');
    const message = `« ${this.source(start, end)} » : plusieurs personnes possibles (${names})`;
    return this.add('person', start, end, ids.join(','), { ambiguous: true, message });
  }

  private addInactive(start: number, end: number, ids: readonly string[]): number {
    const names = ids.map((id) => this.directory.longLabel(id)).join(', ');
    const message = `« ${this.source(start, end)} » : personne inactive (${names})`;
    return this.add('person', start, end, ids.join(','), { ambiguous: true, message });
  }

  private afterMaterial(): boolean {
    const word = this.previousWord;
    return MATERIAL_PREFIXES.has(word) || BOLT_RE.test(word);
  }

  private unitFollows(end: number, units: ReadonlySet<string> = UNIT_WORDS): boolean {
    const match = matchAt(NEXT_WORD_RE, this.folded, end);
    return match !== null && units.has(rstrip(match.first ?? '', '.'));
  }

  /** Whether a `1.5` ending at `end` is a measure or a duration, not a date. */
  private isMeasure(end: number): boolean {
    return this.afterMaterial() || this.unitFollows(end, MEASURE_WORDS);
  }

  private findProject(
    year: string | undefined,
    number: string,
    sub: string | undefined,
  ): [string, ProjectInfo | null] {
    let suffix = '';
    if (sub) suffix = /^\d+$/u.test(sub) ? `-${Number(sub)}` : `-${sub.toUpperCase()}`;
    const defaultYear = this.context.defaultYear;
    const years = year ? [Number(year)] : [defaultYear, defaultYear - 1];
    for (const candidateYear of years) {
      const candidate = `${candidateYear}-${number}${suffix}`;
      const project = this.context.findProject(candidate);
      if (project != null) return [candidate, project];
    }
    return [`${years[0]}-${number}${suffix}`, null];
  }

  // --------------------------------------------------------------- days ----

  /** Typed date; without year, the next one unless recent (or the range would be long). */
  private makeTypedDate(
    dayText: string,
    monthText: string,
    yearText: string | undefined,
    after: number | null = null,
  ): number | null {
    const today = this.context.today;
    const todayYear = dateParts(today).year;
    let year: number;
    if (yearText) {
      year = Number(yearText);
      year += year < TWO_DIGIT_YEARS ? CENTURY : 0;
      if (!(todayYear - YEARS_BACK <= year && year <= todayYear + YEARS_AHEAD)) return null;
    } else {
      year = dateParts(after ?? today).year;
    }
    const day = makeDate(year, Number(monthText), Number(dayText));
    if (day === null) return null;
    if (yearText) return day;
    if (after !== null) return wrapped(day, after);
    return day < today - PAST_DATE_DAYS ? nextYear(day) : day;
  }

  private isoMonday(week: number, after: number | null = null): number | null {
    const today = isoCalendar(this.context.today);
    const year =
      after !== null ? isoCalendar(after).year : today.year + (week < today.week - PAST_WEEKS ? 1 : 0);
    let monday = fromIsoCalendar(year, week, 1);
    if (monday === null) return null;
    if (after !== null && monday < after) {
      const later = fromIsoCalendar(year + 1, week, 1);
      if (later === null) return null;
      monday = later - after <= MAX_WRAP_DAYS ? later : monday;
    }
    return monday;
  }

  private resolveDayWord(word: string, reference: number | null, nextWeek: boolean): number {
    const today = this.context.today;
    const relative = RELATIVE_DAYS.get(word);
    if (relative !== undefined) {
      let day = today;
      for (let count = 0; count < relative; count += 1) day = this.calendar.nextWorkday(day);
      return day;
    }
    const dayOfWeek = WEEKDAY_WORDS.get(word) ?? 0;
    if (nextWeek) return today + mod(dayOfWeek - weekday(today) - 1, DAYS_PER_WEEK) + 1;
    const monday = mondayOf(reference ?? this.context.weekStart);
    let day = monday + dayOfWeek;
    if (reference === null && monday === mondayOf(today) && day < today) day += DAYS_PER_WEEK;
    return day;
  }

  private dayWordSpan(word: DayWord, reference: number | null): Span {
    const first = this.resolveDayWord(word.first, reference, word.nextWeek);
    let last = first;
    const lastWeekday = word.last !== undefined ? WEEKDAY_WORDS.get(word.last) : undefined;
    if (lastWeekday !== undefined) {
      last = first + mod(lastWeekday - weekday(first), DAYS_PER_WEEK);
    } else if (word.last !== undefined) {
      last = Math.max(first, this.resolveDayWord(word.last, null, false));
    }
    const token = this.tokens[word.tokenIndex];
    this.tokens[word.tokenIndex] = { ...token, value: spanValue(first, last) };
    return { first, last, isRange: last > first, weekReference: false };
  }

  private resolveSpan(): Span | null {
    const typed = this.typedSpans;
    if (typed.length > 1) this.warn('Plusieurs dates : la première est retenue');
    const span = typed.length ? typed[0] : null;
    if (!this.dayWords.length) return span;
    if (this.dayWords.length > 1) this.warn('Plusieurs jours : le premier est retenu');
    const word = this.dayWords[0];
    if (span === null || span.weekReference) return this.dayWordSpan(word, span !== null ? span.first : null);
    const named = this.dayWordSpan(word, null);
    const same = WEEKDAY_WORDS.has(word.first)
      ? weekday(named.first) === weekday(span.first)
      : named.first === span.first;
    if (!same) {
      const text = this.tokens[word.tokenIndex].text;
      this.warn(`« ${text} » ne correspond pas au ${this.dayLabel(span.first)} : la date est retenue`);
    }
    return span;
  }

  private workedSpan(span: Span): [number, number] {
    const calendar = this.calendar;
    let { first, last } = span;
    if (!calendar.isWorkday(first)) {
      const moved = calendar.nextWorkday(first);
      this.warn(
        `${this.dayLabel(first)} n'est pas travaillé${this.closedReason(first)} : ` +
          `début le ${this.dayLabel(moved)}`,
      );
      first = moved;
    }
    if (last > first && !calendar.isWorkday(last)) last = calendar.previousWorkday(last);
    return [first, Math.max(first, last)];
  }

  private closedReason(day: number): string {
    const holiday = this.calendar.holiday(day);
    if (holiday !== null) return ` (${holiday.name})`;
    return this.calendar.isClosed(day) ? ' (fermeture)' : '';
  }

  // ------------------------------------------------------------- times ----

  /**
   * Read a bare `7h` as a start time on a typed day inside working hours, else hours.
   *
   * On a cell without typed day it is a duration: the cell already gives the start.
   */
  private resolveBareHours(): void {
    const hasDay = Boolean(this.typedSpans.length || this.dayWords.length);
    const opening = this.calendar.dayStartMinute;
    const closing = this.calendar.dayEndMinute;
    for (const [index, minutes] of this.bareHours) {
      const token = this.tokens[index];
      let message: string;
      if (hasDay && this.startMinute === null && opening <= minutes && minutes <= closing) {
        const value = snap(minutes);
        message = value === minutes ? '' : `${token.text} arrondi à ${formatHhmm(value)}`;
        this.startMinute = value;
        this.tokens[index] = { ...token, value: formatHhmm(value), message };
      } else if (minutes <= 0 || this.tooLong(minutes)) {
        const problem = minutes <= 0 ? 'Durée nulle' : 'Durée trop longue';
        message = `${problem} : ${token.text}`;
        this.tokens[index] = { ...token, kind: 'duration', ambiguous: true, message };
      } else {
        const value = roundDuration(minutes);
        message = value === minutes ? '' : `${token.text} arrondi à ${formatDuration(value)}`;
        if (hasDay && minutes > closing) {
          message = `« ${token.text} » compris comme une durée de ${formatDuration(value)}`;
        }
        this.durations.push(value);
        this.tokens[index] = { ...token, kind: 'duration', value: String(value), message };
      }
      if (message) this.warn(message);
    }
  }

  private periods(day: number): readonly WorkPeriod[] {
    return this.calendar.periodsOfWeekday(weekday(day));
  }

  private afternoon(day: number): WorkPeriod {
    const periods = this.periods(day);
    if (periods.length > 1) return [periods[1][0], periods[periods.length - 1][1]];
    this.warn(`Pas d'après-midi travaillé le ${DAY_NAMES[weekday(day)]} : matin retenu`);
    return periods[0];
  }

  private startMinuteOf(day: number): number {
    if (this.startMinute !== null) return this.startMinute;
    if (this.part === 'afternoon') return this.afternoon(day)[0];
    return this.periods(day)[0][0];
  }

  private endMinuteOf(day: number): number {
    if (this.endMinute !== null) return this.endMinute;
    if (this.part === 'morning') return this.periods(day)[0][1];
    if (this.part === 'afternoon') return this.afternoon(day)[1];
    const periods = this.periods(day);
    return periods[periods.length - 1][1];
  }

  /**
   * Exact span of night work, null when the entry is not one.
   *
   * `nuit` or a start typed from 18:00 makes a night (a start before 06:00 too with
   * `nuit`: still the evening's night); a range ending before it starts ends the next
   * morning, `nuit` alone lasts from 21:00 to 05:00, and a night ends by 06:30. A night
   * typed on a Friday, Saturday or Sunday evening is refused with a warning and the entry
   * is read as daytime work on that day's hours.
   */
  private night(span: Span | null): [Instant, Instant] | null {
    const first = this.startMinute;
    const eveningStart = first !== null && first >= NIGHT_EARLIEST_START;
    if (!(this.nightWord || eveningStart)) return null;
    const day = this.nightDay(span);
    if (!nightAllowed(day)) {
      // The evening hours mean nothing by day: the day's own hours are kept.
      this.warn(NIGHT_WEEKEND_MESSAGE);
      this.startMinute = null;
      this.endMinute = null;
      return null;
    }
    const base = dayStart(day);
    const start = this.nightStart(base);
    const typed = start !== base + DEFAULT_NIGHT_START || first === DEFAULT_NIGHT_START;
    const latest = nightWindow(day)[1];
    let end: Instant;
    if (this.endMinute !== null && typed) {
      end = base + this.endMinute;
      while (end <= start) end += MINUTES_PER_DAY;
      this.ignoreDuration();
    } else if (this.durations.length || this.halfDays) {
      end = start + this.durationMinutes();
    } else {
      end = Math.max(base + MINUTES_PER_DAY + DEFAULT_NIGHT_END, start + MIN_NIGHT_MINUTES);
    }
    if (end > latest) {
      this.warn('Une nuit finit à 06:30 au plus tard : fin à 06:30');
      end = Math.max(latest, start + MIN_NIGHT_MINUTES);
    }
    return [start, end];
  }

  /** Evening of a night: the typed day, else the cell's, else today. */
  private nightDay(span: Span | null): number {
    const context = this.context;
    if (span !== null) {
      if (span.isRange) this.warn(`Une nuit à la fois : nuit du ${this.dayLabel(span.first)} retenue`);
      return span.first;
    }
    if (context.cellStart != null) return instantDay(context.cellStart);
    return context.today;
  }

  /**
   * Start of the night of the day at `base`: the typed evening hour, else 21:00.
   *
   * With `nuit`, an hour before 06:00 is after midnight, still that night.
   */
  private nightStart(base: Instant): Instant {
    const first = this.startMinute;
    if (first !== null && first >= NIGHT_EARLIEST_START) return base + first;
    if (first !== null && first < NIGHT_MORNING_START && this.nightWord) return base + MINUTES_PER_DAY + first;
    if (first !== null) this.warn(`${formatHhmm(first)} n'est pas une heure de nuit : début à 21:00`);
    return base + DEFAULT_NIGHT_START;
  }

  private schedule(typedSpan: Span | null): [Instant | null, Instant | null] {
    const context = this.context;
    let span = typedSpan;
    const typedTime = this.startMinute !== null || this.endMinute !== null || this.part !== null;
    if (span === null && !typedTime) {
      if (context.cellStart == null) return [null, null];
      const start = this.calendar.workingStart(context.cellStart);
      if (start !== context.cellStart) {
        const reason = this.closedReason(instantDay(context.cellStart));
        this.warn(`Début reporté au ${this.instantLabel(start)}${reason}`);
      }
      return [start, this.endAfter(start, instantDay(start), true)];
    }
    const fromCell = span === null;
    if (span === null) {
      const base = context.cellStart != null ? instantDay(context.cellStart) : context.today;
      span = { first: base, last: base, isRange: false, weekReference: false };
    }
    const [first, last] = this.workedSpan(span);
    const typedStart = dayStart(first) + this.startMinuteOf(first);
    const start = this.calendar.workingStart(typedStart);
    if (instantDay(start) !== first) {
      const periods = this.periods(first);
      const closing = formatHhmm(periods[periods.length - 1][1]);
      this.warn(
        `Le ${DAY_NAMES[weekday(first)]} finit à ${closing} : début reporté au ${this.instantLabel(start)}`,
      );
    }
    let end: Instant;
    if (span.isRange || this.endMinute !== null) {
      const typedEnd = dayStart(last) + this.endMinuteOf(last);
      end = this.typedEnd(typedStart, typedEnd, start, !span.isRange);
      this.ignoreDuration();
    } else {
      end = this.endAfter(start, instantDay(start), fromCell);
    }
    if (end <= start) {
      this.warn('La fin précède le début : 30 min retenues');
      end = this.calendar.addWork(start, SNAP_MINUTES);
    } else if (span.isRange && this.part !== null && instantDay(end) !== instantDay(start)) {
      this.warn(`Plage continue du ${this.instantLabel(start)} au ${this.instantLabel(end)}`);
    }
    return [start, end];
  }

  /** End of a typed range; hours typed outside working time keep their length. */
  private typedEnd(typedStart: Instant, typedEnd: Instant, start: Instant, singleDay: boolean): Instant {
    if (singleDay && typedEnd > typedStart && !this.calendar.workBetween(typedStart, typedEnd)) {
      const hours = `${formatHhmm(minuteOfDay(typedStart))}-${formatHhmm(minuteOfDay(typedEnd))}`;
      const length = typedEnd - typedStart;
      this.warn(`${hours} hors des heures de travail : durée de ${formatDuration(length)} gardée`);
      return this.calendar.addWork(start, length);
    }
    return this.calendar.workingEnd(typedEnd);
  }

  /** End of a task without typed range: duration, part of day, cell, time or day. */
  private endAfter(start: Instant, day: number, fromCell: boolean): Instant {
    const cellEnd = this.context.cellEnd;
    if (this.halfDays || this.durations.length) return this.endOfDurations(start);
    if (this.part !== null) return this.calendar.workingEnd(dayStart(day) + this.endMinuteOf(day));
    if (fromCell && cellEnd != null && this.calendar.workingEnd(cellEnd) > start) {
      return this.calendar.workingEnd(cellEnd);
    }
    if (this.startMinute !== null) return this.calendar.addWork(start, SNAP_MINUTES);
    return this.calendar.workingEnd(dayStart(day) + this.endMinuteOf(day));
  }

  /** Working days counted from the day of `start`, then the worked hours after them. */
  private endOfDurations(start: Instant): Instant {
    let end = start;
    if (this.halfDays) end = this.calendar.addDays(start, this.halfDays / 2);
    const minutes = this.durations.reduce((total, value) => total + value, 0);
    return minutes ? this.calendar.addWork(end, minutes) : end;
  }

  private ignoreDuration(): void {
    if (!(this.durations.length || this.halfDays)) return;
    this.ignoredDuration = true;
    const message = 'Durée ignorée : la plage tapée fixe la fin';
    this.warn(message);
    this.tokens = this.tokens.map((token) => (token.kind === 'duration' ? { ...token, message } : token));
  }

  // ----------------------------------------------------------- absences ----

  private makeAbsence(span: Span | null, people: readonly string[], typedTitle: string): AbsenceDraft {
    const reason: AbsenceReason = this.absenceReason ?? 'absent';
    const [first, last, halfDay] = this.absenceDays(span);
    if (this.startMinute !== null || this.endMinute !== null) {
      this.warn('Les heures ne comptent pas pour une absence : seuls les jours sont retenus');
    }
    const part: DayPart = this.part ?? (halfDay ? 'morning' : 'day');
    if (part === 'afternoon' && first === last && this.periods(first).length <= 1) {
      this.warn(`Pas d'après-midi travaillé le ${DAY_NAMES[weekday(first)]}`);
    }
    if (!this.calendar.daysBetween(first, last).length) {
      if (first === last) {
        this.warn(`${this.dayLabel(first)} n'est pas travaillé${this.closedReason(first)}`);
      } else {
        this.warn(`Aucun jour travaillé du ${this.dayLabel(first)} au ${this.dayLabel(last)}`);
      }
    }
    this.warnIfPast(first);
    const note = [typedTitle, ...this.notes].filter(Boolean).join('\n');
    return { resourceIds: [...people], reason, firstDay: first, lastDay: last, part, note };
  }

  /** First and last day, and whether a typed duration asks for a half day. */
  private absenceDays(span: Span | null): [number, number, boolean] {
    const context = this.context;
    let first: number;
    let last: number;
    if (span !== null) {
      ({ first, last } = span);
    } else if (context.cellStart != null) {
      first = instantDay(context.cellStart);
      last = context.cellEnd != null ? instantDay(context.cellEnd - 1) : first;
    } else {
      first = context.today;
      last = first;
    }
    const duration = this.durationMinutes();
    if (duration && (span === null || !span.isRange)) return this.countedDays(first, duration);
    if (duration) this.ignoreDuration();
    return [first, Math.max(first, last), false];
  }

  /** `5j` of absence: five working days, whatever their hours; `½j` a half day. */
  private countedDays(start: number, minutes: number): [number, number, boolean] {
    let first = start;
    if (!this.calendar.isWorkday(first)) {
      [first] = this.workedSpan({ first, last: first, isRange: false, weekReference: false });
    }
    if (minutes <= HALF_DAY_MINUTES) {
      if (minutes < HALF_DAY_MINUTES) this.warn('Absence comptée en demi-journée');
      return [first, first, true];
    }
    const count = Math.ceil(minutes / DAY_MINUTES);
    if (count * DAY_MINUTES !== minutes) this.warn(`Absence comptée en jours entiers : ${count} j`);
    let last = first;
    for (let step = 0; step < count - 1; step += 1) last = this.calendar.nextWorkday(last);
    return [first, last, false];
  }

  // -------------------------------------------------------- description ----

  private leftover(): string {
    const pieces: string[] = [];
    let cursor = 0;
    const sorted = [...this.spans].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    for (const [begin, finish] of sorted) {
      if (begin > cursor) pieces.push(this.text.slice(cursor, begin));
      cursor = Math.max(cursor, finish);
    }
    pieces.push(this.text.slice(cursor));
    return pieces.join(' ');
  }

  private dayLabel(day: number): string {
    const { year, month, date } = dateParts(day);
    const suffix = year === dateParts(this.context.today).year ? '' : `.${year}`;
    const pad = (part: number) => String(part).padStart(2, '0');
    return `${DAY_ABBREVIATIONS[weekday(day)]} ${pad(date)}.${pad(month)}${suffix}`;
  }

  private instantLabel(value: Instant): string {
    return `${this.dayLabel(instantDay(value))} ${formatHhmm(minuteOfDay(value))}`;
  }

  private describe(entry: ParsedEntry): string {
    if (entry.absence !== null) return this.describeAbsence(entry.absence);
    if (entry.destination === 'invalid') return this.text.trim() ? 'Saisie incomplète' : 'Saisie vide';
    return [...this.describeWhat(entry), ...this.describeWhen(entry)].join(' · ');
  }

  private describeWhat(entry: ParsedEntry): string[] {
    const parts: string[] = [];
    if (entry.projectNumber) {
      const client = entry.project !== null ? (entry.project.client ?? '') : '';
      parts.push(`${entry.projectNumber} ${client}`.trim());
    }
    if (entry.title) parts.push(capitalised(entry.title));
    const labels = entry.resourceIds.map((id) => this.directory.label(id)).join(', ');
    if (entry.start !== null) parts.push(labels || 'Non affecté');
    else if (labels) parts.push(labels);
    return parts;
  }

  private describeWhen(entry: ParsedEntry): string[] {
    let parts: string[];
    if (entry.offHours && entry.start !== null && entry.end !== null) {
      const moments = `${this.instantLabel(entry.start)} → ${this.instantLabel(entry.end)}`;
      parts = [`${MOON} nuit ${moments}`, elapsedText(entry.end - entry.start)];
    } else if (entry.start !== null && entry.end !== null) {
      // The length its bar will show (`formatSpan`), not the days as typed.
      parts = [
        `${this.instantLabel(entry.start)} → ${this.instantLabel(entry.end)}`,
        formatSpan(this.calendar, entry.start, entry.end),
      ];
    } else {
      parts = ['À planifier'];
      const typed = this.typedDuration();
      if (typed) parts.push(typed);
    }
    if (entry.deadline !== null) parts.push(`avant le ${this.dayLabel(entry.deadline)}`);
    if (entry.provisional) parts.push('provisoire');
    if (entry.urgent) parts.push('urgent');
    return parts;
  }

  /** Duration of a card as typed: `2 j`, `4 h 30`, `1 j 4 h`. */
  private typedDuration(): string {
    const pieces: string[] = [];
    if (this.halfDays) pieces.push(formatDays(this.halfDays / 2));
    const minutes = this.durations.reduce((total, value) => total + value, 0);
    if (minutes) pieces.push(formatDuration(minutes));
    return pieces.join(' ');
  }

  private describeAbsence(absence: AbsenceDraft): string {
    const people = absence.resourceIds.map((id) => this.directory.label(id)).join(', ');
    let days = this.dayLabel(absence.firstDay);
    if (absence.lastDay !== absence.firstDay) days = `${days} → ${this.dayLabel(absence.lastDay)}`;
    if (absence.part !== 'day') days = `${days} ${PART_LABELS[absence.part]}`;
    const label = ABSENCE_LABELS[absence.reason] ?? absence.reason;
    return [label, people || 'qui ?', days, ...splitLines(absence.note)].join(' · ');
  }
}

function unreadableEntry(text: string): ParsedEntry {
  return {
    projectNumber: '',
    project: null,
    title: '',
    taskType: DEFAULT_TASK_TYPE,
    resourceIds: [],
    start: null,
    end: null,
    estimateMinutes: 0,
    provisional: false,
    urgent: false,
    deadline: null,
    notes: '',
    absence: null,
    destination: 'invalid',
    tokens: [],
    warnings: [UNREADABLE_WARNING],
    interpretation: text.trim() ? 'Saisie incomplète' : 'Saisie vide',
    estimateDays: 0,
    ignoredDuration: false,
    offHours: false,
  };
}

/** Read one line of quick entry; never throws on user text. */
export function parseEntry(text: string, context: EntryContext): ParsedEntry {
  try {
    return new Parser(text, context).parse();
  } catch (error) {
    // A calendar without any working day ahead.
    if (error instanceof ValueError) return unreadableEntry(text);
    throw error;
  }
}
