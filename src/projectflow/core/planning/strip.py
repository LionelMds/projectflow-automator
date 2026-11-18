"""The Planning grid as one strip of pages side by side: a day, a week or a month each.

The strip runs about one year back and two years ahead of the day it was laid out around.
Scrolling slides from a page to the next and comes to rest on the left edge of a page as
long as a page fits the viewport; zoomed in, a page is wider than the viewport and the
scroll runs freely across the pages. Only the page on screen and its two neighbours are
drawn; the others keep their place. Positions are pixels from the left end of the strip
at the strip's zoom. Nothing here imports Qt.
"""

from __future__ import annotations

import bisect
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

from projectflow.core.planning.calendar import DAYS_PER_WEEK
from projectflow.core.planning.timescale import (
    VIEW_KINDS,
    WEEKDAYS,
    ViewKind,
    month_range,
    shift_view,
    shown_day,
    week_start,
)

WEEKS_BACK = 52
WEEKS_AHEAD = 104
"""How far the day and week strips run from the week they are laid out around."""
MONTHS_BACK = 12
MONTHS_AHEAD = 24
DAY_MIN_WIDTH = 600.0
"""A day page is never narrower: 60 px an hour over the ten hours of a day."""
WEEK_MIN_DAY_WIDTH = 90.0
MONTH_MIN_DAY_WIDTH = 22.0
FIT_TOLERANCE = 1.0
"""Pixels a page may exceed the viewport by and still hold the scroll at rest."""
GESTURE_SHARE = 0.12
"""Share of a page a drag must travel to carry the strip to the next page."""
DAY_MINUTES = 600
"""Minutes drawn in a day (06:30 to 16:30) the minimum width of a day page is given for."""


@dataclass(frozen=True, slots=True)
class Page:
    """One period of the strip: its calendar days and how many of them have a column."""

    index: int
    first_day: date
    """The day itself, the Monday of the week, or the first day of the month."""
    last_day: date
    columns: int

    @property
    def key(self) -> date:
        return self.first_day


def _weekday_count(first: date, last: date, weekdays: Collection[int]) -> int:
    """Shown days from ``first`` to ``last`` included."""
    days = (last - first).days + 1
    weeks, rest = divmod(days, DAYS_PER_WEEK)
    count = weeks * len(set(weekdays))
    for offset in range(rest):
        if (first + timedelta(days=offset)).weekday() in weekdays:
            count += 1
    return count


def page_key(view: ViewKind, day: date, weekdays: Collection[int] = WEEKDAYS) -> date:
    """First day of the page showing ``day``.

    The day view shows the next shown day for a Saturday or a Sunday (Monday); the week of
    a weekend day is its own ISO week and its month is its own month.
    """
    if view == "day":
        return shown_day(day, weekdays)
    if view == "week":
        return week_start(day)
    if view == "month":
        return day.replace(day=1)
    raise ValueError(f"Vue de planning inconnue: {view!r}")


def pages_of(
    view: ViewKind, origin: date, weekdays: Collection[int] = WEEKDAYS
) -> tuple[Page, ...]:
    """Pages of the strip laid out around ``origin``, oldest first."""
    if view not in VIEW_KINDS:
        raise ValueError(f"Vue de planning inconnue: {view!r}")
    shown = frozenset(weekdays)
    if not shown:
        raise ValueError("Aucun jour de la semaine affiche.")
    pages: list[Page] = []
    if view == "month":
        first = shift_view(origin, "month", -MONTHS_BACK, shown)
        for _ in range(MONTHS_BACK + MONTHS_AHEAD + 1):
            start, end = month_range(first)
            pages.append(Page(len(pages), start, end, _weekday_count(start, end, shown)))
            first = end + timedelta(days=1)
        return tuple(pages)
    monday = week_start(origin) - timedelta(days=DAYS_PER_WEEK * WEEKS_BACK)
    for _ in range(WEEKS_BACK + WEEKS_AHEAD + 1):
        if view == "week":
            last = monday + timedelta(days=DAYS_PER_WEEK - 1)
            pages.append(Page(len(pages), monday, last, len(shown)))
        else:
            for offset in sorted(shown):
                day = monday + timedelta(days=offset)
                pages.append(Page(len(pages), day, day, 1))
        monday += timedelta(days=DAYS_PER_WEEK)
    return tuple(pages)


