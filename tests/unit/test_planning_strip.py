from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pytest

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.strip import (
    DAY_MIN_WIDTH,
    MONTH_MIN_DAY_WIDTH,
    MONTHS_AHEAD,
    MONTHS_BACK,
    WEEK_MIN_DAY_WIDTH,
    WEEKS_AHEAD,
    WEEKS_BACK,
    Strip,
    base_day_width,
    lay_strip,
    page_key,
    pages_of,
    restrip,
)
from projectflow.core.planning.timescale import TimeScale
from projectflow.core.planning.walltime import wall_time

MON = date(2026, 11, 16)
WED = date(2026, 11, 18)
FRI = date(2026, 11, 20)
SAT = date(2026, 11, 21)
SUN = date(2026, 11, 22)
NEXT_MON = date(2026, 11, 23)
VIEWPORT = 1000.0


def week_strip(*, viewport: float = VIEWPORT, zoom: float = 1.0) -> Strip:
    return lay_strip("week", WED, viewport=viewport, zoom=zoom)


def day_strip(*, viewport: float = VIEWPORT, zoom: float = 1.0) -> Strip:
    return lay_strip("day", WED, viewport=viewport, zoom=zoom)


def month_strip(*, viewport: float = VIEWPORT, zoom: float = 1.0) -> Strip:
    return lay_strip("month", WED, viewport=viewport, zoom=zoom)


# ------------------------------------------------------------------ pages ----


def test_week_pages_run_one_year_back_and_two_ahead() -> None:
    pages = pages_of("week", WED)

    assert len(pages) == WEEKS_BACK + WEEKS_AHEAD + 1
    assert pages[0].first_day == MON - timedelta(weeks=WEEKS_BACK)
    assert pages[-1].first_day == MON + timedelta(weeks=WEEKS_AHEAD)
    assert pages[WEEKS_BACK].first_day == MON
    assert pages[WEEKS_BACK].last_day == SUN
    assert all(page.columns == 5 for page in pages)
    assert [page.index for page in pages] == list(range(len(pages)))


def test_week_pages_follow_each_other_without_gap() -> None:
    pages = pages_of("week", WED)

    assert all(
        later.first_day - earlier.first_day == timedelta(days=7)
        for earlier, later in pairwise(pages)
    )


def test_day_pages_are_the_shown_days_only() -> None:
    pages = pages_of("day", WED)

    assert len(pages) == 5 * (WEEKS_BACK + WEEKS_AHEAD + 1)
    assert all(page.first_day == page.last_day and page.columns == 1 for page in pages)
    assert all(page.first_day.weekday() < 5 for page in pages)
    around = [page.first_day for page in pages[5 * WEEKS_BACK + 3 : 5 * WEEKS_BACK + 7]]
    assert around == [date(2026, 11, 19), FRI, NEXT_MON, date(2026, 11, 24)]


def test_month_pages_count_their_working_days() -> None:
    pages = pages_of("month", WED)

    assert len(pages) == MONTHS_BACK + MONTHS_AHEAD + 1
    assert pages[0].first_day == date(2025, 11, 1)
    assert pages[-1].first_day == date(2028, 11, 1)
    november = pages[MONTHS_BACK]
    assert (november.first_day, november.last_day) == (date(2026, 11, 1), date(2026, 11, 30))
    assert november.columns == 21
    assert {page.columns for page in pages} <= {20, 21, 22, 23}


def test_pages_follow_the_weekdays_shown() -> None:
    pages = pages_of("week", WED, {0, 1, 2, 3})

    assert all(page.columns == 4 for page in pages)
    assert all(page.first_day.weekday() == 0 for page in pages_of("day", WED, {0})[:3])


def test_unknown_view_or_no_weekday_is_refused() -> None:
    with pytest.raises(ValueError, match="inconnue"):
        pages_of("year", WED)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Aucun jour"):
        pages_of("week", WED, ())


# ------------------------------------------------------ page of a date ----


@pytest.mark.parametrize(
    ("view", "day", "expected"),
    [
        ("day", WED, WED),
        ("day", SAT, NEXT_MON),
        ("day", SUN, NEXT_MON),
        ("week", WED, MON),
        ("week", SUN, MON),
        ("week", NEXT_MON, NEXT_MON),
        ("month", WED, date(2026, 11, 1)),
        ("month", date(2026, 11, 1), date(2026, 11, 1)),
        ("month", date(2026, 10, 31), date(2026, 10, 1)),
    ],
)
def test_page_key(view: str, day: date, expected: date) -> None:
    assert page_key(view, day) == expected  # type: ignore[arg-type]


