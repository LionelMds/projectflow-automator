from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QEnterEvent, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.models import DEFAULT_TASK_TYPES, DEFAULT_TEAMS, Resource
from projectflow.core.planning.quick_entry import (
    EntryContext,
    ParsedEntry,
    ProjectInfo,
    parse_entry,
)
from projectflow.core.planning.walltime import format_wall_time, parse_wall_time, wall_date
from projectflow.ui.planning.hovercard import HoverCard, HoverInfo, dossier_summary, meta_line
from projectflow.ui.planning.inline_editor import InlineEditor
from projectflow.ui.planning.quick_entry_bar import (
    EXAMPLES,
    PLACEHOLDER,
    POPUP_ROW_HEIGHT,
    POPUP_ROWS,
    Availability,
    EntryChip,
    QuickEntryBar,
    Suggestion,
    absence_example,
    entry_chips,
    entry_examples,
    suggestions_for,
    word_bounds,
)
from projectflow.ui.planning.sync_badge import SyncBadge, badge_text, relative_age
from projectflow.ui.planning.toast import HOVER_GRACE_MS, MAX_TOASTS, Toast, ToastOverlay
from projectflow.ui.theme import apply_theme, theme_notifier

TODAY = date(2026, 10, 22)  # a Thursday
SCREENSHOT_DIR = os.environ.get("PLANNING_UI_SCREENSHOT_DIR", "")

RESOURCES = (
    Resource(id="lbi", name="Luca Bianchi", initials="LBI", team="montage", order=1),
    Resource(id="ych", name="Yann Chappuis", initials="YCH", team="montage", order=2),
    Resource(id="jmf", name="Jean-Marc Fasel", initials="JMF", team="montage", order=3),
    Resource(id="kdu", name="Kevin Dupuis", initials="KDU", team="atelier", order=4),
    Resource(id="tfa", name="Thomas Favre", initials="TFA", team="atelier", order=5),
    Resource(id="lbr", name="Laura Bron", initials="LBR", team="bureau", order=6),
    Resource(id="oti", name="Olivier Tissot", initials="OTI", team="montage", active=False),
)
PROJECTS = tuple(
    ProjectInfo(number, client, "", designation, "LMO")
    for number, client, designation in (
        ("2026-121", "Commune de Marsens", "Marquise entrée école"),
        ("2026-124", "Gérance Rhône SA", "Portail coulissant"),
        ("2026-129", "PPE Les Cèdres", "Garde-corps balcons"),
        ("2026-112", "M. Perret", "Escalier extérieur"),
        ("2025-125", "Coop", "Auvent quai"),
        ("2026-5231", "Implenia SA", "Garde-corps immeuble"),
    )
)
PROJECT_INDEX = {project.number: project for project in PROJECTS}
CONTEXT = EntryContext(
    today=TODAY,
    week_start=date(2026, 10, 19),
    calendar=WorkCalendar(cantons=("GE",)),
    resources=RESOURCES,
    teams=DEFAULT_TEAMS,
    task_types=DEFAULT_TASK_TYPES,
    find_project=PROJECT_INDEX.get,
    default_year=2026,
)
# Swiss first names that read like days or parts of day: « mar », « mat », « ven ».
NAMESAKES = (
    Resource(id="mro", name="Marc Rossier", initials="MRO", team="atelier", order=7),
    Resource(id="mve", name="Mathieu Venetz", initials="MVE", team="atelier", order=8),
)


@pytest.fixture(autouse=True)
def industry_theme(qapp: QApplication) -> Iterator[None]:
    sheet, palette, font = qapp.styleSheet(), qapp.palette(), qapp.font()
    # Styled without telling the widgets of earlier tests: some are gone and a slot left
    # behind by one of them would fail the setup of these tests.
    notifier = theme_notifier()
    blocked = notifier.blockSignals(True)  # noqa: FBT003
    try:
        apply_theme(qapp)
    finally:
        notifier.blockSignals(blocked)
    yield
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)
    qapp.setFont(font)


def _bar(qtbot: Any, *, width: int = 900) -> QuickEntryBar:
    bar = QuickEntryBar()
    qtbot.addWidget(bar)
    bar.set_context_provider(lambda: CONTEXT)
    bar.set_projects(PROJECTS)
    bar.resize(width, bar.sizeHint().height())
    bar.show()
    qtbot.waitExposed(bar)
    bar.activateWindow()
    bar.focus_entry()
    qtbot.waitUntil(bar.line_edit.hasFocus)
    return bar


def _chip(chips: tuple[EntryChip, ...], kind: str) -> EntryChip:
    return next(chip for chip in chips if chip.kind == kind)


def _labels(chips: tuple[EntryChip, ...], kind: str) -> list[str]:
    return [chip.label for chip in chips if chip.kind == kind]


def _save(widget_pixmap: QPixmap, name: str) -> None:
    assert not widget_pixmap.isNull()
    if SCREENSHOT_DIR:
        widget_pixmap.save(str(Path(SCREENSHOT_DIR) / f"planning_ui_entry_{name}.png"))


# ------------------------------------------------------------------ chips ----


def test_chips_of_a_planned_entry_on_two_people(qtbot: Any) -> None:
    bar = _bar(qtbot)
    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH")

    chips = bar.chips()
    project = _chip(chips, "project")
    assert (project.strong, project.text, project.tone) == (
        "2026-121",
        " · Commune de Marsens",
        "project",
    )
    assert _chip(chips, "title").strong == "Montage marquise"
    kind = _chip(chips, "type")
    assert (kind.text, kind.swatch) == ("Montage chantier", "#4a925c")
    assert _labels(chips, "person") == ["LBI · Luca Bianchi", "YCH · Yann Chappuis"]
    assert _labels(chips, "when") == ["jeu 22.10 06:30 → ven 23.10 12:00 · 2 j"]
    assert _labels(chips, "destination") == ["Entrée → planning"]
    assert "Maj+Entrée garde le dossier" in _labels(chips, "hint")
    assert "LBI, YCH" in bar.interpretation()


