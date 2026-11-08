/**
 * Port of the rank tests of the desktop (`tests/unit/test_planning_commands.py`:
 * `test_rank_between_*`, `test_initial_ranks_*`) for `lib/planning/ranks.ts`.
 */

import { initialRanks, isRank, rankBetween } from '@/lib/planning/ranks';

/** Reproducible pseudo-random numbers in [0, 1) (mulberry32), like Python's `Random(118)`. */
function seeded(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/** Python's `randint`: an integer from `low` to `high` included. */
function randint(random: () => number, low: number, high: number): number {
  return low + Math.floor(random() * (high - low + 1));
}

/** Plain text order, as Python's `sorted` on strings. */
function sorted(ranks: readonly string[]): string[] {
  return [...ranks].sort();
}

describe('rankBetween', () => {
  it('handles open ends and the middle', () => {
    const first = rankBetween(null, null);
    const after = rankBetween(first, null);
    const before = rankBetween(null, first);
    const middle = rankBetween(before, first);
    expect(before < middle && middle < first && first < after).toBe(true);
    expect([first, after, before, middle].every(isRank)).toBe(true);
  });

  it('keeps the order under random insertions', () => {
    const random = seeded(118);
    const ranks: string[] = [];
    for (let i = 0; i < 3000; i += 1) {
      const position = randint(random, 0, ranks.length);
      const low = position ? ranks[position - 1] : null;
      const high = position < ranks.length ? ranks[position] : null;
      const rank = rankBetween(low, high);
      expect(isRank(rank)).toBe(true);
      if (low !== null) expect(low < rank).toBe(true);
      if (high !== null) expect(rank < high).toBe(true);
      ranks.splice(position, 0, rank);
    }
    expect(ranks).toEqual(sorted(ranks));
    expect(new Set(ranks).size).toBe(ranks.length);
  });

  it('keeps appends and prepends short', () => {
    const appended: string[] = [];
    const prepended: string[] = [];
    for (let i = 0; i < 500; i += 1) {
      appended.push(rankBetween(appended.length ? appended[appended.length - 1] : null, null));
      prepended.unshift(rankBetween(null, prepended.length ? prepended[0] : null));
    }
    expect(appended).toEqual(sorted(appended));
    expect(prepended).toEqual(sorted(prepended));
    expect(Math.max(...[...appended, ...prepended].map((rank) => rank.length))).toBeLessThanOrEqual(16);
  });

  it('rejects unusable bounds', () => {
    // Python raises ValueError, the port RangeError; the messages are the same.
    expect(() => rankBetween('k', 'c')).toThrow(RangeError);
    expect(() => rankBetween('k', 'c')).toThrow(/desordre/);
    expect(() => rankBetween('k', 'k')).toThrow(/desordre/);
    expect(() => rankBetween(null, '')).toThrow(/invalide/);
    expect(() => rankBetween('A', null)).toThrow(/invalide/);
    expect(() => rankBetween(null, 'i0')).toThrow(/invalide/);
  });

  it('handles the edge digits', () => {
    expect(rankBetween(null, '1i')).toBe('1');
    expect(rankBetween('1', '2i')).toBe('2');
    expect(rankBetween(null, '1')).toBe('0z');
    expect(rankBetween('zz', null)).toBe('zz1');
  });
});

describe('initialRanks', () => {
  it.each([0, 1, 2, 35, 36, 37, 500])('gives %i sorted, distinct and valid ranks', (count) => {
    const ranks = initialRanks(count);
    expect(ranks).toHaveLength(count);
    expect(ranks).toEqual(sorted(ranks));
    expect(new Set(ranks).size).toBe(count);
    expect(ranks.every(isRank)).toBe(true);
    if (count) expect(rankBetween(null, ranks[0]) < ranks[0]).toBe(true);
  });
});