def base_day_width(
    view: ViewKind,
    viewport: float,
    *,
    week_columns: int = len(WEEKDAYS),
    widest_columns: int = 23,
    day_minutes: int = DAY_MINUTES,
) -> float:
    """Width of a day column before any zoom.

    A day or a week is as wide as the viewport, and the widest month fits it, down to a
    minimum under which the page becomes wider than the viewport.
    """
    if view == "day":
        return max(viewport, DAY_MIN_WIDTH * day_minutes / DAY_MINUTES)
    if view == "week":
        return max(viewport / max(week_columns, 1), WEEK_MIN_DAY_WIDTH)
    return max(viewport / max(widest_columns, 1), MONTH_MIN_DAY_WIDTH)


@dataclass(frozen=True, slots=True)
class Strip:
    """Pages and their positions for one view, viewport width and zoom."""

    view: ViewKind
    origin: date
    weekdays: frozenset[int]
    pages: tuple[Page, ...]
    viewport: float
    zoom: float
    day_width: float
    """Width of one day column at this zoom."""
    lefts: tuple[float, ...]
    """Left edge of every page, then the right end of the strip."""
    keys: tuple[int, ...]
    """Ordinal of every page's first day, for lookups."""
    day_minutes: int = DAY_MINUTES
    """Minutes drawn in a day, which the least width of a day page follows."""

    # Geometry ---------------------------------------------------------

    @property
    def total(self) -> float:
        return self.lefts[-1]

    @property
    def max_scroll(self) -> float:
        return max(self.total - self.viewport, 0.0)

    @property
    def widest(self) -> float:
        """Width of the widest page."""
        return max(page.columns for page in self.pages) * self.day_width

    @property
    def fits(self) -> bool:
        """Whether a page fits the viewport: the scroll then rests on page edges."""
        return self.widest <= self.viewport + FIT_TOLERANCE

    @property
    def scope(self) -> tuple[ViewKind, date]:
        """What a scroll position is counted in: it means nothing on another strip."""
        return self.view, self.origin

    def width_of(self, index: int) -> float:
        return self.lefts[index + 1] - self.lefts[index]

    def left_of(self, index: int) -> float:
        return self.lefts[index]

    # Pages ------------------------------------------------------------

    def index_of(self, day: date) -> int | None:
        """Page showing ``day``; None when the strip does not reach that far."""
        key = page_key(self.view, day, self.weekdays).toordinal()
        index = bisect.bisect_left(self.keys, key)
        if index < len(self.keys) and self.keys[index] == key:
            return index
        return None

    def page_at(self, x: float) -> int:
        """Page under the position ``x``, the first or last one past the ends."""
        index = bisect.bisect_right(self.lefts, x) - 1
        return min(max(index, 0), len(self.pages) - 1)

    def middle_page(self, scroll: float) -> int:
        """Page under the middle of the viewport scrolled to ``scroll``: the one named."""
        return self.page_at(scroll + self.viewport / 2)

    def anchor_of(self, index: int) -> date:
        """Day naming the page: its first shown day (a month may begin on a Sunday)."""
        return shown_day(self.pages[index].first_day, self.weekdays)

    def drawn(self, index: int) -> range:
        """Pages drawn around the page ``index``: itself and its two neighbours."""
        return range(max(index - 1, 0), min(index + 1, len(self.pages) - 1) + 1)

    def clamp_index(self, index: int) -> int:
        return min(max(index, 0), len(self.pages) - 1)

    # Rest positions ---------------------------------------------------

    def clamp(self, scroll: float) -> float:
        return min(max(scroll, 0.0), self.max_scroll)

    def rest(self, index: int) -> int:
        """Scroll position showing the page ``index`` from its left edge."""
        return round(self.clamp(self.lefts[self.clamp_index(index)]))

    def rests(self) -> tuple[int, ...]:
        """Every position the scroll rests on, empty while the scroll runs freely."""
        if not self.fits:
            return ()
        return tuple(self.rest(index) for index in range(len(self.pages)))

    def nearest_rest(self, scroll: float) -> int:
        """Rest position of the page under the middle of the viewport."""
        return self.rest(self.middle_page(scroll))

    def gesture_rest(self, start: int, scroll: float) -> int:
        """Page a drag released at ``scroll`` comes to rest on, from the page ``start``.

        A gesture never carries further than the next page, and goes there once it has
        travelled a small share of the page; short of it the strip comes back.
        """
        start = self.clamp_index(start)
        travel = scroll - self.lefts[start]
        if abs(travel) < GESTURE_SHARE * self.width_of(start):
            return start
        return self.clamp_index(start + (1 if travel > 0 else -1))

    # Keeping a place across layouts -----------------------------------

    def locate(self, x: float) -> tuple[int, float]:
        """Page under ``x`` and the share of its width left of ``x``."""
        index = self.page_at(x)
        width = self.width_of(index)
        share = (x - self.lefts[index]) / width if width > 0 else 0.0
        return index, share

    def position(self, index: int, share: float = 0.0) -> float:
        """Position of the ``share`` of the page ``index`` (the reverse of ``locate``)."""
        index = self.clamp_index(index)
        return self.lefts[index] + share * self.width_of(index)

    def moved_from(self, other: Strip, scroll: float, focus: float = 0.0) -> float:
        """Scroll keeping under the viewport x ``focus`` what ``other`` showed there.

        ``other`` is the same strip at another zoom or viewport width.
        """
        if other.scope != self.scope or len(other.pages) != len(self.pages):
            return self.clamp(scroll)
        index, share = other.locate(scroll + focus)
        return self.clamp(self.position(index, share) - focus)