def test_chips_of_backlog_unassigned_and_absence_entries() -> None:
    def chips(text: str) -> tuple[EntryChip, ...]:
        from projectflow.core.planning.quick_entry import parse_entry  # noqa: PLC0415

        return entry_chips(text, parse_entry(text, CONTEXT), CONTEXT)

    backlog = chips("129 relevé de cotes <06.11 !")
    assert _labels(backlog, "deadline") == ["Avant le ven 06.11"]
    assert _labels(backlog, "urgent") == ["Urgent"]
    assert _labels(backlog, "destination") == ["Entrée → À planifier"]

    unassigned = chips("livraison 112 ven matin ?")
    assert _labels(unassigned, "when") == ["ven 23.10 06:30 → ven 23.10 12:00 · 1 j"]
    assert _labels(unassigned, "provisional") == ["Provisoire"]
    assert _labels(unassigned, "destination") == ["Entrée → ligne Non affecté"]

    absence = chips("/vac LBI 26-30.10")
    assert _labels(absence, "absence") == ["Vacances"]
    assert _labels(absence, "when") == ["lun 26.10 → ven 30.10 · 5 j"]
    assert _labels(absence, "destination") == ["Entrée → absence"]

    internal = chips("rangement stock TFA lun")
    assert _chip(internal, "project").text == "Sans dossier"


def test_warnings_are_amber_chips_pointing_at_their_text() -> None:
    from projectflow.core.planning.quick_entry import parse_entry  # noqa: PLC0415

    text = "255 portail LBX demain 7h20"
    chips = entry_chips(text, parse_entry(text, CONTEXT), CONTEXT)

    project = _chip(chips, "project")
    assert (project.text, project.tone) == ("2026-255 introuvable", "warn")
    warnings = [chip for chip in chips if chip.kind == "warning"]
    assert [chip.text for chip in warnings] == ["7h20 arrondi à 07:30"]
    assert all(chip.tone == "warn" for chip in warnings)
    assert text[warnings[0].start : warnings[0].end] == "7h20"
    # The unknown dossier is told once, by its own chip.
    assert not any("introuvable" in chip.text for chip in warnings)


def test_clicking_a_chip_selects_its_source_text(qtbot: Any) -> None:
    bar = _bar(qtbot)
    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH")
    chips = bar.chips()
    index = next(i for i, chip in enumerate(chips) if chip.label.startswith("LBI"))

    qtbot.mouseClick(
        bar.chip_strip, Qt.MouseButton.LeftButton, pos=bar.chip_strip.chip_rect(index).center()
    )

    assert bar.line_edit.selectedText() == "LBI"
    title = next(i for i, chip in enumerate(chips) if chip.kind == "title")
    qtbot.mouseClick(
        bar.chip_strip, Qt.MouseButton.LeftButton, pos=bar.chip_strip.chip_rect(title).center()
    )
    assert bar.line_edit.selectedText() == "montage marquise"


def test_empty_field_shows_examples_that_fill_it(qtbot: Any) -> None:
    bar = _bar(qtbot)

    examples = [chip for chip in bar.chips() if chip.kind == "example"]
    assert examples
    # The placeholder names no dossier nor person: it cannot type one the planning lacks.
    assert bar.line_edit.placeholderText() == PLACEHOLDER
    index = bar.chips().index(examples[0])
    qtbot.mouseClick(
        bar.chip_strip, Qt.MouseButton.LeftButton, pos=bar.chip_strip.chip_rect(index).center()
    )

    assert bar.text() == examples[0].text
    assert _labels(bar.chips(), "destination") == ["Entrée → planning"]


def _shop_context() -> EntryContext:
    """Build a planning with other people and dossiers than the examples of the spec."""
    people = (
        Resource(id="ps", name="Paulo Silva", initials="PS", team="atelier", order=1),
        Resource(id="nf", name="Nicolas Favre", initials="NF", team="montage", order=2),
        Resource(id="ga", name="Galvanisation", initials="GA", kind_of_resource="subcontractor"),
    )
    projects = {
        number: ProjectInfo(number, client, "", "Barrière", "LMO")
        for number, client in (("2026-5000", "Gillioz Immobilier"), ("2026-5003", "Régie du Lac"))
    }
    return replace(CONTEXT, resources=people, find_project=projects.get)


def test_examples_are_typed_with_the_planning_people_and_dossiers(qtbot: Any) -> None:
    context = _shop_context()
    projects = (
        ProjectInfo("2026-5003", "Régie du Lac", "", "Main courante", "LMO"),
        ProjectInfo("2026-121", "Inconnu", "", "Absent du répertoire", "LMO"),
        ProjectInfo("2026-5000", "Gillioz Immobilier", "", "Barrière", "LMO"),
    )

    examples = entry_examples(context, projects)

    assert examples[0] == "2026-5003 montage marquise jeu-ven PS NF"
    assert "5000 débit portail 1,5j PS lun" in examples
    assert examples[-1] == "/vac PS 26-30.10"
    for text in examples:
        entry = parse_entry(text, context)
        assert entry.destination != "invalid", text
        assert not entry.warnings, text
        assert not {"LBI", "YCH", "KDU", "GA", "121"} & set(text.split()), text
    assert absence_example(context) == "/vac PS 26-30.10"
    # Nobody yet: generic examples, no initials, and no absence example.
    empty = replace(context, resources=())
    assert entry_examples(empty, ()) == EXAMPLES
    assert absence_example(empty) == ""
    assert entry_examples(None) == EXAMPLES

    bar = QuickEntryBar()
    qtbot.addWidget(bar)
    bar.set_context_provider(lambda: context)
    bar.set_projects(projects)
    assert bar.examples() == examples


