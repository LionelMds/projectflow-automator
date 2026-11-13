"""Rolls of the « Détail » panel: a day, an hour and minutes by the half hour."""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from projectflow.core.planning.walltime import wall_time
from projectflow.ui.planning.wheel_picker import (
    ROW_HEIGHT,
    VISIBLE_ROWS,
    DateTimeWheel,
    DateWheel,
    day_text,
    moment_text,
)

MONDAY = date(2026, 11, 16)
TODAY = date(2026, 11, 13)


def _wheel(qtbot: Any, *, width: int = 300) -> DateTimeWheel:
    wheel = DateTimeWheel()
    qtbot.addWidget(wheel)
    wheel.resize(width, wheel.height())
    wheel.set_moment(wall_time(MONDAY, 7, 0), today=TODAY)
    wheel.show()
    qtbot.waitExposed(wheel)
    return wheel


def _center(wheel: DateTimeWheel, column: int, row: int = 0) -> QPoint:
    """Middle of a column, ``row`` rows below the middle one (negative: above)."""
    rect = wheel.column_rect(column)
    return QPoint(round(rect.center().x()), round((VISIBLE_ROWS // 2 + row + 0.5) * ROW_HEIGHT))


def _turn(wheel: DateTimeWheel, point: QPoint, delta: int) -> None:
    event = QWheelEvent(
        QPointF(point),
        QPointF(wheel.mapToGlobal(point)),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,  # noqa: FBT003
    )
    QApplication.sendEvent(wheel, event)


def test_texts_of_a_day_and_a_moment() -> None:
    assert day_text(MONDAY) == "lun 16 nov"
    assert day_text(date(2027, 1, 4), TODAY) == "lun 4 janv 2027"
    assert day_text(MONDAY, TODAY) == "lun 16 nov"
    assert moment_text(wall_time(MONDAY, 7, 0)) == "lun 16 nov · 07:00"


def test_five_rows_with_the_value_in_the_middle(qtbot: Any) -> None:
    wheel = _wheel(qtbot)

    assert wheel.height() == VISIBLE_ROWS * ROW_HEIGHT
    assert wheel.visible_texts(0) == [
        "sam 14 nov",
        "dim 15 nov",
        "lun 16 nov",
        "mar 17 nov",
        "mer 18 nov",
    ]
    assert wheel.visible_texts(1) == ["05", "06", "07", "08", "09"]
    assert wheel.visible_texts(2) == ["", "", "00", "30", ""]
    assert wheel.moment() == wall_time(MONDAY, 7, 0)


def test_hours_go_from_00_to_23_and_minutes_by_the_half_hour(qtbot: Any) -> None:
    wheel = _wheel(qtbot)

    assert [text for _value, text in wheel.items(1)] == [f"{hour:02d}" for hour in range(24)]
    assert [text for _value, text in wheel.items(2)] == ["00", "30"]
    wheel.set_moment(wall_time(MONDAY, 16, 45))  # off the half hour: its half hour
    assert wheel.moment() == wall_time(MONDAY, 16, 30)
    wheel.step(1, 100)
    assert wheel.text(1) == "23"  # the ends hold
    wheel.step(2, -5)
    assert wheel.text(2) == "00"
    assert wheel.moment() == wall_time(MONDAY, 23, 0)


def test_the_arrow_keys_roll_the_column_with_the_focus(qtbot: Any) -> None:
    wheel = _wheel(qtbot)
    changes: list[bool] = []
    wheel.changed.connect(lambda: changes.append(True))
    finished: list[bool] = []
    wheel.finished.connect(finished.append)

    qtbot.keyClick(wheel, Qt.Key.Key_Down)
    assert wheel.text(0) == "mar 17 nov"
    qtbot.keyClick(wheel, Qt.Key.Key_Right)
    qtbot.keyClick(wheel, Qt.Key.Key_Up)
    assert wheel.text(1) == "06"
    qtbot.keyClick(wheel, Qt.Key.Key_Right)
    qtbot.keyClick(wheel, Qt.Key.Key_Down)
    qtbot.keyClick(wheel, Qt.Key.Key_Right)  # no column after the minutes
    assert wheel.current_column() == 2
    qtbot.keyClick(wheel, Qt.Key.Key_PageUp)
    assert wheel.moment() == wall_time(date(2026, 11, 17), 6, 0)
    assert len(changes) == 4  # PageUp on « 30 » rolled back to « 00 »
    qtbot.keyClick(wheel, Qt.Key.Key_Left)
    assert wheel.current_column() == 1
    qtbot.keyClick(wheel, Qt.Key.Key_Return)
    qtbot.keyClick(wheel, Qt.Key.Key_Escape)
    assert finished == [True, False]


def test_the_wheel_rolls_the_column_under_the_pointer(qtbot: Any) -> None:
    wheel = _wheel(qtbot)
    changes: list[bool] = []
    wheel.changed.connect(lambda: changes.append(True))

    _turn(wheel, _center(wheel, 1), -120)  # towards oneself: later
    assert wheel.text(1) == "08"
    _turn(wheel, _center(wheel, 0), 240)
    assert wheel.text(0) == "sam 14 nov"
    _turn(wheel, _center(wheel, 2), -60)  # half a notch: nothing yet
    assert wheel.text(2) == "00"
    _turn(wheel, _center(wheel, 2), -60)
    assert wheel.text(2) == "30"
    assert wheel.current_column() == 2
    assert len(changes) == 4


def test_a_click_brings_a_value_to_the_middle_and_a_drag_rolls(qtbot: Any) -> None:
    wheel = _wheel(qtbot)

    qtbot.mouseClick(wheel, Qt.MouseButton.LeftButton, pos=_center(wheel, 1, 2))
    assert wheel.text(1) == "09"
    qtbot.mouseClick(wheel, Qt.MouseButton.LeftButton, pos=_center(wheel, 0, -1))
    assert wheel.text(0) == "dim 15 nov"

    start = _center(wheel, 1)
    qtbot.mousePress(wheel, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(wheel, start - QPoint(0, 3 * ROW_HEIGHT))  # dragged up three rows
    qtbot.mouseRelease(wheel, Qt.MouseButton.LeftButton, pos=start - QPoint(0, 3 * ROW_HEIGHT))
    assert wheel.text(1) == "12"
    assert wheel.moment() == wall_time(date(2026, 11, 15), 12, 0)


def test_set_methods_do_not_count_as_a_change(qtbot: Any) -> None:
    wheel = _wheel(qtbot)
    changes: list[bool] = []
    wheel.changed.connect(lambda: changes.append(True))

    wheel.set_moment(wall_time(date(2027, 3, 1), 13, 30), today=TODAY)
    assert wheel.text(0) == "lun 1 mars 2027"
    assert wheel.moment() == wall_time(date(2027, 3, 1), 13, 30)
    assert not wheel.set_value(1, 99)
    assert changes == []


def test_a_day_wheel_alone(qtbot: Any) -> None:
    wheel = DateWheel()
    qtbot.addWidget(wheel)
    wheel.set_day(MONDAY, today=TODAY)

    assert wheel.column_count() == 1
    assert wheel.day() == MONDAY
    wheel.step(0, 4)
    assert wheel.day() == date(2026, 11, 20)
    assert wheel.text(0) == "ven 20 nov"