def test_index_of_finds_the_page_of_any_date() -> None:
    strip = week_strip()

    assert strip.index_of(WED) == WEEKS_BACK
    assert strip.index_of(MON) == WEEKS_BACK
    assert strip.index_of(SUN) == WEEKS_BACK
    assert strip.index_of(NEXT_MON) == WEEKS_BACK + 1
    assert strip.index_of(MON - timedelta(weeks=WEEKS_BACK)) == 0


def test_a_weekend_day_shows_the_following_monday_in_the_day_view() -> None:
    strip = day_strip()
    monday = strip.index_of(NEXT_MON)

    assert monday is not None
    assert strip.index_of(SAT) == monday
    assert strip.index_of(SUN) == monday
    assert strip.index_of(FRI) == monday - 1


def test_a_month_beginning_on_a_sunday_is_found_and_named_by_a_shown_day() -> None:
    # 1 November 2026 is a Sunday: the page is November, named by Monday the 2nd.
    strip = month_strip()
    index = strip.index_of(date(2026, 11, 1))

    assert index == MONTHS_BACK
    assert strip.anchor_of(index) == date(2026, 11, 2)
    assert strip.index_of(strip.anchor_of(index)) == index
    # 1 August 2026 is a Saturday: the same goes for it.
    august = strip.index_of(date(2026, 8, 1))
    assert august is not None
    assert strip.anchor_of(august) == date(2026, 8, 3)
    assert strip.index_of(strip.anchor_of(august)) == august


def test_a_date_beyond_the_strip_has_no_page() -> None:
    strip = week_strip()

    assert strip.index_of(MON + timedelta(weeks=WEEKS_AHEAD + 1)) is None
    assert strip.index_of(MON - timedelta(weeks=WEEKS_BACK + 1)) is None
    # Laid out again around that date, the strip holds it in its middle.
    far = MON + timedelta(weeks=WEEKS_AHEAD + 30)
    again = lay_strip("week", far, viewport=VIEWPORT)
    assert again.index_of(far) == WEEKS_BACK
    assert again.origin == far


def test_the_origin_is_the_first_day_of_its_page() -> None:
    assert week_strip().origin == MON
    assert month_strip().origin == date(2026, 11, 1)
    assert lay_strip("day", SAT, viewport=VIEWPORT).origin == NEXT_MON


# ---------------------------------------------------------------- widths ----


def test_a_week_and_a_day_are_as_wide_as_the_viewport() -> None:
    week = week_strip()
    day = day_strip()

    assert week.day_width == pytest.approx(VIEWPORT / 5)
    assert week.width_of(WEEKS_BACK) == pytest.approx(VIEWPORT)
    assert day.day_width == pytest.approx(VIEWPORT)
    assert week.fits
    assert day.fits


def test_the_widest_month_fits_the_viewport() -> None:
    strip = month_strip()

    assert strip.day_width == pytest.approx(VIEWPORT / 23)
    assert strip.widest == pytest.approx(VIEWPORT)
    assert strip.fits
    assert strip.width_of(MONTHS_BACK) == pytest.approx(21 * VIEWPORT / 23)


def test_narrow_viewports_keep_a_least_width_and_scroll_freely() -> None:
    assert base_day_width("week", 300.0) == WEEK_MIN_DAY_WIDTH
    assert base_day_width("day", 300.0) == DAY_MIN_WIDTH
    assert base_day_width("month", 300.0) == MONTH_MIN_DAY_WIDTH
    assert not lay_strip("week", WED, viewport=300.0).fits
    assert not lay_strip("day", WED, viewport=300.0).fits


def test_a_longer_day_asks_for_a_wider_day_page() -> None:
    assert base_day_width("day", 300.0, day_minutes=1440) == pytest.approx(1440.0)
    strip = lay_strip("day", WED, viewport=300.0, day_minutes=720)
    assert strip.day_width == pytest.approx(720.0)
    assert restrip(strip, zoom=2.0).day_width == pytest.approx(1440.0)


def test_zoom_widens_every_page_and_frees_the_scroll() -> None:
    strip = week_strip(zoom=1.5)

    assert strip.day_width == pytest.approx(300.0)
    assert strip.width_of(0) == pytest.approx(1500.0)
    assert strip.total == pytest.approx(1500.0 * len(strip.pages))
    assert not strip.fits
    assert strip.rests() == ()