def test_read_only_field_offers_no_example(qtbot: Any) -> None:
    bar = _bar(qtbot)
    assert bar.examples()

    bar.line_edit.setReadOnly(True)

    assert bar.examples() == ()
    assert not [chip for chip in bar.chips() if chip.clickable]
    bar.line_edit.setReadOnly(False)
    examples = [chip for chip in bar.chips() if chip.kind == "example"]
    assert examples
    bar.line_edit.setReadOnly(True)
    bar._on_chip_clicked(examples[0])  # noqa: SLF001 - a click that was on its way
    assert bar.text() == ""


def test_typing_does_not_move_what_is_under_the_bar(qtbot: Any) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    bar = QuickEntryBar()
    bar.set_context_provider(lambda: CONTEXT)
    bar.set_projects(PROJECTS)
    below = QLabel("grille")
    layout.addWidget(bar)
    layout.addWidget(below, 1)
    host.resize(900, 600)
    host.show()
    qtbot.waitExposed(host)
    idle = below.geometry().top()
    assert bar.interpretation() == ""

    for text in ("2026-121 montage marquise jeu-ven LBI YCH", "pose", "/vac LBI 26-30.10"):
        bar.set_text(text)
        layout.activate()
        assert bar.interpretation(), text
        assert below.geometry().top() == idle, text
    bar.clear()
    layout.activate()
    assert below.geometry().top() == idle
    # Read-only and empty, nothing can be typed: the room under the field is given back.
    bar.line_edit.setReadOnly(True)
    qtbot.waitUntil(lambda: below.geometry().top() < idle)
    bar.line_edit.setReadOnly(False)
    qtbot.waitUntil(lambda: below.geometry().top() == idle)


def test_examples_stay_on_one_line_when_the_bar_is_narrow(qtbot: Any) -> None:
    bar = _bar(qtbot, width=420)

    shown = bar.chip_strip.shown_chips()
    assert 0 < len(shown) < len(bar.chips())
    assert bar.chip_strip.heightForWidth(420) == bar.chip_strip.heightForWidth(2000)
    hidden = bar.chips().index(next(c for c in bar.chips() if c not in shown))
    assert not bar.chip_strip.chip_rect(hidden).isValid()


def test_when_chip_tells_the_length_the_interpretation_tells() -> None:
    friday = _cell_context()
    text = "124 portail ½j"
    entry = parse_entry(text, friday)
    # The typed ½ j is kept as the estimate of the card it may become again…
    assert entry.estimate_days == 0.5
    # …but the Friday morning is a whole working day: chips say what the bar will show.
    assert _labels(entry_chips(text, entry, friday), "when") == [
        "ven 23.10 06:30 → ven 23.10 12:00 · 1 j"
    ]
    assert entry.interpretation.endswith("· 1 j")

    card = parse_entry("129 relevé 3j", CONTEXT)
    assert _labels(entry_chips("129 relevé 3j", card, CONTEXT), "when") == ["~3 j"]

    christmas = "/vac KDU 25.12"
    absence = parse_entry(christmas, CONTEXT)
    chips = entry_chips(christmas, absence, CONTEXT)
    assert _labels(chips, "when") == ["ven 25.12 · 0 j"]
    assert any("Noël" in chip.text for chip in chips if chip.kind == "warning")


# ------------------------------------------------------------ completion ----


def test_digits_complete_dossiers_most_recent_first(qtbot: Any) -> None:
    bar = _bar(qtbot)
    qtbot.keyClicks(bar.line_edit, "12")
    qtbot.waitUntil(bar.suggestions_visible)

    keys = [item.key for item in bar.suggestions()]
    assert keys == ["2026-129", "2026-124", "2026-121", "2025-125"]
    assert bar.suggestions()[0].detail == "PPE Les Cèdres · Garde-corps balcons · géré par LMO"

    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Down)
    assert bar.popup.current_row() == 1
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Tab)

    assert bar.text() == "2026-124 "
    assert not bar.suggestions_visible()
    assert bar.line_edit.hasFocus()


def _away_lbi(resource_id: str, _entry: ParsedEntry | None) -> Availability:
    if resource_id == "lbi":
        return Availability("absent", available=False)
    return Availability("libre")


def test_letters_and_plus_complete_people_and_teams(qtbot: Any) -> None:
    bar = _bar(qtbot)
    bar.set_availability_provider(_away_lbi)
    submitted: list[object] = []
    bar.entry_submitted.connect(lambda entry, _keep: submitted.append(entry))

    qtbot.keyClicks(bar.line_edit, "121 pose lb")
    bar.refresh()
    # The absent person is proposed last.
    assert [(item.key, item.note) for item in bar.suggestions()] == [
        ("LBR", "libre"),
        ("LBI", "absent"),
    ]
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)  # accepts, does not submit
    assert bar.text() == "121 pose LBR "
    assert submitted == []

    qtbot.keyClicks(bar.line_edit, "+mo")
    bar.refresh()
    first = bar.suggestions()[0]
    assert (first.kind, first.key) == ("team", "+montage")
    assert first.detail == "Montage · 3 personnes"
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Tab)
    assert bar.text() == "121 pose LBR +montage "

    # A plain text does not reorder.
    plain = suggestions_for("lb", CONTEXT, availability=lambda _rid: "?")
    assert [item.key for item in plain] == ["LBI", "LBR"]


