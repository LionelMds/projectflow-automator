"""Fractional ranks: the shared manual order of the « À planifier » cards.

A rank is a lower-case base-36 string compared as plain text. A new rank always fits
strictly between two others, so moving one card rewrites that card only and two postes
reordering at the same time never renumber each other's cards. Ranks never end with
``0``, which keeps room before every rank. Appending or prepending grows a rank by one
character every 35 cards; repeated insertions at the same place by one character every
five.
"""

from __future__ import annotations

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE = len(DIGITS)
_TOP = _BASE - 1
_FIRST = 1
_MIDDLE = DIGITS[_BASE // 2]
_INDEX = {digit: index for index, digit in enumerate(DIGITS)}


def is_rank(text: str) -> bool:
    """Whether ``text`` is a rank this module can insert around."""
    return bool(text) and text[-1] != DIGITS[0] and all(char in _INDEX for char in text)


def rank_between(before: str | None, after: str | None) -> str:
    """Return a rank strictly greater than ``before`` and strictly lower than ``after``.

    None (or an empty ``before``) is an open end: ``rank_between(last, None)`` appends,
    ``rank_between(None, first)`` prepends. Raises ValueError for unusable bounds.
    """
    low = before or ""
    if low and not is_rank(low):
        raise ValueError(f"Rang invalide: {low!r}")
    if after is None:
        return _MIDDLE if not low else _increment(low)
    if not is_rank(after):
        raise ValueError(f"Rang invalide: {after!r}")
    if not low:
        return _decrement(after)
    if low >= after:
        raise ValueError(f"Rangs dans le desordre: {low!r} >= {after!r}")
    return _midpoint(low, after)


def initial_ranks(count: int) -> tuple[str, ...]:
    """``count`` increasing ranks of equal length spread evenly, for a first ordering."""
    if count <= 0:
        return ()
    width = 1
    while _BASE**width <= count:
        width += 1
    step = _BASE**width // (count + 1)
    return tuple(
        _digits(step * (position + 1), width).rstrip(DIGITS[0]) for position in range(count)
    )


def _digits(value: int, width: int) -> str:
    chars: list[str] = []
    for _ in range(width):
        value, digit = divmod(value, _BASE)
        chars.append(DIGITS[digit])
    return "".join(reversed(chars))


def _increment(low: str) -> str:
    """Next rank after ``low`` with the smallest step: the first digit below ``z`` + 1."""
    for position, char in enumerate(low):
        digit = _INDEX[char]
        if digit < _TOP:
            return low[:position] + DIGITS[digit + 1]
    return low + DIGITS[_FIRST]


def _decrement(high: str) -> str:
    """Previous rank before ``high`` with the smallest step, never empty."""
    for position, char in enumerate(high):
        digit = _INDEX[char]
        if digit > _FIRST:
            return high[:position] + DIGITS[digit - 1]
        if digit == _FIRST:
            if position < len(high) - 1:
                return high[: position + 1]
            return high[:position] + DIGITS[0] + DIGITS[_TOP]
    # Unreachable for a valid rank: its last digit is never 0.
    raise ValueError(f"Rang invalide: {high!r}")


def _midpoint(low: str, high: str) -> str:
    """Rank halfway between ``low`` < ``high`` (``low`` may be empty)."""
    shared = 0
    while shared < len(high) and (low[shared] if shared < len(low) else DIGITS[0]) == high[shared]:
        shared += 1
    prefix = high[:shared]
    low_rest = low[shared:]
    high_rest = high[shared:]
    low_digit = _INDEX[low_rest[0]] if low_rest else 0
    high_digit = _INDEX[high_rest[0]]
    if high_digit - low_digit > 1:
        return prefix + DIGITS[(low_digit + high_digit) // 2]
    if len(high_rest) > 1:
        return prefix + high_rest[0]
    return prefix + DIGITS[low_digit] + _midpoint_above(low_rest[1:])


def _midpoint_above(low: str) -> str:
    """Rank halfway between ``low`` and the end of the digit range."""
    prefix = ""
    rest = low
    while True:
        digit = _INDEX[rest[0]] if rest else 0
        if digit < _TOP:
            return prefix + DIGITS[(digit + _BASE) // 2]
        prefix += rest[0]
        rest = rest[1:]