def test_lefts_are_the_running_sum_of_the_page_widths() -> None:
    strip = month_strip()

    assert strip.lefts[0] == 0.0
    assert len(strip.lefts) == len(strip.pages) + 1
    for index, page in enumerate(strip.pages):
        assert strip.lefts[index + 1] - strip.lefts[index] == pytest.approx(
            page.columns * strip.day_width
        )
    assert strip.total == strip.lefts[-1]
    assert strip.max_scroll == pytest.approx(strip.total - VIEWPORT)


# -------------------------------------------------- page under a position ----


def test_page_at_gives_the_page_holding_a_position() -> None:
    strip = week_strip()

    assert strip.page_at(0.0) == 0
    assert strip.page_at(999.9) == 0
    assert strip.page_at(1000.0) == 1
    assert strip.page_at(52_400.0) == 52
    assert strip.page_at(-50.0) == 0
    assert strip.page_at(strip.total + 50.0) == len(strip.pages) - 1


def test_page_at_follows_uneven_month_pages() -> None:
    strip = month_strip()

    for index in (0, 5, MONTHS_BACK, len(strip.pages) - 1):
        left = strip.left_of(index)
        assert strip.page_at(left) == index
        assert strip.page_at(left + strip.width_of(index) - 0.01) == index


def test_the_page_named_is_the_one_under_the_middle_of_the_viewport() -> None:
    strip = week_strip()
    rest = strip.rest(WEEKS_BACK)

    assert strip.middle_page(rest) == WEEKS_BACK
    assert strip.middle_page(rest + 499) == WEEKS_BACK
    assert strip.middle_page(rest + 500) == WEEKS_BACK + 1
    assert strip.middle_page(rest - 501) == WEEKS_BACK - 1


def test_the_pages_drawn_are_the_page_and_its_neighbours() -> None:
    strip = week_strip()

    assert list(strip.drawn(10)) == [9, 10, 11]
    assert list(strip.drawn(0)) == [0, 1]
    last = len(strip.pages) - 1
    assert list(strip.drawn(last)) == [last - 1, last]


# -------------------------------------------------------- rest positions ----


def test_rests_are_the_left_edges_of_the_pages() -> None:
    strip = week_strip()
    rests = strip.rests()

    assert len(rests) == len(strip.pages)
    assert rests[:3] == (0, 1000, 2000)
    assert strip.rest(WEEKS_BACK) == WEEKS_BACK * 1000
    assert all(isinstance(rest, int) for rest in rests)


def test_the_last_rest_never_scrolls_past_the_end() -> None:
    strip = month_strip()
    last = len(strip.pages) - 1

    assert strip.rest(last) == round(strip.max_scroll)
    assert strip.rest(last + 5) == strip.rest(last)
    assert strip.rest(-3) == 0


def test_nearest_rest_is_the_page_under_the_middle() -> None:
    strip = week_strip()

    assert strip.nearest_rest(10_200.0) == 10_000
    assert strip.nearest_rest(10_499.0) == 10_000
    assert strip.nearest_rest(10_500.0) == 11_000
    assert strip.nearest_rest(-80.0) == 0


def test_a_gesture_goes_one_page_at_most() -> None:
    strip = week_strip()
    start = 20
    left = strip.left_of(start)

    assert strip.gesture_rest(start, left + 50) == start
    assert strip.gesture_rest(start, left - 50) == start
    assert strip.gesture_rest(start, left + 130) == start + 1
    assert strip.gesture_rest(start, left - 130) == start - 1
    assert strip.gesture_rest(start, left + 4_000) == start + 1
    assert strip.gesture_rest(start, left - 4_000) == start - 1
    assert strip.gesture_rest(0, -500.0) == 0
    last = len(strip.pages) - 1
    assert strip.gesture_rest(last, strip.left_of(last) + 900) == last


def test_a_month_dezoomed_until_it_fits_rests_month_by_month() -> None:
    wide = month_strip(zoom=2.0)
    fitted = month_strip(zoom=1.0)

    assert not wide.fits
    assert wide.rests() == ()
    assert fitted.fits
    assert fitted.rests()[MONTHS_BACK] == round(fitted.left_of(MONTHS_BACK))


# ------------------------------------------------- keeping a place ----


def test_locate_and_position_are_each_other_s_reverse() -> None:
    strip = month_strip(zoom=1.7)

    for x in (0.0, 123.4, strip.left_of(7) + 10, strip.total - 5):
        index, share = strip.locate(x)
        assert strip.position(index, share) == pytest.approx(x)