def test_moving_into_a_word_never_completes_it(qtbot: Any) -> None:
    bar = _bar(qtbot)
    submitted: list[ParsedEntry] = []
    bar.entry_submitted.connect(lambda entry, _keep: submitted.append(entry))
    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH")

    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Home)
    for _ in range(6):
        qtbot.keyClick(bar.line_edit, Qt.Key.Key_Right)
    qtbot.wait(120)
    assert bar.line_edit.cursorPosition() == 6
    assert not bar.suggestions_visible()
    # A click inside « LBI » neither.
    bar.line_edit.setCursorPosition(36)
    qtbot.wait(120)
    assert not bar.suggestions_visible()
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)

    assert [entry.project_number for entry in submitted] == ["2026-121"]
    assert submitted[0].resource_ids == ("lbi", "ych")

    # Typing inside a word does not complete it either: the whole word would be replaced.
    bar.set_text("121 pose LB jeu")
    bar.line_edit.setCursorPosition(10)
    qtbot.keyClicks(bar.line_edit, "x")
    bar.refresh()
    assert not bar.suggestions_visible()

    # The popup shown while typing closes when the cursor moves away.
    bar.set_text("121 pose ")
    qtbot.keyClicks(bar.line_edit, "lb")
    bar.refresh()
    assert bar.suggestions_visible()
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Left)
    assert not bar.suggestions_visible()


def test_words_the_parser_reads_are_not_completed(qtbot: Any) -> None:
    context = replace(CONTEXT, resources=(*RESOURCES, *NAMESAKES))
    bar = _bar(qtbot)
    bar.set_context_provider(lambda: context)
    submitted: list[ParsedEntry] = []
    bar.entry_submitted.connect(lambda entry, _keep: submitted.append(entry))

    # « mar » is Tuesday, not Marc Rossier; « mat » the morning, not Mathieu Venetz.
    for text in ("124 portail KDU mar", "124 portail KDU ven mat", "portail KDU 121"):
        bar.set_text(text[:-1])
        qtbot.keyClicks(bar.line_edit, text[-1])
        bar.refresh()
        assert not bar.suggestions_visible(), text
    assert [item.key for item in suggestions_for("mar", context)] == ["MRO"]

    bar.set_text("124 portail KDU ma")
    qtbot.keyClicks(bar.line_edit, "r")
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)  # before the debounce, too
    assert len(submitted) == 1
    assert submitted[0].resource_ids == ("kdu",)
    assert submitted[0].start is not None
    assert wall_date(submitted[0].start) == date(2026, 10, 27)

    # A known dossier is complete; an unknown one or an ambiguous word is completed.
    bar.set_text("2026-12")
    qtbot.keyClicks(bar.line_edit, "1")
    bar.refresh()
    assert not bar.suggestions_visible()
    bar.set_text("/so")
    qtbot.keyClicks(bar.line_edit, "u")
    bar.refresh()
    assert [item.key for item in bar.suggestions()] == ["/soudure", "/soustraitance"]


def test_slash_completes_types_and_absences(qtbot: Any) -> None:
    bar = _bar(qtbot)
    qtbot.keyClicks(bar.line_edit, "/sou")
    bar.refresh()

    items = bar.suggestions()
    assert [item.key for item in items] == ["/soudure", "/soustraitance"]
    assert items[0].swatch == "#b7684c"
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Down)
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)
    assert bar.text() == "/soustraitance "

    vacation = suggestions_for("/va", CONTEXT)
    assert [(item.kind, item.key, item.detail) for item in vacation] == [
        ("absence", "/vac", "Absence · Vacances")
    ]
    assert suggestions_for("/vac", CONTEXT) == ()  # already complete


def test_completion_rules() -> None:
    assert word_bounds("121 pose LB", 11) == (9, 11)
    assert word_bounds("121 pose LB", 6) == (4, 8)
    # Bare letters never turn a task keyword into a team.
    assert all(item.kind == "person" for item in suggestions_for("mon", CONTEXT))
    # Typed initials that are complete leave Entrée to create the task.
    assert suggestions_for("LBI", CONTEXT) == ()
    assert suggestions_for("1", CONTEXT, PROJECTS) == ()
    inactive = [item.key for item in suggestions_for("+o", CONTEXT)]
    assert "OTI" not in inactive
    assert isinstance(suggestions_for("+", CONTEXT)[0], Suggestion)
    # A complete number is recognised even behind a popup full of longer ones.
    many = (*PROJECTS, *(ProjectInfo(f"2026-121{digit}") for digit in range(10)))
    assert suggestions_for("2026-121", CONTEXT, many) == ()


def test_slash_lists_every_type_and_absence_in_a_scrolling_popup(qtbot: Any) -> None:
    bar = _bar(qtbot)
    qtbot.keyClicks(bar.line_edit, "/")
    bar.refresh()

    items = bar.suggestions()
    assert [item.key for item in items if item.kind == "type"] == [
        f"/{kind.key}" for kind in DEFAULT_TASK_TYPES
    ]
    assert [item.key for item in items if item.kind == "absence"][:2] == ["/vac", "/absent"]
    assert len(items) > POPUP_ROWS
    assert bar.popup.height() == POPUP_ROWS * POPUP_ROW_HEIGHT + 2
    for _ in range(len(items) - 1):
        qtbot.keyClick(bar.line_edit, Qt.Key.Key_Down)
    assert bar.popup.current() == items[-1]
    view = bar.popup.view
    assert view.visualRect(view.currentIndex()).bottom() <= view.viewport().height()


# ------------------------------------------------------------- submitting ----


def test_enter_submits_and_shift_enter_keeps_the_dossier(qtbot: Any) -> None:
    bar = _bar(qtbot)
    submitted: list[tuple[ParsedEntry, bool]] = []
    bar.entry_submitted.connect(lambda entry, keep: submitted.append((entry, keep)))

    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH")
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)

    assert len(submitted) == 1
    entry, keep = submitted[0]
    assert (entry.project_number, entry.resource_ids, keep) == ("2026-121", ("lbi", "ych"), False)
    assert bar.text() == ""

    bar.set_text("124 débit portail 1,5j KDU lun")
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)

    entry, keep = submitted[1]
    assert (entry.project_number, entry.destination, keep) == ("2026-124", "grid", True)
    assert bar.text() == "124 "
    assert _labels(bar.chips(), "project") == ["2026-124 · Gérance Rhône SA"]


