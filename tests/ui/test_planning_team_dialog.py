from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QListWidgetItem, QScrollArea

from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    PlanningCommandError,
    calendar_resolver,
    update_meta,
    update_resource,
)
from projectflow.core.planning.models import (
    Author,
    Closure,
    PlanningMeta,
    RecurringOff,
    Resource,
    Team,
)
from projectflow.ui.dialogs.planning_team import (
    CommandBuilder,
    TeamDialog,
    propose_initials,
    work_week_text,
)
from projectflow.ui.theme import body_font, build_theme, load_fonts, style_sheet

AUTHOR = Author(initials="LM", name="Lionel Mottiez", poste="BUREAU-LM", poste_id="poste-1")
EARLIER = "2026-10-01T08:00:00Z"
NOW = "2026-10-22T10:00:00Z"
MEMBERS = (("m-kd", "Kevin Duc", "kevin.duc@balzmetal.ch"),)


def make_resource(resource_id: str, name: str, initials: str, **fields: object) -> Resource:
    return Resource.model_validate(
        {
            "id": resource_id,
            "name": name,
            "initials": initials,
            "created_at": EARLIER,
            "updated_at": EARLIER,
            **fields,
        },
    )


def roster() -> PlanningSnapshot:
    rows = [
        make_resource("sr", "Samuel Rey", "SR", team="atelier", order=1000),
        make_resource("mb", "Marc Blanc", "MB", team="atelier", order=2000),
        make_resource("nf", "Nicolas Favre", "NF", team="montage", order=1000),
    ]
    return PlanningSnapshot(resources={row.id: row for row in rows}, meta=PlanningMeta())


class FakePlanning:
    """Runs each command on its snapshot, as the planning service would."""

    def __init__(self, snapshot: PlanningSnapshot) -> None:
        self.snapshot = snapshot
        self.labels: list[str] = []

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

    def resource(self, name: str) -> Resource:
        return next(row for row in self.snapshot.resources.values() if row.name == name)

    def change_elsewhere(self, build: CommandBuilder) -> None:
        """Store an edit made on another poste, not yet shown in the dialog."""
        context = CommandContext(
            snapshot=self.snapshot,
            author=AUTHOR,
            now=NOW,
            calendar_for=calendar_resolver(self.snapshot),
        )
        self.snapshot = self.snapshot.apply(build(context))


def open_dialog(qtbot, planning: FakePlanning, members=()) -> TeamDialog:  # type: ignore[no-untyped-def]
    dialog = TeamDialog(lambda: planning.snapshot, planning.apply, members)
    qtbot.addWidget(dialog)
    return dialog


def roster_names(dialog: TeamDialog) -> list[str]:
    widget = dialog.people_page.roster_list
    items = [widget.item(index) for index in range(widget.count())]
    return [item.text() for item in items if isinstance(item, QListWidgetItem)]


def test_add_person_with_recurring_school_day(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page

    page.add_person_button.click()
    page.name_edit.setText("Kevin Duc")
    page.name_edit.textEdited.emit("Kevin Duc")
    page.team_combo.setCurrentIndex(page.team_combo.findData("atelier"))
    page.role_edit.setText("Apprenti 2e")
    page.apprentice_checkbox.setChecked(True)
    page.canton_choice.option("VD").click()
    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(2))
    page.off_part_combo.setCurrentIndex(page.off_part_combo.findData("day"))
    page.off_reason_combo.setCurrentIndex(page.off_reason_combo.findData("ecole"))
    page.off_add_button.click()

    assert planning.labels == []
    assert page.initials_edit.text() == "KD"

    page.create_button.click()

    assert planning.labels == ["Ajouter Kevin Duc"]
    kevin = planning.resource("Kevin Duc")
    assert kevin.initials == "KD"
    assert kevin.team == "atelier"
    assert kevin.role == "Apprenti 2e"
    assert kevin.apprentice is True
    assert kevin.canton == "VD"
    assert kevin.recurring_off == (RecurringOff(weekday=2, part="day", reason="ecole"),)
    assert kevin.created_by == AUTHOR
    # The new row is selected and shown at the end of its team.
    assert page.name_edit.text() == "Kevin Duc"
    assert page.create_button.isHidden()
    assert roster_names(dialog)[:5] == [
        "Bureau technique",
        "Atelier",
        "Samuel Rey",
        "Marc Blanc",
        "Kevin Duc",
    ]


