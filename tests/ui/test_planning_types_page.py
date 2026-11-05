from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import CommandContext, calendar_resolver
from projectflow.core.planning.models import (
    DEFAULT_TASK_TYPES,
    Author,
    PlanningMeta,
    PlanningTask,
    TaskType,
)
from projectflow.core.planning.task_types import SIMPLE_TASK_TYPES
from projectflow.ui.dialogs.planning_team import CommandBuilder, TeamDialog
from projectflow.ui.planning.colors import type_colors

AUTHOR = Author(initials="LM", name="Lionel Mottiez", poste="BUREAU-LM")
STAMP = "2026-10-01T08:00:00Z"
NOW = "2026-10-26T09:00:00Z"


def make_task(task_id: str, task_type: str) -> PlanningTask:
    return PlanningTask.model_validate(
        {
            "id": task_id,
            "title": f"Tâche {task_id}",
            "task_type": task_type,
            "created_at": STAMP,
            "updated_at": STAMP,
        }
    )


class FakePlanning:
    """Applies each builder as the planning service would, one entry per undo step."""

    def __init__(self, snapshot: PlanningSnapshot) -> None:
        self.snapshot = snapshot
        self.labels: list[str] = []
        self.steps: list[int] = []

    def apply(self, build: CommandBuilder, label: str) -> None:
        context = CommandContext(
            snapshot=self.snapshot,
            author=AUTHOR,
            now=NOW,
            calendar_for=calendar_resolver(self.snapshot),
        )
        changes = build(context)
        self.snapshot = self.snapshot.apply(changes)
        self.labels.append(label)
        self.steps.append(len(changes))

    def types(self) -> list[str]:
        return [kind.key for kind in self.snapshot.meta.task_types]


def planning_with(*tasks: PlanningTask, meta: PlanningMeta | None = None) -> FakePlanning:
    return FakePlanning(
        PlanningSnapshot(tasks={task.id: task for task in tasks}, meta=meta or PlanningMeta())
    )


def open_page(qtbot, planning: FakePlanning) -> tuple[TeamDialog, Any]:  # type: ignore[no-untyped-def]
    dialog = TeamDialog(lambda: planning.snapshot, planning.apply)
    qtbot.addWidget(dialog)
    dialog.show_page("types")
    return dialog, dialog.types_page