def lay_strip(
    view: ViewKind,
    origin: date,
    *,
    viewport: float,
    zoom: float = 1.0,
    weekdays: Collection[int] = WEEKDAYS,
    day_minutes: int = DAY_MINUTES,
    pages: tuple[Page, ...] | None = None,
) -> Strip:
    """Lay the strip of ``view`` out around ``origin`` for a viewport width and a zoom.

    ``pages`` reuses the pages of a strip of the same view, origin and weekdays.
    """
    shown = frozenset(weekdays)
    key = page_key(view, origin, shown)
    laid = pages if pages is not None else pages_of(view, key, shown)
    widest = max(page.columns for page in laid)
    width = (
        base_day_width(
            view,
            max(viewport, 1.0),
            week_columns=len(shown),
            widest_columns=widest,
            day_minutes=day_minutes,
        )
        * zoom
    )
    lefts = [0.0]
    for page in laid:
        lefts.append(lefts[-1] + page.columns * width)
    return Strip(
        view=view,
        origin=key,
        weekdays=shown,
        pages=laid,
        viewport=float(viewport),
        zoom=float(zoom),
        day_width=width,
        lefts=tuple(lefts),
        keys=tuple(page.first_day.toordinal() for page in laid),
        day_minutes=day_minutes,
    )


def restrip(strip: Strip, *, viewport: float | None = None, zoom: float | None = None) -> Strip:
    """Lay the same pages out for another viewport width or zoom."""
    return lay_strip(
        strip.view,
        strip.origin,
        viewport=strip.viewport if viewport is None else viewport,
        zoom=strip.zoom if zoom is None else zoom,
        weekdays=strip.weekdays,
        day_minutes=strip.day_minutes,
        pages=strip.pages,
    )
