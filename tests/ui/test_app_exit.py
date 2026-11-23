"""The windows are destroyed before the application returns (see ``_release_windows``)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QApplication, QLabel, QWidget
from shiboken6 import isValid

from projectflow.app import _release_windows


def test_the_windows_are_destroyed_before_the_application_returns(qtbot: Any) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    window = QWidget()
    child = QLabel("Planning", window)
    popup = QWidget()
    window.show()

    _release_windows(app)

    assert not isValid(window)
    assert not isValid(child)
    assert not isValid(popup)
    assert not app.topLevelWidgets()
