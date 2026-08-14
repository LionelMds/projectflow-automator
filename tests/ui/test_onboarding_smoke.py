from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QSizePolicy, QSystemTrayIcon

from projectflow.config import AppConfig
from projectflow.core.client_directory import ClientDirectory
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.core.sortie_service import OutputCandidate, OutputInventory
from projectflow.ui.creation_tab import CreationFormData, CreationTab
from projectflow.ui.dialogs.quick_confirmation import QuickCreationConfirmationDialog
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard
from projectflow.ui.repertoire_tab import RepertoireDossierTab
from projectflow.ui.sortie_tab import SortieDossierTab
from projectflow.ui.tray import ProjectFlowTray
from projectflow.ui.widgets.planner import PlannerTaskFormData


def test_onboarding_wizard_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)

    assert wizard.page(0) is not None


def test_onboarding_applies_local_paths(qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    wizard = OnboardingWizard(AppConfig())
    qtbot.addWidget(wizard)
    wizard.paths_page.racine_edit.setText(str(tmp_path / "clients"))
    wizard.paths_page.reference_edit.setText(str(tmp_path / "reference"))
    wizard.paths_page.repertoire_edit.setText(str(tmp_path / "repertoire.xlsx"))

    wizard.paths_page.apply_to_config()

    assert wizard.config.paths.racine_projets == tmp_path / "clients"
    assert wizard.config.paths.dossier_reference == tmp_path / "reference"
    assert wizard.config.paths.repertoire_chantier.display_path == str(tmp_path / "repertoire.xlsx")


def test_main_window_smoke(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)

    assert window.windowTitle() == "ProjectFlow Automator - Balz Metal Sa"
    assert window.sortie_tab is not None
    assert window.repertoire_tab is not None


def test_repertoire_tab_positions_near_next_available_and_filters(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = RepertoireDossierTab()
    qtbot.addWidget(tab)
    tab.set_year(2026)
    snapshot = RepertoireSnapshot(
        year=2026,
        rows=tuple(
            RepertoireRow(
                row_index=index,
                values=(f"2026-{4990 + index}", "", "", "", ""),
            )
            for index in range(8)
        ),
        next_available=NextAvailableProject(
            number=parse_project_number("2026-4997"),
            row_index=7,
        ),
    )

    tab.set_snapshot(snapshot)

    selected = tab.selected_row_payload()
    assert selected is not None
    assert selected[0] == 7
    assert "2026-4997" in tab.status_label.text()

    tab.search_edit.setText("2026-4992")
    assert tab.table.model().rowCount() == 1
    assert tab.sync_project_button.text() == "Mettre à jour le projet"

    tab.search_edit.clear()
    proxy_index = tab.table.model().index(0, 4)
    assert tab.table.model().setData(proxy_index, "Modifie", Qt.ItemDataRole.EditRole)
    source_index = tab._proxy.mapToSource(proxy_index)  # noqa: SLF001
    dirty_background = tab._model.data(  # noqa: SLF001
        source_index,
        Qt.ItemDataRole.BackgroundRole,
    )
    assert isinstance(dirty_background, QColor)

    tab.mark_saved(0, ("2026-4990", "", "", "", "Modifie"))
    assert (
        tab._model.data(  # noqa: SLF001
            source_index,
            Qt.ItemDataRole.BackgroundRole,
        )
        is None
    )
    assert tab.open_project_button.text() == "Charger le projet"
    assert tab.create_subproject_button.text() == "Créer sous-projet"
    assert tab.duplicate_project_button.text() == "Dupliquer"
    assert tab.delete_project_button.text() == "Supprimer avec éléments liés"


def test_sortie_tab_has_browse_controls(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = SortieDossierTab()
    qtbot.addWidget(tab)

    assert tab.create_output_button.text() == "Creer dossier de sortie"
    assert not tab.create_output_button.isEnabled()
    assert tab.photo_browse_button.text() == "Parcourir"
    assert tab.plan_browse_button.text() == "Parcourir"


def test_sortie_tab_browses_from_project_subfolders_and_previews_photo(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    tab = SortieDossierTab()
    qtbot.addWidget(tab)
    fiche = tmp_path / "fiche.xlsx"
    mesure = tmp_path / "cote.pdf"
    photo_dir = tmp_path / "photos"
    photo_dir.mkdir()
    photo = photo_dir / "photo.png"
    image = QImage(30, 20, QImage.Format.Format_RGB32)
    image.fill("#2D6CDF")
    assert image.save(str(photo))
    plan_dir = tmp_path / "Plans" / "Plan d'execution"
    plan_dir.mkdir(parents=True)
    plan = plan_dir / "plan.pdf"
    for path in (fiche, mesure, plan):
        path.touch()

    def candidate(path: Path) -> OutputCandidate:
        return OutputCandidate(path=path, size_bytes=path.stat().st_size, modified_timestamp=0)

    tab.set_project_directory(tmp_path)
    tab.set_inventory(
        OutputInventory(
            fiches=(candidate(fiche),),
            mesure_pdfs=(candidate(mesure),),
            photos=(candidate(photo),),
            plans=(candidate(plan),),
        ),
    )
    dialog_directories: list[str] = []
    dialog_results = [([str(photo)], ""), ([str(plan)], "")]

    def fake_get_open_file_names(*args: Any) -> tuple[list[str], str]:
        dialog_directories.append(args[2])
        return dialog_results.pop(0)

    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileNames",
        fake_get_open_file_names,
    )
    tab.photo_browse_button.click()
    tab.plan_browse_button.click()
    tab.mesure_list.setCurrentRow(0)

    data = tab.data()

    assert data.fiche_path == fiche.resolve()
    assert data.mesure_pdf_path == mesure.resolve()
    assert data.photo_paths == (photo.resolve(),)
    assert data.plan_paths == (plan.resolve(),)
    assert dialog_directories == [str(photo_dir), str(plan_dir)]
    assert tab.photo_preview.pixmap() is not None
    assert not tab.photo_preview.pixmap().isNull()


def test_creation_tab_uses_expanding_field_widths(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)

    assert tab.minimumWidth() >= 760
    assert tab.project_id_edit.minimumWidth() > tab.year_combo.minimumWidth()
    assert tab.designation_edit.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


def test_creation_tab_normalizes_company_and_filters_contacts(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)
    tab.set_client_directory(
        ClientDirectory.from_repertoire_rows(
            [
                ("2026-5000", "", "Métal SA", "Élodie Martin", "Projet"),
                ("2026-5001", "", "Autre Client", "Jean Dupont", "Projet"),
            ]
        )
    )

    tab.societe_edit.setText("metal sa")
    tab.societe_edit.editingFinished.emit()
    tab.contact_edit.setText("elodie martin")
    tab.contact_edit.editingFinished.emit()

    assert tab.societe_edit.text() == "Métal SA"
    assert tab.contact_edit.text() == "Élodie Martin"
    contact_model = tab.contact_edit.completer().model()
    assert contact_model.rowCount() == 1
    assert contact_model.index(0, 0).data() == "Élodie Martin"


def test_quick_create_uses_same_client_directory(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)
    dialog.set_client_directory(
        ClientDirectory.from_repertoire_rows(
            [("2026-5000", "", "Balz Metal SA", "Lionel", "Projet")]
        )
    )

    company_model = dialog.societe_edit.completer().model()

    assert company_model.rowCount() == 1
    assert company_model.index(0, 0).data() == "Balz Metal SA"


def test_creation_tab_reset_button_clears_form_fields_only(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)
    tab.year_combo.addItem("2026")
    tab.year_combo.setCurrentText("2026")
    tab.project_id_edit.setText("4995")
    tab.subproject_edit.setText("2")
    tab.designation_edit.setText("Escalier")
    tab.societe_edit.setText("Balz")
    tab.contact_edit.setText("Lionel")
    tab.localisation_edit.setText("Zurich")
    tab.gere_par_edit.setText("LM")
    tab.append_log("+ Log conserve")

    tab.reset_button.click()

    assert tab.year_combo.currentText() == "2026"
    assert tab.project_id_edit.text() == ""
    assert tab.subproject_edit.text() == ""
    assert tab.designation_edit.text() == ""
    assert tab.societe_edit.text() == ""
    assert tab.contact_edit.text() == ""
    assert tab.localisation_edit.text() == ""
    assert tab.gere_par_edit.text() == ""
    assert "+ Log conserve" in tab.logs.toPlainText()


def test_creation_tab_can_apply_quick_form_data(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)

    tab.set_form_data(
        CreationFormData(
            year="2027",
            project_id="6001",
            subproject_id="2",
            designation="Escalier rapide",
            societe="Balz",
            contact="Lionel",
            localisation="Geneve",
            gere_par="LM",
        ),
    )

    assert tab.year_combo.currentText() == "2027"
    assert tab.project_id_edit.text() == "6001"
    assert tab.subproject_edit.text() == "2"
    assert tab.designation_edit.text() == "Escalier rapide"
    assert tab.societe_edit.text() == "Balz"
    assert tab.contact_edit.text() == "Lionel"
    assert tab.localisation_edit.text() == "Geneve"
    assert tab.gere_par_edit.text() == "LM"


def test_creation_tab_round_trips_planner_options(qtbot) -> None:  # type: ignore[no-untyped-def]
    tab = CreationTab()
    qtbot.addWidget(tab)
    config = AppConfig()
    config.planner.enabled = True
    config.planner.bucket_id = "bucket-id"
    config.planner.bucket_name = "A faire"
    tab.apply_planner_config(config.planner)

    data = CreationFormData(
        year="2027",
        project_id="6001",
        subproject_id="",
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Geneve",
        gere_par="LM",
        planner=PlannerTaskFormData(
            enabled=True,
            bucket_id="bucket-id",
            bucket_name="A faire",
            assignee_ids=("user-id",),
            assignee_labels=("Lionel",),
            due_enabled=True,
            due_days=5,
        ),
    )

    tab.set_form_data(data)

    assert tab.data() == data


def test_quick_create_dialog_round_trips_form_data(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)

    dialog.set_data(
        CreationFormData(
            year="2028",
            project_id="7001",
            subproject_id="",
            designation="Garde-corps",
            societe="Client",
            contact="Contact",
            localisation="Lausanne",
            gere_par="AB",
        ),
    )

    assert dialog.data() == CreationFormData(
        year="2028",
        project_id="7001",
        subproject_id="",
        designation="Garde-corps",
        societe="Client",
        contact="Contact",
        localisation="Lausanne",
        gere_par="AB",
    )


def test_quick_create_dialog_round_trips_planner_options(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)
    dialog.apply_planner_config(
        enabled=True,
        bucket_id="bucket-id",
        bucket_name="A faire",
        due_days=7,
    )
    data = CreationFormData(
        year="2028",
        project_id="7001",
        subproject_id="",
        designation="Garde-corps",
        societe="Client",
        contact="Contact",
        localisation="Lausanne",
        gere_par="AB",
        planner=PlannerTaskFormData(
            enabled=True,
            bucket_id="bucket-id",
            bucket_name="A faire",
            assignee_ids=("user-id",),
            assignee_labels=("Lionel",),
            due_enabled=True,
            due_days=12,
        ),
    )

    dialog.set_data(data)

    assert dialog.data() == data


def test_quick_create_dialog_updates_project_identity(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)

    dialog.set_project_identity(year="2029", project_id="8001")

    assert dialog.year_combo.currentText() == "2029"
    assert dialog.project_id_edit.text() == "8001"
    assert dialog.subproject_edit.text() == ""


def test_quick_create_dialog_emits_navigation_signals(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreateDialog()
    qtbot.addWidget(dialog)
    emitted: list[str] = []
    dialog.classic_requested.connect(lambda: emitted.append("classic"))
    dialog.next_available_requested.connect(lambda: emitted.append("next"))

    dialog.classic_button.click()
    dialog.next_available_button.click()

    assert emitted == ["classic", "next"]


def test_quick_confirmation_keeps_dialog_open_for_file_actions(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreationConfirmationDialog(
        title="Projet cree",
        message="Le projet a ete cree avec succes.",
        project_dir="C:/tmp/2026-4995",
    )
    qtbot.addWidget(dialog)
    emitted: list[str] = []
    dialog.open_fiche_requested.connect(lambda: emitted.append("fiche"))
    dialog.open_repertoire_requested.connect(lambda: emitted.append("repertoire"))
    dialog.show()

    dialog.open_fiche_button.click()
    dialog.open_repertoire_button.click()

    assert dialog.isVisible()
    assert emitted == ["fiche", "repertoire"]
    assert dialog.selected_action() is None


def test_quick_confirmation_closes_for_next_or_edit(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = QuickCreationConfirmationDialog(
        title="Projet cree",
        message="Le projet a ete cree avec succes.",
        project_dir="C:/tmp/2026-4995",
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.next_button.click()

    assert dialog.selected_action() == "next"
    assert not dialog.isVisible()


def test_tray_single_click_opens_quick_create() -> None:
    tray = ProjectFlowTray(icon=QIcon())
    emitted: list[str] = []
    tray.quick_create_requested.connect(lambda: emitted.append("quick"))
    tray.show_requested.connect(lambda: emitted.append("show"))

    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)  # noqa: SLF001
    tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)  # noqa: SLF001

    assert emitted == ["quick", "show"]


def test_tray_context_click_only_opens_menu(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tray = ProjectFlowTray(icon=QIcon())
    emitted: list[str] = []
    menus: list[bool] = []
    tray.quick_create_requested.connect(lambda: emitted.append("quick"))
    tray.show_requested.connect(lambda: emitted.append("show"))
    monkeypatch.setattr(tray, "_show_context_menu", lambda: menus.append(True))

    tray._on_activated(QSystemTrayIcon.ActivationReason.Context)  # noqa: SLF001

    assert emitted == []
    assert menus == [True]


def test_main_window_close_hides_when_background_mode_enabled(qtbot) -> None:  # type: ignore[no-untyped-def]
    window = MainWindow(AppConfig())
    qtbot.addWidget(window)
    window.set_background_mode_enabled(enabled=True)
    hidden = []
    window.hidden_to_background.connect(lambda: hidden.append(True))
    window.show()

    window.close()

    assert hidden == [True]
    assert not window.isVisible()
