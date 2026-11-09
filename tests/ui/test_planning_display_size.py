"""Planning: row height, column zoom, sideways scrolling, task names, older dossiers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_planning_controller import (  # type: ignore[import-not-found]
    WEEK,
    Hooks,
    at,
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_documents,
    seed_store,
    started,
    task,
    wait_for,
)

from projectflow.core.planning.models import task_label
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.repertoire_service import RepertoireRow, RepertoireSnapshot
from projectflow.ui.planning.grid import EDGE_PUSH, WHEEL_NOTCH
from projectflow.ui.planning.grid_items import text_growth
from projectflow.ui.planning.grid_model import (
    MAX_LANE_HEIGHT,
    MAX_ZOOM,
    MIN_LANE_HEIGHT,
    fitted_day_width,
)
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.planning_controller import project_from_row

OLD_YEAR = 2019

# ---------------------------------------------------------------- pure parts ----


def test_task_label_is_number_dash_designation() -> None:
    assert task_label("2026-5271", "Local grillagé") == "2026-5271 - Local grillagé"
    assert task_label("2026-5271", " ") == "2026-5271"
    assert task_label("", "Rangement atelier") == "Rangement atelier"
    typed = task("t", "2026-118", "Pose", None, None, [])
    assert typed.label == "2026-118 - Pose"
    dossier = typed.model_copy(
        update={
            "title": "",
            "project_cache": typed.project_cache.model_copy(update={"designation": "Garde-corps"}),
        }
    )
    assert dossier.designation == "Garde-corps"
    assert dossier.label == "2026-118 - Garde-corps"


def test_zoom_widens_the_columns_past_the_viewport() -> None:
    fit = fitted_day_width("week", 1000.0, 5, 600)
    assert fitted_day_width("week", 1000.0, 5, 600, zoom=2.0) == pytest.approx(2 * fit)
    assert fitted_day_width("week", 1000.0, 5, 600, zoom=0.2) == pytest.approx(fit)
    assert fitted_day_width("month", 1000.0, 22, 600, zoom=99) == pytest.approx(
        max((1000.0 - 0.5) / 22, 22.0) * MAX_ZOOM
    )


def test_the_text_of_the_bars_grows_with_the_rows() -> None:
    assert [text_growth(height) for height in (22, 38, 45, 46, 54, 70, MAX_LANE_HEIGHT)] == [
        0,
        0,
        0,
        1,
        2,
        4,
        4,
    ]


def test_old_settings_get_sizes_kept_per_view() -> None:
    config = PlanningPosteConfig(
        row_heights={"week": 60, "year": 50, "day": 999}, column_zooms={"day": 2.5, "week": 1}
    )
    assert config.row_heights == {"week": 60, "day": MAX_LANE_HEIGHT}
    assert config.column_zooms == {"day": 2.5}


# ----------------------------------------------------------------------- tab ----


def test_row_height_and_zoom_are_kept_per_view(qtbot: Any) -> None:
    tab = PlanningTab()
    qtbot.addWidget(tab)
    changed: list[bool] = []
    tab.display_size_changed.connect(lambda: changed.append(True))
    assert tab.row_height == 38
    tab.set_row_height(60)
    tab.set_zoom(2.0)
    assert (tab.row_height, tab.zoom) == (60, 2.0)
    assert tab.row_height_slider.value() == 60
    assert tab.zoom_value.text() == "200 %"
    tab.set_view("month")
    assert (tab.row_height, tab.zoom) == (38, 1.0)  # The month follows the density too.
    tab.set_view("week")
    assert (tab.row_height, tab.zoom) == (60, 2.0)
    assert tab.row_heights() == {"week": 60}
    assert tab.zooms() == {"week": 2.0}
    assert len(changed) == 2
    tab.fit_columns()
    assert tab.zoom == 1.0
    tab.set_density("large")  # A density sets the rows of the day and the week again.
    assert tab.row_height == 54
    tab.apply_preferences(row_heights={"week": 30}, zooms={"week": 3.0, "day": 0.5})
    assert (tab.row_height, tab.zoom) == (30, 3.0)


def test_the_month_follows_the_density(qtbot: Any) -> None:
    tab = PlanningTab()
    qtbot.addWidget(tab)
    tab.set_view("month")
    heights: dict[str, int] = {}
    for density in ("compact", "normal", "large"):
        tab.set_density(density)
        heights[density] = tab.row_height
    assert heights == {"compact": 22, "normal": 38, "large": 54}
    tab.set_row_height(70)
    tab.set_density("large")  # A density sets the month again, past a height chosen.
    assert tab.row_height == 54
    assert tab.row_heights() == {}


def test_the_lines_buttons_step_the_rows_of_the_view(qtbot: Any) -> None:
    tab = PlanningTab()
    qtbot.addWidget(tab)
    tab.set_view("month")
    tab.set_density("compact")
    tab.taller_rows_button.click()
    assert tab.row_height == 30
    tab.taller_rows_button.click()
    tab.taller_rows_button.click()
    assert tab.row_height == 46
    assert "46 px" in tab.taller_rows_button.toolTip()
    assert tab.row_heights() == {"month": 46}
    tab.set_view("week")
    assert tab.row_height == 22  # Kept per view: the week keeps its density.
    tab.set_row_height(MAX_LANE_HEIGHT)
    assert not tab.taller_rows_button.isEnabled()
    tab.lower_rows_button.click()
    assert tab.row_height == MAX_LANE_HEIGHT - 8
    assert tab.taller_rows_button.isEnabled()
    tab.set_row_height(MIN_LANE_HEIGHT)
    assert not tab.lower_rows_button.isEnabled()


def test_the_sideways_wheel_past_the_end_shows_the_next_period(qtbot: Any) -> None:
    tab = PlanningTab()
    qtbot.addWidget(tab)
    steps: list[int] = []
    tab.grid.period_step_requested.connect(steps.append)
    for _ in range(EDGE_PUSH // WHEEL_NOTCH - 1):
        tab.grid.scroll_sideways(-WHEEL_NOTCH)  # towards the future, no room to scroll
    assert steps == []
    tab.grid.scroll_sideways(-WHEEL_NOTCH)
    assert steps == [1]
    tab.grid.scroll_sideways(WHEEL_NOTCH)  # the other way starts again from zero
    tab.grid.scroll_sideways(WHEEL_NOTCH)
    tab.grid.scroll_sideways(WHEEL_NOTCH)
    assert steps == [1, -1]


@pytest.mark.asyncio
async def test_zoomed_week_scrolls_and_rows_grow(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    width = tab.grid.model.timescale.width
    tab.set_zoom(3.0)
    controller.refresh_now()
    assert tab.grid.model.timescale.width == pytest.approx(3 * width, rel=0.05)
    assert tab.grid.view.horizontalScrollBar().maximum() > 0
    tab.set_row_height(70)
    controller.refresh_now()
    assert tab.grid.model.density == 70
    assert controller.poste_config.row_heights == {"week": 70}
    assert controller.poste_config.column_zooms == {"week": 3.0}
    await controller.aclose()


# ------------------------------------------------------------- older dossiers ----


class OldHooks(Hooks):
    """A répertoire with an older sheet laid out as before 2022 (designation in C)."""

    def __init__(self) -> None:
        super().__init__()
        self.years: list[int] = []

    async def read_repertoire(self, year: int) -> RepertoireSnapshot:
        self.years.append(year)
        if year == OLD_YEAR:
            row = RepertoireRow(
                row_index=3,
                values=("2019-3056", "43479", "EFAFLEX / Rolex - park moto", "", "1250"),
            )
            return RepertoireSnapshot(year=year, rows=(row,), next_available=None)
        return await super().read_repertoire(year)


@pytest.mark.asyncio
async def test_an_older_dossier_is_found_in_its_sheet(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    old = task("t-old", "2019-3056", "", at(WEEK, 7), at(WEEK, 10), ["psi"])
    older = task("t-old-2", "2019-3056", "Pose", at(WEEK, 13), at(WEEK, 15), ["psi"])
    await seed_store(store, [*seed_documents(), old, older])
    hooks = OldHooks()
    controller, tab, _ = make_controller(qtbot, make_config(store), hooks=hooks)
    await started(controller)
    await wait_for(lambda: "2019-3056" in controller.projects, poke=controller.refresh_now)
    controller.refresh_now()
    bars = tab.grid.model.bars_by_task["t-old"]
    assert (bars[0].title, bars[0].client) == ("EFAFLEX / Rolex - park moto", "")
    assert hooks.years.count(OLD_YEAR) == 1  # one sheet read once for all its dossiers
    assert sorted(set(hooks.years)) == [2019, 2024, 2025, 2026]
    # Only what is drawn is filled in: the shared task keeps its own details.
    assert controller.service.snapshot.tasks["t-old"].project_cache.designation == ""
    await controller.aclose()


def test_sheets_before_2022_hold_the_designation_in_column_c() -> None:
    values = ("2019-3056", "43479", "EFAFLEX / park moto", "non", "1250")
    old = project_from_row(values, 2019)
    assert old is not None
    assert (old.designation, old.client, old.contact) == ("EFAFLEX / park moto", "", "")
    recent = project_from_row(("2022-3821", "44574", "Mairie", "M. X", "Marquise"), 2022)
    assert recent is not None
    assert (recent.designation, recent.client) == ("Marquise", "Mairie")
    assert project_from_row(("2019-3057", "43479", "", "x", "y"), 2019) is None