def test_the_line_stays_until_the_handler_accepts_it(qtbot: Any) -> None:
    bar = _bar(qtbot)
    seen: list[str] = []
    changes: list[object] = []
    bar.entry_changed.connect(changes.append)

    def refuse(_entry: object, _keep: object) -> None:
        seen.append(bar.text())
        bar.refuse()  # read-only planning, say

    bar.entry_submitted.connect(refuse)
    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH")
    assert isinstance(changes[-1], ParsedEntry)
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)

    assert seen == ["2026-121 montage marquise jeu-ven LBI YCH"]
    assert bar.text() == "2026-121 montage marquise jeu-ven LBI YCH"
    assert bar.flashing

    bar.entry_submitted.disconnect(refuse)
    bar.entry_submitted.connect(lambda _entry, _keep: seen.append(bar.text()))
    assert bar.submit()
    assert seen[-1] == "2026-121 montage marquise jeu-ven LBI YCH"
    assert bar.text() == ""
    assert changes[-1] is None


def test_undo_and_redo_go_to_the_planning_when_the_field_has_none(qtbot: Any) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    layout = QVBoxLayout(window)
    bar = QuickEntryBar()
    bar.set_context_provider(lambda: CONTEXT)
    bar.set_projects(PROJECTS)
    layout.addWidget(bar)
    undo, redo = (
        QShortcut(QKeySequence.StandardKey.Undo, window),
        QShortcut(QKeySequence.StandardKey.Redo, window),
    )
    actions: list[str] = []
    undo.activated.connect(lambda: actions.append("undo"))
    redo.activated.connect(lambda: actions.append("redo"))
    window.resize(900, 200)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    bar.focus_entry()
    qtbot.waitUntil(bar.line_edit.hasFocus)

    bar.set_text("2026-121 pose jeu LBI")
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)  # created: the field is empty
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert actions == ["undo", "redo"]

    # While the field has its own history, Ctrl+Z undoes the typing, a completion too.
    qtbot.keyClicks(bar.line_edit, "121 pose lb")
    bar.refresh()
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Tab)
    assert bar.text() == "121 pose LBI "
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert bar.text() == "121 pose lb"
    assert actions == ["undo", "redo"]


def test_invalid_entry_is_not_submitted(qtbot: Any) -> None:
    bar = _bar(qtbot)
    submitted: list[object] = []
    rejected: list[object] = []
    bar.entry_submitted.connect(lambda entry, _keep: submitted.append(entry))
    bar.entry_rejected.connect(rejected.append)

    bar.set_text("am")
    qtbot.keyClick(bar.line_edit, Qt.Key.Key_Return)

    assert submitted == []
    assert len(rejected) == 1
    assert bar.flashing
    assert bar.text() == "am"
    assert [chip.tone for chip in bar.chips()] == ["warn"]
    qtbot.waitUntil(lambda: not bar.flashing)


def test_escape_clears_leaves_and_cancels(qtbot: Any) -> None:
    bar = _bar(qtbot)
    with qtbot.waitSignal(bar.cancelled):
        bar.set_text("121 pose")
        qtbot.keyClick(bar.line_edit, Qt.Key.Key_Escape)

    assert bar.text() == ""
    assert not bar.line_edit.hasFocus()


def test_without_context_nothing_is_parsed(qtbot: Any) -> None:
    bar = QuickEntryBar()
    qtbot.addWidget(bar)
    bar.set_text("2026-121 pose")

    assert bar.entry is None
    assert bar.chips() == ()
    assert not bar.submit()


# ---------------------------------------------------------- inline editor ----


@dataclass
class _Host:
    window: QWidget
    grid: QWidget
    other: QLineEdit
    editor: InlineEditor


def _host(qtbot: Any) -> _Host:
    window = QWidget()
    qtbot.addWidget(window)
    window.resize(900, 420)
    layout = QVBoxLayout(window)
    other = QLineEdit()
    grid = QWidget()
    grid.setMinimumHeight(340)
    grid.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    layout.addWidget(other)
    layout.addWidget(grid)
    editor = InlineEditor(grid)
    editor.set_projects(PROJECTS)
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    grid.setFocus()
    qtbot.waitUntil(grid.hasFocus)
    return _Host(window, grid, other, editor)


def _open_on_friday(qtbot: Any, host: _Host, prefill: str = "") -> None:
    cell = QRect(host.grid.mapToGlobal(QPoint(40, 40)), host.grid.mapToGlobal(QPoint(120, 70)))
    host.editor.open(
        cell, _cell_context(), label="KDU · ven 23.10 06:30 → ven 23.10 12:00", prefill=prefill
    )
    qtbot.waitUntil(host.editor.bar.line_edit.hasFocus)


def _cell_context() -> EntryContext:
    return replace(
        CONTEXT,
        cell_resource_ids=("kdu",),
        cell_start=parse_wall_time("2026-10-23T06:30"),
        cell_end=parse_wall_time("2026-10-23T12:00"),
    )