def test_edits_apply_at_once_with_clear_labels(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("mb")

    page.name_edit.setText("Marc Blanchard")
    page.name_edit.editingFinished.emit()
    page.role_edit.setText("Serrurier")
    page.role_edit.editingFinished.emit()
    page.team_combo.setCurrentIndex(page.team_combo.findData("montage"))
    page.canton_choice.option("VD").click()
    page.apprentice_checkbox.setChecked(True)
    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(4))
    page.off_part_combo.setCurrentIndex(page.off_part_combo.findData("morning"))
    page.off_reason_combo.setCurrentIndex(page.off_reason_combo.findData("cie"))
    page.off_add_button.click()

    assert planning.labels == [
        "Renommer Marc Blanc en Marc Blanchard",
        "Fonction de Marc Blanchard : Serrurier",
        "Équipe de Marc Blanchard : Montage",
        "Canton de Marc Blanchard : VD",
        "Marquer Marc Blanchard comme apprenti",
        "Cours CIE de Marc Blanchard le vendredi matin",
    ]
    marc = planning.snapshot.resources["mb"]
    assert (marc.name, marc.role, marc.team, marc.canton, marc.apprentice) == (
        "Marc Blanchard",
        "Serrurier",
        "montage",
        "VD",
        True,
    )
    assert marc.recurring_off == (RecurringOff(weekday=4, part="morning", reason="cie"),)
    assert (
        dialog.status_label.text() == "Enregistré : Cours CIE de Marc Blanchard le vendredi matin"
    )

    page.canton_choice.option("").click()
    assert planning.labels[-1] == "Canton de Marc Blanchard : par défaut (GE)"
    assert planning.snapshot.resources["mb"].canton == ""

    # An unchanged field writes nothing.
    page.name_edit.editingFinished.emit()
    assert len(planning.labels) == 7


def test_recurring_day_overlap_is_refused_and_removal_applies(qtbot) -> None:  # type: ignore[no-untyped-def]
    snapshot = roster()
    school = RecurringOff(weekday=2, part="day", reason="ecole")
    resources = dict(snapshot.resources)
    resources["sr"] = resources["sr"].model_copy(update={"recurring_off": (school,)})
    planning = FakePlanning(PlanningSnapshot(resources=resources, meta=snapshot.meta))
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("sr")

    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(2))
    page.off_part_combo.setCurrentIndex(page.off_part_combo.findData("morning"))
    page.off_add_button.click()

    assert planning.labels == []
    assert "mercredi est déjà indiqué" in page.off_error_label.text()
    assert not page.off_error_label.isHidden()

    remove = page.off_rows.itemAt(0).layout().itemAt(1).widget()  # type: ignore[union-attr]
    remove.click()  # type: ignore[union-attr]

    assert planning.labels == ["Retirer le mercredi de Samuel Rey"]
    assert planning.snapshot.resources["sr"].recurring_off == ()


