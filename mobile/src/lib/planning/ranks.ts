/**
 * Fractional ranks: the shared manual order of the « À planifier » cards
 * (`core/planning/ranks.py`). A rank is a lower-case base-36 string compared as plain
 * text; a new rank always fits strictly between two others, and never ends with « 0 ».
 */

const DIGITS = '0123456789abcdefghijklmnopqrstuvwxyz';
const BASE = DIGITS.length;
const TOP = BASE - 1;
const FIRST = 1;
const MIDDLE = DIGITS[Math.floor(BASE / 2)];

function index(char: string): number {
  return DIGITS.indexOf(char);
}

export function isRank(text: string): boolean {
  return Boolean(text) && text[text.length - 1] !== DIGITS[0] && [...text].every((char) => index(char) >= 0);
}

/** A rank strictly between `before` and `after`; null (or empty `before`) is an open end. */
export function rankBetween(before: string | null | undefined, after: string | null | undefined): string {
  const low = before || '';
  if (low && !isRank(low)) throw new RangeError(`Rang invalide: ${low}`);
  if (after === null || after === undefined) return low ? increment(low) : MIDDLE;
  if (!isRank(after)) throw new RangeError(`Rang invalide: ${after}`);
  if (!low) return decrement(after);
  if (low >= after) throw new RangeError(`Rangs dans le desordre: ${low} >= ${after}`);
  return midpoint(low, after);
}

/** `count` increasing ranks of equal length spread evenly, for a first ordering. */
export function initialRanks(count: number): string[] {
  if (count <= 0) return [];
  let width = 1;
  while (BASE ** width <= count) width += 1;
  const step = Math.floor(BASE ** width / (count + 1));
  return Array.from({ length: count }, (_, position) => digits(step * (position + 1), width).replace(/0+$/, ''));
}

function digits(value: number, width: number): string {
  const chars: string[] = [];
  let rest = value;
  for (let i = 0; i < width; i += 1) {
    chars.push(DIGITS[rest % BASE]);
    rest = Math.floor(rest / BASE);
  }
  return chars.reverse().join('');
}

function increment(low: string): string {
  for (let position = 0; position < low.length; position += 1) {
    const digit = index(low[position]);
    if (digit < TOP) return low.slice(0, position) + DIGITS[digit + 1];
  }
  return low + DIGITS[FIRST];
}

function decrement(high: string): string {
  for (let position = 0; position < high.length; position += 1) {
    const digit = index(high[position]);
    if (digit > FIRST) return high.slice(0, position) + DIGITS[digit - 1];
    if (digit === FIRST) {
      if (position < high.length - 1) return high.slice(0, position + 1);
      return high.slice(0, position) + DIGITS[0] + DIGITS[TOP];
    }
  }
  throw new RangeError(`Rang invalide: ${high}`);
}

function midpoint(low: string, high: string): string {
  let shared = 0;
  while (shared < high.length && (shared < low.length ? low[shared] : DIGITS[0]) === high[shared]) shared += 1;
  const prefix = high.slice(0, shared);
  const lowRest = low.slice(shared);
  const highRest = high.slice(shared);
  const lowDigit = lowRest ? index(lowRest[0]) : 0;
  const highDigit = index(highRest[0]);
  if (highDigit - lowDigit > 1) return prefix + DIGITS[Math.floor((lowDigit + highDigit) / 2)];
  if (highRest.length > 1) return prefix + highRest[0];
  return prefix + DIGITS[lowDigit] + midpointAbove(lowRest.slice(1));
}

function midpointAbove(low: string): string {
  let prefix = '';
  let rest = low;
  for (;;) {
    const digit = rest ? index(rest[0]) : 0;
    if (digit < TOP) return prefix + DIGITS[Math.floor((digit + BASE) / 2)];
    prefix += rest[0];
    rest = rest.slice(1);
  }
}