def test_inline_editor_submits_a_task_on_the_cell(qtbot: Any) -> None:
    host = _host(qtbot)
    cell = QRect(host.grid.mapToGlobal(QPoint(220, 60)), host.grid.mapToGlobal(QPoint(380, 90)))

    host.editor.open(
        cell, _cell_context(), label="KDU · ven 23.10 06:30 → ven 23.10 12:00", prefill="1"
    )
    qtbot.waitUntil(host.editor.bar.line_edit.hasFocus)
    assert host.editor.isVisible()
    assert host.editor.label_text().startswith("KDU · ven 23.10")
    assert host.editor.bar.text() == "1"
    assert host.editor.geometry().intersects(
        QRect(host.grid.mapFromGlobal(cell.topLeft()), cell.size())
    )

    # QTest.keyClicks crashes PySide6 6.11 on Windows with accented letters: insert them.
    qtbot.keyClicks(host.editor.bar.line_edit, "24 ")
    host.editor.bar.line_edit.insert("débit portail")
    with qtbot.waitSignal(host.editor.submitted) as blocker:
        qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Return)

    entry, advance = blocker.args
    assert isinstance(entry, ParsedEntry)
    assert advance is False
    assert entry.project_number == "2026-124"
    assert entry.resource_ids == ("kdu",)
    assert entry.start is not None
    assert format_wall_time(entry.start) == "2026-10-23T06:30"
    assert not host.editor.isVisible()
    assert not host.editor.is_open()
    # The grid gets the focus back, for its cell cursor.
    assert QApplication.focusWidget() is host.grid
    # The keys are on their own line, never cut off by a long cell label.
    assert host.editor.hint_text() == "Entrée crée · Tab crée et continue · Échap annule"
    hint = next(
        widget
        for widget in host.editor.findChildren(QLabel)
        if widget.text() == host.editor.hint_text()
    )
    assert hint.wordWrap()
    assert host.editor.label_text() == "KDU · ven 23.10 06:30 → ven 23.10 12:00"


def test_inline_editor_tab_creates_and_moves_on(qtbot: Any) -> None:
    host = _host(qtbot)
    events: list[tuple[str, object]] = []
    host.editor.submitted.connect(lambda _entry, advance: events.append(("submitted", advance)))
    host.editor.cancelled.connect(lambda reason: events.append(("cancelled", reason)))

    _open_on_friday(qtbot, host)
    qtbot.keyClicks(host.editor.bar.line_edit, "124 portail")
    qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Tab)

    assert events == [("submitted", True)]
    assert not host.editor.is_open()
    assert QApplication.focusWidget() is host.grid

    # Maj+Tab neither leaves the editor nor loses the line.
    _open_on_friday(qtbot, host)
    qtbot.keyClicks(host.editor.bar.line_edit, "124 portail")
    qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert host.editor.is_open()
    assert host.editor.bar.text() == "124 portail"
    assert host.editor.bar.line_edit.hasFocus()


def test_inline_editor_keeps_a_refused_line(qtbot: Any) -> None:
    host = _host(qtbot)
    host.editor.submitted.connect(lambda _entry, _advance: host.editor.refuse())

    _open_on_friday(qtbot, host, prefill="1")
    qtbot.keyClicks(host.editor.bar.line_edit, "24 portail")
    qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Return)

    assert host.editor.is_open()
    assert host.editor.bar.text() == "124 portail"
    assert host.editor.bar.flashing


def test_inline_editor_cancels_on_escape_and_focus_loss(qtbot: Any) -> None:
    host = _host(qtbot)
    cancelled: list[str] = []
    submitted: list[object] = []
    host.editor.cancelled.connect(cancelled.append)
    host.editor.submitted.connect(submitted.append)

    _open_on_friday(qtbot, host)
    qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Escape)
    assert cancelled == ["escape"]
    assert not host.editor.isVisible()
    assert QApplication.focusWidget() is host.grid

    _open_on_friday(qtbot, host)
    host.other.setFocus()
    qtbot.waitUntil(lambda: len(cancelled) == 2)
    assert cancelled[-1] == "focus"
    assert not host.editor.isVisible()
    # The focus stays where the user put it.
    assert QApplication.focusWidget() is host.other

    _open_on_friday(qtbot, host)
    qtbot.keyClicks(host.editor.bar.line_edit, "am")
    qtbot.keyClick(host.editor.bar.line_edit, Qt.Key.Key_Return)
    assert submitted == []
    assert host.editor.is_open()


# ------------------------------------------------------------------ toasts ----


def test_toast_action_runs_once_and_dismisses(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 400)
    overlay = ToastOverlay(parent)
    parent.show()
    calls: list[str] = []

    toast = overlay.show_toast(
        "Tâche supprimée : 2026-121 Montage marquise",
        action=lambda: calls.append("undo"),
        author_tag="LMO · BUREAU-2",
    )

    assert toast.action_button.text() == "Annuler"
    assert toast.tag_label.text() == "LMO · BUREAU-2"
    assert overlay.geometry().right() <= parent.width()
    assert overlay.geometry().bottom() <= parent.height()
    qtbot.mouseClick(toast.action_button, Qt.MouseButton.LeftButton)

    assert calls == ["undo"]
    assert overlay.toasts() == ()
    assert not overlay.isVisible()
    toast.trigger()  # once only
    assert calls == ["undo"]


def _hover(toast: Toast) -> None:
    inside = QPointF(toast.rect().center())
    QApplication.sendEvent(toast, QEnterEvent(inside, inside, QPointF(toast.mapToGlobal(inside))))
    QApplication.sendEvent(toast, QEvent(QEvent.Type.Leave))


def test_hovering_a_toast_never_shortens_it(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 400)
    overlay = ToastOverlay(parent)
    parent.show()
    qtbot.waitExposed(parent)

    sticky = overlay.show_toast("Collant", timeout_ms=0)
    timed = overlay.show_toast("Annulable", action=lambda: None, timeout_ms=HOVER_GRACE_MS + 900)
    _hover(sticky)
    _hover(timed)
    qtbot.wait(HOVER_GRACE_MS + 300)

    assert overlay.toasts() == (sticky, timed)
    qtbot.waitUntil(lambda: overlay.toasts() == (sticky,), timeout=3000)
    assert not sticky.dismissed_already


def test_the_gaps_of_the_toast_stack_let_the_mouse_through(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 400)
    overlay = ToastOverlay(parent)
    parent.show()
    qtbot.waitExposed(parent)

    long = overlay.show_toast("Une tâche a été ajoutée dans À planifier", timeout_ms=0)
    short = overlay.show_toast("Bref", timeout_ms=0)
    assert short.width() < long.width()
    gap = overlay.mapTo(parent, QPoint(2, short.geometry().center().y()))
    between = overlay.mapTo(
        parent, QPoint(long.geometry().center().x(), long.geometry().bottom() + 2)
    )

    assert parent.childAt(gap) is None
    assert parent.childAt(between) is None
    assert parent.childAt(overlay.mapTo(parent, short.geometry().center())) is not None


