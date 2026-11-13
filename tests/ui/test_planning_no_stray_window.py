"""No stray little window at start (a widget of the detail once opened on its own)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from test_planning_controller import (  # type: ignore[import-not-found]
    data_dirs,  # noqa: F401 - fixture
    make_config,
    make_controller,
    seed_store,
    started,
)

from projectflow.ui.planning.detail import DetailPanel


def _stray_windows(*allowed: QWidget) -> list[str]:
    return [
        f"{type(widget).__name__} {getattr(widget, 'text', lambda: '')()!r}"
        for widget in QApplication.topLevelWidgets()
        if widget.isVisible()
        and widget not in allowed
        and widget.isWindow()
        and not widget.inherits("QMenu")
    ]


def test_the_detail_opens_no_window_of_its_own(qtbot: Any) -> None:
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.clear()  # what is shown before any task
    for widget in (panel.clear_deadline_button, panel.start_picker, panel.deadline_picker):
        assert not widget.isWindow()
    assert _stray_windows(panel) == []


@pytest.mark.asyncio
async def test_start_opens_no_other_window(qtbot: Any, tmp_path: Path) -> None:
    store = tmp_path / "store"
    await seed_store(store)
    controller, tab, _hooks = make_controller(qtbot, make_config(store))
    await started(controller)
    assert _stray_windows(tab) == []
    await controller.aclose()