def test_a_zoom_keeps_the_instant_under_the_pointer() -> None:
    before = week_strip()
    scroll = float(before.rest(WEEKS_BACK))
    focus = 600.0
    after = restrip(before, zoom=2.0)

    moved = after.moved_from(before, scroll, focus)

    assert after.locate(moved + focus) == pytest.approx(before.locate(scroll + focus))
    # Wednesday noon was at 3/5 of the week: it still is, under the same viewport x.
    assert moved == pytest.approx(WEEKS_BACK * 2000.0 + 0.6 * 2000.0 - focus)


def test_a_new_viewport_width_keeps_the_page_on_its_left_edge() -> None:
    before = week_strip()
    after = restrip(before, viewport=1400.0)

    assert after.moved_from(before, float(before.rest(60))) == pytest.approx(after.rest(60))


def test_a_place_on_another_strip_is_not_carried_over() -> None:
    week = week_strip()
    month = month_strip()

    assert month.moved_from(week, 80_000.0) == pytest.approx(month.clamp(80_000.0))
    assert month.moved_from(week, 10.0**9) == pytest.approx(month.max_scroll)


def test_restrip_keeps_the_pages() -> None:
    strip = day_strip()
    wider = restrip(strip, viewport=1600.0, zoom=1.25)

    assert wider.pages is strip.pages
    assert wider.scope == strip.scope
    assert wider.day_width == pytest.approx(2000.0)
    assert not wider.fits


# ------------------------------------------------- the axis on the strip ----


def axis(**options: object) -> TimeScale:
    return TimeScale(WorkCalendar(), MON, FRI, 200.0, **options)  # type: ignore[arg-type]


def test_an_axis_begins_at_its_place_on_the_strip() -> None:
    plain = axis()
    placed = axis(origin=52_000.0)

    assert (placed.left, placed.right, placed.width) == (52_000.0, 53_000.0, 1_000.0)
    assert plain.left == 0.0
    assert placed.columns[2].x == pytest.approx(52_400.0)
    noon = wall_time(WED, 11, 30)
    assert placed.x_of(noon) == pytest.approx(plain.x_of(noon) + 52_000.0)
    assert placed.time_at(52_500.0) == plain.time_at(500.0)
    assert placed.column_at(52_450.0).day == WED
    assert [column.day for column in placed.visible_columns(52_250.0, 52_450.0)] == [
        date(2026, 11, 17),
        WED,
    ]
    # Left of the drawn pages is the first drawn instant, right of them the last one.
    assert placed.time_at(10.0) == placed.start
    assert placed.time_at(99_000.0) == placed.end


def test_a_day_may_draw_its_24_hours_in_the_width_of_any_day() -> None:
    scale = axis(origin=1_000.0, windows={WED: (0, 1440)})

    assert scale.window_of(WED) == (0, 1440)
    assert scale.window_of(MON) == (390, 990)
    assert scale.full_day_on(WED)
    assert not scale.full_day_on(MON)
    assert not scale.full_day
    assert scale.own_windows == {WED: (0, 1440)}
    assert {column.width for column in scale.columns} == {200.0}
    assert scale.x_of(wall_time(WED, 0, 0)) == pytest.approx(1_400.0)
    assert scale.x_of(wall_time(WED, 12, 0)) == pytest.approx(1_500.0)
    assert scale.x_of(wall_time(WED, 24, 0)) == pytest.approx(1_600.0)
    assert scale.x_of(wall_time(MON, 11, 30)) == pytest.approx(1_100.0)
    assert scale.time_at(1_500.0) == wall_time(WED, 12, 0)
    assert scale.time_at(1_450.0) == wall_time(WED, 6, 0)
    assert scale.time_at(1_100.0) == wall_time(MON, 11, 30)


def test_the_line_between_two_days_reads_as_a_morning_or_an_evening() -> None:
    scale = axis(windows={WED: (0, 1440)})
    tuesday = date(2026, 11, 17)

    # Tuesday | Wednesday: the next morning for a start, the evening before for an end.
    assert scale.time_at(400.0) == wall_time(WED, 0, 0)
    assert scale.time_at(400.0, end=True) == wall_time(tuesday, 16, 30)
    # Wednesday | Thursday.
    assert scale.time_at(600.0) == wall_time(date(2026, 11, 19), 6, 30)
    assert scale.time_at(600.0, end=True) == wall_time(WED, 24, 0)


def test_a_window_like_the_others_changes_nothing() -> None:
    scale = axis(windows={WED: (390, 990)})

    assert scale.own_windows == {}
    assert scale.x_of(wall_time(WED, 11, 30)) == axis().x_of(wall_time(WED, 11, 30))
    with pytest.raises(ValueError, match="invalide"):
        axis(windows={WED: (600, 300)})