def test_page_lists_the_types_with_their_tasks(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = planning_with(make_task("a", "soudure"), make_task("b", "soudure"))
    dialog, page = open_page(qtbot, planning)

    assert [row.key for row in page.rows] == [kind.key for kind in DEFAULT_TASK_TYPES]
    soudure = page.row("soudure")
    assert soudure is not None
    assert soudure.name_edit.text() == "Soudure / assemblage"
    assert soudure.keywords_edit.text().startswith("soudure, souder")
    assert soudure.command_label.text() == "/soudure"
    assert soudure.count_label.text() == "2 tâches"
    assert page.rows[0].count_label.text() == "aucune tâche"
    assert not page.rows[0].up_button.isEnabled()
    assert dialog.pages.currentWidget().widget() is page  # type: ignore[attr-defined]
    assert "Soudure / assemblage" in page.default_label.text()


def test_add_rename_colour_keywords_and_reorder(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = planning_with()
    _dialog, page = open_page(qtbot, planning)

    page.new_type_edit.setText("Atelier")
    page.add_button.click()
    atelier = page.row("atelier")
    assert atelier is not None
    assert page.new_type_edit.text() == ""
    page.new_type_edit.setText("atelier")
    page.add_button.click()
    assert "existe" in page.error_label.text() or "déjà" in page.error_label.text()

    atelier.name_edit.setText("Atelier serrurerie")
    atelier.name_edit.editingFinished.emit()
    atelier.keywords_edit.setText("Serrurerie, Grille, ÉQUERRE, grille")
    atelier.keywords_edit.editingFinished.emit()
    page.set_color("atelier", "#203040")
    atelier.up_button.click()

    assert planning.labels == [
        "Ajouter le type Atelier",
        "Renommer le type Atelier en Atelier serrurerie",
        "Mots-clés du type Atelier serrurerie",
        "Couleur du type Atelier serrurerie",
        "Monter le type Atelier serrurerie",
    ]
    kind = planning.snapshot.meta.task_type("atelier")
    assert kind.label == "Atelier serrurerie"
    assert kind.keywords == ("serrurerie", "grille", "equerre")
    assert kind.color == "#203040"
    assert kind.color_dark not in {"", "#203040"}
    assert planning.types()[-2:] == ["atelier", "interne"]
    # The palette pairs light and dark colours.
    page.row("atelier").color_menu.actions()[3].trigger()  # type: ignore[union-attr]
    assert planning.snapshot.meta.task_type("atelier").color_dark == "#6fb880"


def test_keyword_of_another_type_is_refused(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = planning_with()
    _dialog, page = open_page(qtbot, planning)
    debit = page.row("debit")
    assert debit is not None
    debit.keywords_edit.setText("debit, pose")
    debit.keywords_edit.editingFinished.emit()
    assert planning.labels == []
    assert "Montage chantier" in page.error_label.text()


def test_delete_moves_its_tasks_in_one_step(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = planning_with(
        make_task("a", "debit"), make_task("b", "debit"), make_task("c", "dessin")
    )
    _dialog, page = open_page(qtbot, planning)
    asked: list[tuple[str, int, list[str]]] = []

    def ask(kind: TaskType, count: int, remaining: Sequence[TaskType]) -> str | None:
        asked.append((kind.key, count, [item.key for item in remaining]))
        return "soudure"

    page.ask_replacement = ask
    page.row("debit").delete_button.click()  # type: ignore[union-attr]

    assert asked[0][:2] == ("debit", 2)
    assert "debit" not in asked[0][2]
    assert planning.labels == ["Supprimer le type Débit (2 tâches en Soudure / assemblage)"]
    assert planning.steps == [3]  # the meta and both tasks, one undo step
    assert "debit" not in planning.types()
    assert planning.snapshot.tasks["a"].task_type == "soudure"
    assert planning.snapshot.tasks["c"].task_type == "dessin"

    # Cancelled: nothing changes.
    page.ask_replacement = lambda _kind, _count, _remaining: None
    page.row("dessin").delete_button.click()  # type: ignore[union-attr]
    assert len(planning.labels) == 1
    # An unused type goes without question.
    page.row("interne").delete_button.click()  # type: ignore[union-attr]
    assert planning.labels[-1] == "Supprimer le type Interne"


def test_the_last_type_cannot_be_deleted(qtbot) -> None:  # type: ignore[no-untyped-def]
    only = (DEFAULT_TASK_TYPES[0],)
    planning = planning_with(meta=PlanningMeta(task_types=only))
    _dialog, page = open_page(qtbot, planning)
    row = page.rows[0]
    assert not row.delete_button.isEnabled()
    page._delete(row.key)  # noqa: SLF001
    assert planning.labels == []
    assert "au moins un type" in page.error_label.text()


def test_simplify_preset_converts_every_task(qtbot) -> None:  # type: ignore[no-untyped-def]
    tasks = [
        make_task("weld", "soudure"),
        make_task("cut", "debit"),
        make_task("site", "montage"),
        make_task("truck", "livraison"),
        make_task("draw", "dessin"),
        make_task("odd", "inconnu"),
    ]
    planning = planning_with(*tasks)
    _dialog, page = open_page(qtbot, planning)
    counts: list[int] = []
    page.confirm_preset = lambda count: counts.append(count) is None

    page.preset_button.click()

    assert counts == [5]  # « montage » keeps its key
    assert planning.labels == ["Simplifier les types : Atelier / Montage / Autre"]
    assert planning.steps == [6]
    assert planning.snapshot.meta.task_types == SIMPLE_TASK_TYPES
    assert {task.id: task.task_type for task in planning.snapshot.tasks.values()} == {
        "weld": "atelier",
        "cut": "atelier",
        "site": "montage",
        "truck": "montage",
        "draw": "autre",
        "odd": "autre",
    }
    assert [row.key for row in page.rows] == ["atelier", "montage", "autre"]
    assert page.row("atelier").count_label.text() == "2 tâches"  # type: ignore[union-attr]
    # Grid colours follow the new list.
    assert type_colors(planning.snapshot.meta, "atelier").stripe == "#4f7ea8"

    page.preset_button.click()
    assert len(planning.labels) == 1
    assert "déjà" in page.error_label.text()


def test_simplify_can_be_cancelled(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = planning_with(make_task("weld", "soudure"))
    _dialog, page = open_page(qtbot, planning)
    page.confirm_preset = lambda _count: False
    page.preset_button.click()
    assert planning.labels == []
    assert planning.snapshot.meta.task_types == DEFAULT_TASK_TYPES