def test_at_most_three_toasts_and_they_time_out(qtbot: Any) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.resize(800, 400)
    overlay = ToastOverlay(parent, margin_right=320)
    parent.show()

    for number in range(5):
        overlay.show_toast(f"Message {number}", action=lambda: None, timeout_ms=0)
    assert [toast.message() for toast in overlay.toasts()] == [
        "Message 2",
        "Message 3",
        "Message 4",
    ]
    assert len(overlay.toasts()) == MAX_TOASTS
    assert overlay.geometry().right() <= parent.width() - 320
    qtbot.wait(20)
    # Stacked one above the other, each at its full height.
    tops = [toast.geometry().top() for toast in overlay.toasts()]
    assert tops == sorted(tops)
    assert len(set(tops)) == MAX_TOASTS
    assert all(toast.height() >= toast.sizeHint().height() for toast in overlay.toasts())
    assert overlay.toasts()[-1].geometry().bottom() < overlay.height()

    overlay.clear()
    overlay.show_toast("Bref", timeout_ms=30)
    qtbot.waitUntil(lambda: overlay.toasts() == ())


# -------------------------------------------------------------- sync badge ----


@dataclass(frozen=True)
class _Status:
    state: str = "ok"
    pending: int = 0
    last_success: float | None = None
    message: str = ""


def test_sync_badge_texts() -> None:
    now = 100.0
    assert badge_text(_Status(last_success=98.0), local=False, now=now) == (
        "À jour · il y a 2 s",
        "ok",
    )
    assert badge_text(_Status(state="sending", pending=2), local=False, now=now) == (
        "Envoi… (2)",
        "warn",
    )
    assert badge_text(_Status(state="offline"), local=False, now=now) == (
        "Hors ligne · consultation seule",
        "err",
    )
    assert badge_text(_Status(state="auth_required"), local=False, now=now) == (
        "Reconnexion Microsoft requise",
        "err",
    )
    assert badge_text(_Status(state="forbidden"), local=False, now=now) == (
        "Autorisation Microsoft manquante",
        "err",
    )
    assert badge_text(_Status(last_success=99.0), local=True, now=now) == (
        "Planning local (ce poste)",
        "neutral",
    )
    assert badge_text(None, local=False, now=now) == ("Synchronisation…", "neutral")
    assert [relative_age(value) for value in (0.4, 59, 61, 7300)] == [
        "à l'instant",
        "il y a 59 s",
        "il y a 1 min",
        "il y a 2 h",
    ]


def test_sync_badge_refreshes_its_age_and_tooltip(qtbot: Any) -> None:
    clock = [10.0]
    badge = SyncBadge(monotonic=lambda: clock[0])
    qtbot.addWidget(badge)
    badge.show()
    clicks: list[bool] = []
    badge.clicked.connect(lambda: clicks.append(True))

    badge.set_status(_Status(last_success=8.0))
    assert badge.text() == "À jour · il y a 2 s"
    clock[0] = 15.0
    qtbot.waitUntil(lambda: badge.text() == "À jour · il y a 7 s", timeout=3000)

    badge.set_status(_Status(state="offline", message="Planning hors ligne : consultation seule."))
    assert (badge.text(), badge.tone()) == ("Hors ligne · consultation seule", "err")
    assert badge.toolTip() == "Planning hors ligne : consultation seule."
    qtbot.mouseClick(badge, Qt.MouseButton.LeftButton)
    assert clicks == [True]

    badge.set_local(local=True)
    badge.set_status(_Status(last_success=14.0))
    assert badge.text() == "Planning local (ce poste)"
    assert "uniquement" in badge.toolTip()


def test_sync_badge_sign_in_works_from_the_keyboard(qtbot: Any) -> None:
    badge = SyncBadge()
    qtbot.addWidget(badge)
    clicks: list[bool] = []
    badge.clicked.connect(lambda: clicks.append(True))
    badge.set_status(_Status(last_success=8.0))
    assert badge.focusPolicy() == Qt.FocusPolicy.NoFocus  # nothing to do: no tab stop

    badge.set_status(_Status(state="auth_required"))
    badge.show()
    qtbot.waitExposed(badge)
    badge.activateWindow()
    assert badge.focusPolicy() == Qt.FocusPolicy.TabFocus
    assert badge.accessibleName().endswith("Reconnexion Microsoft requise")
    badge.setFocus()
    qtbot.waitUntil(badge.hasFocus)
    qtbot.keyClick(badge, Qt.Key.Key_Space)
    qtbot.keyClick(badge, Qt.Key.Key_Return)
    assert clicks == [True, True]


# -------------------------------------------------------------- hover card ----


def _hover_info() -> HoverInfo:
    day = 1440
    start = parse_wall_time("2026-10-19T06:30")
    return HoverInfo(
        number="2026-121",
        designation="Marquise entrée école",
        client="Commune de Marsens",
        contact="M. Rey · 079 000 00 00",
        atelier_release="14.10",
        managed_by="LMO",
        location="Marsens, rte de l'École 4",
        title="Montage marquise",
        type_label="Montage chantier",
        type_color="#4a925c",
        when="jeu 22.10 06:30 → ven 23.10 12:00",
        duration="2 j",
        people=("LBI", "YCH"),
        status="Confirmé",
        notes="Accès camion 7h-9h",
        findings=("LBI absent le ven 23.10 (Vacances)",),
        author="Modifié par LMO · BUREAU-2 il y a 3 min",
        dossier_tasks=(
            (start, start + 2 * day, "#b7684c"),
            (start + 2 * day, start + 3 * day, "#aa732b"),
            (start + 3 * day, start + 4 * day + 330, "#4a925c"),
        ),
        current=(start + 3 * day, start + 4 * day + 330),
        dossier_minutes=51 * 60,
    )


