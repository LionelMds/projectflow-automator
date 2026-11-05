"""Planning: row height, column zoom, sideways scrolling, task names, older dossiers."""

from __future__ import annotations

from dataclasses import replace
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
from projectflow.core.planning.quick_entry import ProjectInfo
from projectflow.ui.planning.grid import EDGE_PUSH, WHEEL_NOTCH
from projectflow.ui.planning.grid_model import MAX_LANE_HEIGHT, MAX_ZOOM, fitted_day_width
from projectflow.ui.planning.planning_tab import PlanningTab
from projectflow.ui.planning_controller import PlanningHooks

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
    assert (tab.row_height, tab.zoom) == (22, 1.0)
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
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []

    async def find_project(self, number: str) -> ProjectInfo | None:
        self.asked.append(number)
        if number == "2019-3056":
            return ProjectInfo(number=number, client="Efaflex", designation="Park moto")
        return None

    def build(self) -> PlanningHooks:
        return replace(super().build(), find_project=self.find_project)


@pytest.mark.asyncio
async def test_an_older_dossier_is_found_from_its_fiche(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    old = task("t-old", "2019-3056", "", at(WEEK, 7), at(WEEK, 10), ["psi"])
    await seed_store(store, [*seed_documents(), old])
    hooks = OldHooks()
    controller, tab, _ = make_controller(qtbot, make_config(store), hooks=hooks)
    await started(controller)
    await wait_for(lambda: "2019-3056" in controller.projects, poke=controller.refresh_now)
    controller.refresh_now()
    bars = tab.grid.model.bars_by_task["t-old"]
    assert (bars[0].title, bars[0].client) == ("Park moto", "Efaflex")
    assert hooks.asked.count("2019-3056") == 1
    # Dossiers of the years read from the répertoire are never looked up one by one.
    assert not any(number.startswith(("2026-", "2025-", "2024-")) for number in hooks.asked)
    # Only what is drawn is filled in: the shared task keeps its own details.
    assert controller.service.snapshot.tasks["t-old"].project_cache.designation == ""
    await controller.aclose()