def test_initials_must_stay_unique(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("mb")

    page.initials_edit.setText("sr")
    page.initials_edit.textEdited.emit("sr")
    page.initials_edit.editingFinished.emit()

    assert planning.labels == []
    assert page.initials_edit.text() == "SR"
    assert "déjà utilisé par Samuel Rey" in page.initials_error_label.text()

    page.initials_edit.setText("MBL")
    page.initials_edit.editingFinished.emit()
    assert planning.labels == ["Initiales de Marc Blanc : MBL"]
    assert page.initials_error_label.isHidden()

    # A new person typed with taken initials gets other ones proposed, and is refused
    # when the user forces taken ones.
    page.add_person_button.click()
    page.name_edit.setText("Sophie Rossier")
    page.name_edit.textEdited.emit("Sophie Rossier")
    assert page.initials_edit.text() == "SRO"
    page.initials_edit.setText("NF")
    page.initials_edit.textEdited.emit("NF")
    page.create_button.click()
    assert planning.labels == ["Initiales de Marc Blanc : MBL"]
    assert "déjà utilisé par Nicolas Favre" in page.initials_error_label.text()


def test_archive_reactivate_and_show_archived(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("sr")

    page.archive_button.click()

    assert planning.labels == ["Archiver Samuel Rey"]
    assert planning.snapshot.resources["sr"].active is False
    assert "Samuel Rey" not in roster_names(dialog)
    # The archived row stays in the form, ready to be reactivated.
    assert not page.reactivate_button.isHidden()
    assert page.archive_button.isHidden()

    page.show_archived_checkbox.setChecked(True)
    assert "Samuel Rey" in roster_names(dialog)

    page.reactivate_button.click()
    assert planning.labels == ["Archiver Samuel Rey", "Réactiver Samuel Rey"]
    assert planning.snapshot.resources["sr"].active is True


def test_reactivation_is_refused_when_initials_were_reused(qtbot) -> None:  # type: ignore[no-untyped-def]
    snapshot = roster()
    resources = dict(snapshot.resources)
    resources["old"] = make_resource("old", "Marcel Bron", "MB", team="atelier", active=False)
    planning = FakePlanning(PlanningSnapshot(resources=resources, meta=snapshot.meta))
    dialog = open_dialog(qtbot, planning)

    dialog.select_resource("old")
    dialog.people_page.reactivate_button.click()

    assert planning.labels == []
    assert "utilisé par Marc Blanc" in dialog.people_page.form_error_label.text()


def test_reorder_within_team(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("mb")

    assert page.up_button.isEnabled()
    assert not page.down_button.isEnabled()
    page.up_button.click()

    assert planning.labels == ["Monter Marc Blanc"]
    atelier = [row.id for row in planning.snapshot.active_resources() if row.team == "atelier"]
    assert atelier == ["mb", "sr"]
    assert not page.up_button.isEnabled()

    page.down_button.click()
    assert planning.labels[-1] == "Descendre Marc Blanc"
    atelier = [row.id for row in planning.snapshot.active_resources() if row.team == "atelier"]
    assert atelier == ["sr", "mb"]


def test_add_from_microsoft_365_prefills_the_person(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning, MEMBERS)
    page = dialog.people_page

    assert page.members_button.isEnabled()
    page.members_menu.actions()[0].trigger()
    assert page.name_edit.text() == "Kevin Duc"
    assert page.initials_edit.text() == "KD"
    page.create_button.click()

    assert planning.labels == ["Ajouter Kevin Duc"]
    assert planning.resource("Kevin Duc").m365_user_id == "m-kd"
    action = page.members_menu.actions()[0]
    assert not action.isEnabled()
    assert "déjà dans le planning" in action.text()


def test_add_subcontractor_goes_to_its_team(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page

    page.add_subcontractor_button.click()
    page.name_edit.setText("Galvaswiss")
    page.name_edit.textEdited.emit("Galvaswiss")
    assert page.apprentice_checkbox.isHidden()
    page.create_button.click()

    galva = planning.resource("Galvaswiss")
    assert galva.kind_of_resource == "subcontractor"
    assert galva.team == "soustraitants"
    assert galva.initials == "G"


def test_empty_name_is_refused_inline(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page

    page.add_person_button.click()
    page.create_button.click()

    assert planning.labels == []
    assert page.form_error_label.text() == "Le nom est obligatoire."


def test_teams_rename_alias_add_move_and_delete(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.teams_page

    atelier = next(row for row in page.rows if row.key == "atelier")
    atelier.name_edit.setText("Atelier serrurerie")
    atelier.name_edit.editingFinished.emit()
    atelier.alias_edit.setText("+Serrurerie")
    atelier.alias_edit.editingFinished.emit()
    page.new_team_edit.setText("Apprentis")
    page.add_button.click()

    assert planning.labels == [
        "Renommer l'équipe Atelier en Atelier serrurerie",
        "Alias de l'équipe Atelier serrurerie : +serrurerie",
        "Ajouter l'équipe Apprentis",
    ]
    team = planning.snapshot.meta.team("atelier")
    assert team is not None
    assert (team.name, team.alias) == ("Atelier serrurerie", "serrurerie")
    added = planning.snapshot.meta.team("apprentis")
    assert added is not None
    assert (added.name, added.alias) == ("Apprentis", "apprentis")
    assert [row.key for row in page.rows][-1] == "apprentis"
    assert page.new_team_edit.text() == ""

    apprentis = page.rows[-1]
    apprentis.up_button.click()
    assert planning.labels[-1] == "Monter l'équipe Apprentis"
    assert [row.key for row in page.rows] == [
        "bureau",
        "atelier",
        "montage",
        "apprentis",
        "soustraitants",
    ]
    orders = [team.order for team in planning.snapshot.meta.teams]
    assert len(set(orders)) == len(orders)

    used = next(row for row in page.rows if row.key == "montage")
    assert not used.delete_button.isEnabled()
    apprentis = next(row for row in page.rows if row.key == "apprentis")
    apprentis.delete_button.click()
    assert planning.labels[-1] == "Supprimer l'équipe Apprentis"
    assert planning.snapshot.meta.team("apprentis") is None


def test_team_alias_and_name_are_checked(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.teams_page
    montage = next(row for row in page.rows if row.key == "montage")

    montage.alias_edit.setText("atelier")
    montage.alias_edit.editingFinished.emit()
    assert "déjà pris par Atelier" in page.error_label.text()
    montage.alias_edit.setText("monte et pose")
    montage.alias_edit.editingFinished.emit()
    assert "sans espace" in page.error_label.text()
    page.new_team_edit.setText("atelier")
    page.add_button.click()
    assert "s'appelle déjà" in page.error_label.text()
    assert planning.labels == []


def test_calendar_canton_closures_and_auto_card(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.calendar_page

    assert page.hours_label.text() == (
        "Lun-jeu 06:30-12:00 · 13:00-16:30, ven 06:30-12:00 (41 h 30)"
    )
    page.canton_choice.option("VD").click()
    page.closure_first_edit.setDate(QDate(2026, 12, 21))
    page.closure_last_edit.setDate(QDate(2027, 1, 1))
    page.closure_label_edit.setText("Fin d'année")
    page.closure_add_button.click()
    page.auto_card_checkbox.setChecked(False)

    assert planning.labels == [
        "Canton par défaut : VD",
        "Ajouter la fermeture Fin d'année",
        "Désactiver la carte automatique à la sortie dossier",
    ]
    meta = planning.snapshot.meta
    assert meta.default_canton == "VD"
    assert meta.closures == (
        Closure(first_day=date(2026, 12, 21), last_day=date(2027, 1, 1), label="Fin d'année"),
    )
    assert meta.auto_card_on_sortie is False
    assert dialog.people_page.canton_choice.option("").text() == "Par défaut (VD)"

    remove = page.closure_rows.itemAt(0).layout().itemAt(1).widget()  # type: ignore[union-attr]
    remove.click()  # type: ignore[union-attr]
    assert planning.labels[-1] == "Retirer la fermeture Fin d'année"
    assert planning.snapshot.meta.closures == ()


def test_closure_dates_follow_each_other(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.calendar_page

    page.closure_first_edit.setDate(QDate(2027, 7, 26))
    assert page.closure_last_edit.date() == QDate(2027, 7, 26)
    page.closure_last_edit.setDate(QDate(2027, 7, 20))
    page.closure_add_button.click()

    assert planning.labels == []
    assert page.closure_error_label.text() == "La fin de la fermeture précède son début."


def test_refused_command_is_shown_inline(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())

    def refuse(_build: CommandBuilder, _label: str) -> None:
        raise PlanningCommandError("Planning en lecture seule (hors ligne).")

    dialog = TeamDialog(lambda: planning.snapshot, refuse)
    qtbot.addWidget(dialog)
    page = dialog.people_page
    dialog.select_resource("sr")

    page.canton_choice.option("VD").click()

    assert page.form_error_label.text() == "Planning en lecture seule (hors ligne)."
    assert page.canton_choice.current() == ""
    assert dialog.status_label.text() == ""

    # The company canton too goes back to the stored one.
    calendar = dialog.calendar_page
    calendar.canton_choice.option("VD").click()

    assert calendar.canton_error_label.text() == "Planning en lecture seule (hors ligne)."
    assert calendar.canton_choice.current() == "GE"


def test_enter_in_a_field_does_not_close_the_dialog(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    dialog.show()
    dialog.select_resource("sr")
    dialog.people_page.role_edit.setFocus()

    qtbot.keyClicks(dialog.people_page.role_edit, "Chef d'atelier")
    qtbot.keyClick(dialog.people_page.role_edit, "\r")

    assert dialog.isVisible()
    assert planning.labels == ["Fonction de Samuel Rey : Chef d'atelier"]


def test_navigation_meta_counts(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)

    dialog.show_page("calendar")

    assert dialog.pages.currentIndex() == 4
    keys = ("people", "teams", "types", "calendar")
    texts = [dialog._page_meta[key].text() for key in keys]  # noqa: SLF001
    assert texts == ["3", "4", "10", "GE"]


def test_recurring_days_build_on_the_stored_ones(qtbot) -> None:  # type: ignore[no-untyped-def]
    snapshot = roster()
    cie = RecurringOff(weekday=1, part="morning", reason="cie")
    resources = dict(snapshot.resources)
    resources["sr"] = resources["sr"].model_copy(update={"recurring_off": (cie,)})
    planning = FakePlanning(PlanningSnapshot(resources=resources, meta=snapshot.meta))
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("sr")
    school = RecurringOff(weekday=2, part="day", reason="ecole")
    planning.change_elsewhere(lambda ctx: update_resource(ctx, "sr", recurring_off=(school, cie)))

    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(4))
    page.off_part_combo.setCurrentIndex(page.off_part_combo.findData("morning"))
    page.off_reason_combo.setCurrentIndex(page.off_reason_combo.findData("cie"))
    page.off_add_button.click()

    friday = RecurringOff(weekday=4, part="morning", reason="cie")
    assert planning.snapshot.resources["sr"].recurring_off == (cie, school, friday)

    # Removal takes out the day clicked, even when the stored list has moved meanwhile.
    monday = RecurringOff(weekday=0, part="day", reason="formation")
    planning.change_elsewhere(
        lambda ctx: update_resource(ctx, "sr", recurring_off=(monday, cie, school, friday)),
    )
    remove = page.off_rows.itemAt(0).layout().itemAt(1).widget()  # type: ignore[union-attr]
    remove.click()  # type: ignore[union-attr]

    assert planning.labels[-1] == "Retirer le mardi matin de Samuel Rey"
    assert planning.snapshot.resources["sr"].recurring_off == (monday, school, friday)


def test_friday_has_no_afternoon_to_take_off(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("sr")
    model = page.off_part_combo.model()
    assert isinstance(model, QStandardItemModel)
    afternoon = page.off_part_combo.findData("afternoon")
    page.off_part_combo.setCurrentIndex(afternoon)

    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(4))

    assert not model.item(afternoon).isEnabled()
    assert page.off_part_combo.currentData() == "day"
    page.off_part_combo.setCurrentIndex(afternoon)
    page.off_add_button.click()
    assert planning.labels == []
    assert page.off_error_label.text() == "Le vendredi n'est pas travaillé l'après-midi."

    page.off_weekday_combo.setCurrentIndex(page.off_weekday_combo.findData(3))
    assert model.item(afternoon).isEnabled()


def test_initials_differing_by_an_accent_are_taken(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("mb")

    page.initials_edit.setText("ŠR")
    page.initials_edit.editingFinished.emit()

    assert planning.labels == []
    assert "déjà utilisé par Samuel Rey" in page.initials_error_label.text()


def test_team_typed_while_another_poste_adds_a_team(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.show_page("teams")
    page = dialog.teams_page
    edit = next(row for row in page.rows if row.key == "atelier").name_edit
    edit.setFocus()
    qtbot.waitUntil(edit.hasFocus)
    qtbot.keyClicks(edit, " X")
    painting = Team(key="peinture", name="Peinture", order=9)
    planning.change_elsewhere(
        lambda ctx: update_meta(ctx, teams=(*ctx.snapshot.meta.teams, painting)),
    )

    dialog.refresh()

    # Nothing is committed in mid-refresh; the text typed stays in the new row.
    assert planning.labels == []
    assert [row.key for row in page.rows][-1] == "peinture"
    edit = next(row for row in page.rows if row.key == "atelier").name_edit
    assert edit.text() == "Atelier X"
    assert edit.hasFocus()
    assert dialog.snapshot is planning.snapshot

    dialog.close_button.setFocus()
    assert planning.labels == ["Renommer l'équipe Atelier en Atelier X"]
    assert edit.text() == "Atelier X"


def test_refresh_asked_during_a_refresh_runs_after_it(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    provider: list[Callable[[], PlanningSnapshot]] = [lambda: planning.snapshot]

    def current() -> PlanningSnapshot:
        return provider[0]()

    dialog = TeamDialog(current, planning.apply)
    qtbot.addWidget(dialog)
    shown = planning.snapshot

    def nested() -> PlanningSnapshot:
        # An edit committed while the pages are rebuilt asks for its own refresh.
        provider[0] = lambda: planning.snapshot
        planning.change_elsewhere(lambda ctx: update_resource(ctx, "sr", name="Samuel Reymond"))
        dialog.refresh()
        return shown

    provider[0] = nested
    dialog.refresh()

    assert dialog.snapshot is planning.snapshot
    assert "Samuel Reymond" in roster_names(dialog)


def test_refresh_keeps_the_roster_scroll_and_skips_unchanged_rows(qtbot) -> None:  # type: ignore[no-untyped-def]
    rows = [
        make_resource(f"r{number:02d}", f"Personne {number:02d}", f"P{number:02d}")
        for number in range(25)
    ]
    rows[2] = rows[2].model_copy(
        update={"recurring_off": (RecurringOff(weekday=2, part="day", reason="ecole"),)},
    )
    planning = FakePlanning(
        PlanningSnapshot(resources={row.id: row for row in rows}, meta=PlanningMeta()),
    )
    dialog = open_dialog(qtbot, planning)
    dialog.show()
    qtbot.waitExposed(dialog)
    page = dialog.people_page
    dialog.select_resource("r02")
    bar = page.roster_list.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum())

    # A change received from another poste: the list stays where the user scrolled.
    planning.change_elsewhere(lambda ctx: update_resource(ctx, "r20", role="Serrurier"))
    dialog.refresh()
    assert bar.value() == bar.maximum()
    assert page.roster_list.currentItem().data(Qt.ItemDataRole.UserRole) == "r02"

    # Only tasks changed: nothing is rebuilt, the focus stays on its remove button.
    remove = page.off_rows.itemAt(0).layout().itemAt(1).widget()  # type: ignore[union-attr]
    planning.snapshot = PlanningSnapshot(
        resources=dict(planning.snapshot.resources),
        meta=planning.snapshot.meta,
    )
    dialog.refresh()
    assert page.off_rows.itemAt(0).layout().itemAt(1).widget() is remove  # type: ignore[union-attr]
    assert dialog.snapshot is planning.snapshot


def test_long_name_never_widens_the_roster(qtbot) -> None:  # type: ignore[no-untyped-def]
    snapshot = roster()
    resources = dict(snapshot.resources)
    resources["mc"] = make_resource(
        "mc",
        "Marie-Christine de Montmollin-Vuilleumier",
        "MCM",
        team="atelier",
    )
    planning = FakePlanning(PlanningSnapshot(resources=resources, meta=snapshot.meta))
    dialog = open_dialog(qtbot, planning)
    dialog.show()
    qtbot.waitExposed(dialog)
    roster_list = dialog.people_page.roster_list
    qtbot.waitUntil(lambda: roster_list.viewport().width() > 0)

    width = roster_list.viewport().width()
    assert roster_list.horizontalScrollBar().maximum() == 0
    assert all(
        roster_list.visualItemRect(roster_list.item(index)).width() <= width
        for index in range(roster_list.count())
    )


def test_people_form_fits_at_the_minimum_size(qtbot) -> None:  # type: ignore[no-untyped-def]
    snapshot = roster()
    resources = dict(snapshot.resources)
    resources["sr"] = resources["sr"].model_copy(update={"apprentice": True})
    planning = FakePlanning(PlanningSnapshot(resources=resources, meta=snapshot.meta))
    dialog = open_dialog(qtbot, planning)
    # The application's look, on this dialog only: the theme is not changed for the
    # widgets of other tests.
    load_fonts()
    dialog.setFont(body_font(14))
    dialog.setStyleSheet(style_sheet(build_theme("light", "acier")))
    dialog.resize(dialog.minimumSize())
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.select_resource("sr")
    area = dialog.people_page.form_stack.currentWidget()
    assert isinstance(area, QScrollArea)
    form = area.widget()
    assert form is not None

    # Once laid out (the hidden buttons no longer count), the form needs no scrolling.
    qtbot.waitUntil(lambda: area.viewport().width() >= form.minimumSizeHint().width())
    assert area.horizontalScrollBar().maximum() == 0


def test_enter_presses_the_focused_button_and_adds_a_new_row(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    dialog.show()
    qtbot.waitExposed(dialog)
    page = dialog.people_page
    dialog.select_resource("sr")
    page.archive_button.setFocus()
    qtbot.waitUntil(page.archive_button.hasFocus)

    qtbot.keyClick(page.archive_button, Qt.Key.Key_Return)

    qtbot.waitUntil(lambda: planning.labels == ["Archiver Samuel Rey"])
    assert dialog.isVisible()

    page.add_person_button.click()
    page.name_edit.setFocus()
    qtbot.keyClicks(page.name_edit, "Anne Roux")
    qtbot.keyClick(page.name_edit, Qt.Key.Key_Return)

    assert planning.labels[-1] == "Ajouter Anne Roux"
    assert page.create_button.isHidden()
    assert page.name_edit.text() == "Anne Roux"
    assert len(planning.labels) == 2


def test_selecting_another_row_commits_the_text_typed_first(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = FakePlanning(roster())
    dialog = open_dialog(qtbot, planning)
    page = dialog.people_page
    dialog.select_resource("sr")
    page.name_edit.setText("Samuel Reymond")
    page.name_edit.setModified(True)

    dialog.select_resource("mb")

    assert planning.labels == ["Renommer Samuel Rey en Samuel Reymond"]
    assert page.name_edit.text() == "Marc Blanc"
    assert planning.snapshot.resources["mb"].name == "Marc Blanc"


@pytest.mark.parametrize(
    ("name", "taken", "expected"),
    [
        ("Kevin Duc", (), "KD"),
        ("kevin duc", ("kd",), "KDU"),
        ("Kevin Duc", ("KD", "KDU"), "KED"),
        ("Jean-Pierre Favre", (), "JPF"),
        ("Élodie Ançay", (), "EA"),
        ("Galvaswiss", (), "G"),
        ("Galvaswiss", ("G", "GA", "GAL"), "G2"),
        ("Samuel Rey", ("ŠR",), "SRE"),
        ("  ", (), ""),
    ],
)
def test_propose_initials(name: str, taken: tuple[str, ...], expected: str) -> None:
    assert propose_initials(name, taken) == expected


def test_work_week_text_groups_equal_days() -> None:
    week = {0: ((420, 720), (780, 1020)), 1: ((420, 720), (780, 1020)), 4: ((420, 720),)}

    assert work_week_text(week) == "Lun-mar 07:00-12:00 · 13:00-17:00, ven 07:00-12:00 (23 h)"