def test_hover_card_shows_dossier_task_and_strip(qtbot: Any) -> None:
    card = HoverCard()
    qtbot.addWidget(card)

    card.show_for(_hover_info(), QPoint(200, 200))

    assert card.isVisible()
    texts = card.texts()
    assert texts[:2] == ["2026-121", "Commune de Marsens"]
    assert "Sorti atelier 14.10 · Géré par LMO · Marsens, rte de l'École 4" in texts
    assert "3 tâches · 51 h" in texts
    assert "▲ LBI absent le ven 23.10 (Vacances)" in texts
    assert len(card.strip.segments()) == len(_hover_info().dossier_tasks)
    assert not card.testAttribute(Qt.WidgetAttribute.WA_WState_Hidden)
    card.hide()
    assert not card.isVisible()

    card.show_for(HoverInfo(number="2026-130", title="Relevé"), QPoint(10, 10))
    assert card.texts() == ["2026-130", "Relevé"]
    assert dossier_summary(1, 90) == "1 tâche · 1 h 30"
    assert meta_line(HoverInfo()) == ""


def test_hover_card_grows_with_long_notes(qtbot: Any) -> None:
    card = HoverCard()
    qtbot.addWidget(card)
    notes = " ".join(["Accès camion par la cour arrière, clé chez le concierge."] * 6)

    card.show_for(replace(_hover_info(), notes=notes), QPoint(200, 200))

    layout = card.layout()
    assert layout is not None
    assert card.height() >= layout.totalHeightForWidth(card.width())
    # The last row (the author) is inside the frame.
    assert card.author_label.geometry().bottom() <= card.frame_rect().bottom()


# ------------------------------------------------------------- screenshots ----


def _composite(base: QWidget, popups: list[QWidget]) -> QPixmap:
    """Paint the base widget with its popup windows where they stand on screen."""
    origin = base.mapToGlobal(QPoint(0, 0))
    bottom = max(
        [base.height()]
        + [p.mapToGlobal(QPoint(0, 0)).y() - origin.y() + p.height() for p in popups]
    )
    image = QPixmap(base.width(), bottom + 8)
    image.fill(base.palette().window().color())
    painter = QPainter(image)
    painter.drawPixmap(0, 0, base.grab())
    for popup in popups:
        offset = popup.mapToGlobal(QPoint(0, 0)) - origin
        painter.drawPixmap(offset, popup.grab())
    painter.end()
    return image


def test_screenshots_of_the_bar_toasts_and_hover_card(qtbot: Any) -> None:
    host = QWidget()
    host.setProperty("pfRoot", "true")
    host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(16, 16, 16, 16)
    bar = QuickEntryBar()
    bar.set_context_provider(lambda: CONTEXT)
    bar.set_projects(PROJECTS)
    bar.set_availability_provider(_away_lbi)
    layout.addWidget(bar)
    layout.addStretch(1)
    host.resize(1000, 300)
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    bar.focus_entry()
    qtbot.waitUntil(bar.line_edit.hasFocus)

    _save(host.grab(), "empty")
    bar.set_text("2026-121 montage marquise jeu-ven LBI YCH ? <30.10")
    _save(host.grab(), "chips")
    bar.set_text("255 portail LBX demain 7h20 !")
    _save(host.grab(), "warnings")
    bar.set_text("121 pose marquise jeu ")
    qtbot.keyClicks(bar.line_edit, "+")
    bar.refresh()
    assert bar.suggestions_visible()
    _save(_composite(host, [bar.popup]), "people_popup")
    bar.set_text("")
    qtbot.keyClicks(bar.line_edit, "12")
    bar.refresh()
    _save(_composite(host, [bar.popup]), "dossier_popup")
    bar.set_text("")
    qtbot.keyClicks(bar.line_edit, "/")
    bar.refresh()
    assert bar.suggestions_visible()
    _save(_composite(host, [bar.popup]), "slash_popup")

    board = QWidget()
    board.setProperty("pfRoot", "true")
    board.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    qtbot.addWidget(board)
    board.resize(900, 520)
    overlay = ToastOverlay(board)
    badge = SyncBadge(board, monotonic=lambda: 10.0)
    badge.set_status(_Status(last_success=8.0))
    badge.move(16, 16)
    sending = SyncBadge(board)
    sending.set_status(_Status(state="sending", pending=2))
    sending.move(220, 16)
    offline = SyncBadge(board)
    offline.set_status(_Status(state="offline"))
    offline.move(360, 16)
    local = SyncBadge(board, local=True)
    local.move(600, 16)
    board.show()
    qtbot.waitExposed(board)
    board.activateWindow()
    overlay.show_toast(
        "a ajouté « 2026-131 Relevé de cotes passerelle » dans À planifier",
        author_tag="SBE · bureau",
        timeout_ms=0,
    )
    overlay.show_toast(
        "Tâche supprimée : 2026-121 Montage marquise", action=lambda: None, timeout_ms=0
    )
    overlay.show_toast("LBI retiré de 2026-121 · Ctrl+Z pour annuler", timeout_ms=0)
    card = HoverCard(board)
    card.show_for(_hover_info(), board.mapToGlobal(QPoint(40, 50)))

    editor = InlineEditor(board)
    cell = QRect(board.mapToGlobal(QPoint(440, 100)), board.mapToGlobal(QPoint(580, 130)))
    editor.open(
        cell,
        _cell_context(),
        label="KDU · ven 23.10 06:30 → ven 23.10 12:00",
        prefill="124 débit portail ½j",
    )
    qtbot.waitUntil(editor.bar.line_edit.hasFocus)
    editor.bar.refresh()
    _save(_composite(board, [card]), "toasts_hover")
    assert card.isVisible()
