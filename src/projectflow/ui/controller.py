from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtWidgets import QApplication, QMessageBox

from projectflow import __version__
from projectflow.application_settings import ApplicationSettings
from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService, standard_fiche_path
from projectflow.core.models import ProjectCreationResult, ProjectInput
from projectflow.core.numero import format_project_number, parse_project_number, project_folder_name
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ProjectFlowError
from projectflow.platform.filemanager import open_file_default_app, open_path
from projectflow.services import ServiceContainer
from projectflow.ui.dialogs.fiche_selection import FicheSelectionDialog
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow
from projectflow.updates import (
    GitHubReleaseChecker,
    UpdateDownloader,
    launch_install_plan,
    prepare_install_plan,
    select_checksum_asset,
    select_platform_asset,
)


class ServiceProvider(Protocol):
    def fiche(self) -> FicheService:
        """Return fiche service."""

    def repertoire(self) -> RepertoireService:
        """Return repertoire service."""

    def project(self) -> ProjectService:
        """Return project service."""


class ProjectFlowController:
    def __init__(
        self,
        *,
        window: MainWindow,
        config: AppConfig,
        services: ServiceProvider | None = None,
        save_config: Callable[[], None] | None = None,
    ) -> None:
        self._window = window
        self._config = config
        self._services = services or ServiceContainer(config)
        self._save_config = save_config
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._quick_dialog: QuickCreateDialog | None = None
        self._connect()

    def _connect(self) -> None:
        tab = self._window.creation_tab
        tab.create_requested.connect(lambda: asyncio.create_task(self.create_project()))
        self._window.update_confirmed.connect(lambda: asyncio.create_task(self.update_project()))
        tab.load_requested.connect(self.load_project)
        tab.open_fiche_requested.connect(self.open_fiche)
        tab.open_repertoire_requested.connect(self.open_repertoire)
        tab.next_available_requested.connect(lambda: asyncio.create_task(self.next_available()))
        self._window.settings_requested.connect(self.open_settings)
        self._window.update_check_requested.connect(
            lambda: asyncio.create_task(self.check_updates()),
        )

    def show_window(self) -> None:
        self._window.show_and_raise()

    def show_quick_create(self) -> None:
        if self._quick_dialog is not None:
            self._quick_dialog.show_and_raise()
            return
        dialog = QuickCreateDialog(parent=self._window)
        self._quick_dialog = dialog
        dialog.set_data(self._window.creation_tab.data())
        dialog.classic_requested.connect(lambda: self._show_classic_from_quick(dialog))
        dialog.next_available_requested.connect(
            lambda: self._schedule_task(self._quick_next_available(dialog)),
        )
        try:
            result = dialog.exec()
        finally:
            if self._quick_dialog is dialog:
                self._quick_dialog = None
        if result != dialog.DialogCode.Accepted:
            return
        self._window.creation_tab.set_form_data(dialog.data())
        self._window.show_and_raise()
        self._schedule_task(self.create_project())

    def _show_classic_from_quick(self, dialog: QuickCreateDialog) -> None:
        self._window.creation_tab.set_form_data(dialog.data())
        dialog.reject()
        self._window.show_and_raise()

    async def _quick_next_available(self, dialog: QuickCreateDialog) -> None:
        try:
            year = int(dialog.data().year)
            result = await self._services.repertoire().next_available(year=year)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if result is None:
            self._log("! Aucun numero disponible trouve")
            return
        dialog.set_project_identity(
            year=str(result.number.year),
            project_id=result.number.project_id,
        )
        self._window.creation_tab.set_project_identity(
            year=str(result.number.year),
            project_id=result.number.project_id,
        )
        self._save_config_if_available()
        self._log(f"+ Numero disponible: {result.number}")

    async def create_project(self) -> None:
        try:
            project = self._project_from_form()
            project_exists = (
                not project.is_subproject and self._project_dir(project.number).exists()
            )
            existing_update = self._existing_project_update_decision(project)
            if existing_update is None:
                self._log("! Creation annulee")
                return
            result = await self._services.project().create_project(
                project,
                force_overwrite=project_exists and existing_update,
                update_existing_info=existing_update,
            )
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        self._save_config_if_available()
        if result.project_dir_created:
            self._log(f"+ Projet cree: {result.project_dir}")
        else:
            self._log(f"+ Projet existant reapplique: {result.project_dir}")
        if not result.project_dir_created and not existing_update:
            self._log("+ Informations existantes conservees")
        self._log_creation_integrations(result)
        self._open_project_folder(result)
        self._show_creation_confirmation(result)

    async def update_project(self) -> None:
        try:
            project = self._project_from_form()
            result = await self._services.project().update_project(project)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        self._save_config_if_available()
        self._log(f"+ Projet mis a jour: {result.fiche_path or result.project_dir}")

    async def next_available(self) -> None:
        try:
            year = int(self._window.creation_tab.data().year)
            result = await self._services.repertoire().next_available(year=year)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if result is None:
            self._log("! Aucun numero disponible trouve")
            return
        self._window.creation_tab.set_project_identity(
            year=str(result.number.year),
            project_id=result.number.project_id,
        )
        self._save_config_if_available()
        self._log(f"+ Numero disponible: {result.number}")

    def load_project(self) -> None:
        try:
            number = parse_project_number(self._number_from_form())
            project_dir = self._project_dir(number)
            fiche_path = self._choose_fiche(project_dir)
            data = self._services.fiche().read_fiche(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return

        self._window.creation_tab.designation_edit.setText(data.designation)
        self._window.creation_tab.societe_edit.setText(data.societe)
        self._window.creation_tab.contact_edit.setText(data.contact)
        self._window.creation_tab.localisation_edit.setText(data.localisation)
        self._window.creation_tab.gere_par_edit.setText(data.gere_par)
        if data.number and data.number != str(number):
            self._log(f"! C3 contient {data.number}, attendu {number}")
        self._log(f"+ Fiche chargee: {fiche_path.name}")

    def open_fiche(self) -> None:
        try:
            number = parse_project_number(self._number_from_form())
            project_dir = self._project_dir(number)
            fiche_path = standard_fiche_path(project_dir, number)
            if not fiche_path.exists():
                fiche_path = self._services.fiche().locate_fiche(project_dir)
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def open_repertoire(self) -> None:
        display_path = self._config.paths.repertoire_chantier.display_path.strip()
        if not display_path:
            self._error("Repertoire chantier non configure.")
            return
        repertoire_path = Path(display_path).expanduser()
        if not repertoire_path.exists():
            self._error(f"Repertoire chantier introuvable: {repertoire_path}")
            return
        try:
            opened = open_file_default_app(repertoire_path)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir le repertoire avec l'application par defaut.")
            return
        self._log(f"+ Repertoire ouvert: {repertoire_path.name}")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self._config, parent=self._window)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        dialog.apply_to_config(self._config)
        if isinstance(self._services, ServiceContainer):
            self._services.reset_repertoire()
            self._services.reset_planner()
        self._window.apply_config_labels()
        self._save_config_if_available()
        self._log("+ Parametres enregistres")
        if self._config.outlook.enabled:
            target = self._config.outlook.target_mailbox or "compte Outlook selectionne"
            self._log(f"+ Outlook active: {target}")
        else:
            self._log("-> Outlook desactive")
        if self._config.planner.enabled:
            plan = self._config.planner.plan_name or self._config.planner.plan_id
            bucket = self._config.planner.bucket_name or self._config.planner.bucket_id
            self._log(f"+ Planner active: {plan} / {bucket}")
        else:
            self._log("-> Planner desactive")

    async def check_updates(self, *, show_no_update: bool = True) -> None:
        try:
            update = await GitHubReleaseChecker(ApplicationSettings.load()).check(
                current_version=__version__,
            )
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        if update is None:
            if show_no_update:
                self._log("+ ProjectFlow est a jour")
            return
        asset = select_platform_asset(update)
        if asset is None:
            self._log(
                f"! Mise a jour disponible sans artefact compatible: {update.release_url}",
            )
            return
        answer = QMessageBox.question(
            self._window,
            "Mise a jour disponible",
            _update_prompt_text(update.latest_version, update.release_notes),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._log(f"! Mise a jour disponible: {update.latest_version} - {update.release_url}")
            return

        try:
            downloaded_path = await UpdateDownloader().download(
                asset,
                version=update.latest_version,
                checksum_asset=select_checksum_asset(update, asset),
            )
            plan = prepare_install_plan(
                downloaded_path,
                current_executable=Path(sys.executable),
                process_id=os.getpid(),
            )
            launch_install_plan(plan)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        self._log(f"+ Mise a jour telechargee: {downloaded_path.name}")
        if plan.should_quit_app:
            QApplication.quit()

    def _project_from_form(self) -> ProjectInput:
        data = self._window.creation_tab.data()
        number = parse_project_number(
            format_project_number(data.year, data.project_id, data.subproject_id),
        )
        return ProjectInput(
            number=number,
            designation=data.designation,
            societe=data.societe,
            contact=data.contact,
            localisation=data.localisation,
            gere_par=data.gere_par,
        )

    def _number_from_form(self) -> str:
        data = self._window.creation_tab.data()
        return format_project_number(data.year, data.project_id, data.subproject_id)

    def _project_dir(self, number: object) -> Path:
        root = self._config.paths.racine_projets
        if root is None:
            raise ValueError("Racine projets non configuree.")
        parsed = parse_project_number(str(number))
        return root / str(parsed.year) / project_folder_name(parsed)

    def _choose_fiche(self, project_dir: Path) -> Path:
        candidates = self._services.fiche().list_candidates(project_dir)
        if len(candidates) <= 1:
            return self._services.fiche().locate_fiche(project_dir)
        dialog = FicheSelectionDialog(candidates, parent=self._window)
        if dialog.exec() != dialog.DialogCode.Accepted:
            raise ValueError("Selection de fiche annulee.")
        selected = dialog.selected_path()
        if selected is None:
            raise ValueError("Aucune fiche selectionnee.")
        return selected

    def _existing_project_update_decision(self, project: ProjectInput) -> bool | None:
        if project.is_subproject:
            return True
        project_dir = self._project_dir(project.number)
        if not project_dir.exists():
            return True
        if not self._project_info_would_change(project_dir, project):
            return False

        answer = QMessageBox.question(
            self._window,
            "Projet existant",
            "Ce projet existe deja et les informations du formulaire different "
            "de la fiche existante.\n\n"
            "Oui : mettre a jour la fiche et le repertoire, puis relancer Outlook/epingle.\n"
            "Non : conserver les informations existantes et relancer seulement Outlook/epingle.\n"
            "Annuler : ne rien faire.",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return None
        return answer == QMessageBox.StandardButton.Yes

    def _project_info_would_change(self, project_dir: Path, project: ProjectInput) -> bool:
        try:
            fiche_path = self._existing_fiche_for_comparison(project_dir, project)
            if fiche_path is None:
                return False
            data = self._services.fiche().read_fiche(fiche_path)
        except (ProjectFlowError, OSError):
            return False
        return any(
            _non_empty_changed(current, existing)
            for current, existing in [
                (project.designation, data.designation),
                (project.societe, data.societe),
                (project.contact, data.contact),
                (project.localisation, data.localisation),
                (project.gere_par, data.gere_par),
            ]
        )

    def _existing_fiche_for_comparison(
        self,
        project_dir: Path,
        project: ProjectInput,
    ) -> Path | None:
        standard_path = standard_fiche_path(project_dir, project.number)
        if standard_path.exists():
            return standard_path
        try:
            return self._services.fiche().locate_fiche(project_dir)
        except ProjectFlowError:
            return None

    def _log(self, message: str) -> None:
        self._window.creation_tab.append_log(message)

    def _log_creation_integrations(self, result: ProjectCreationResult) -> None:
        if self._config.outlook.enabled and result.outlook_folder_created:
            self._log("+ Dossiers Outlook crees")
        elif self._config.outlook.enabled:
            self._log("! Outlook active mais aucun dossier Outlook cree")
        else:
            self._log("-> Outlook desactive")
        if self._config.planner.enabled and result.planner_task_created:
            self._log("+ Tache Planner creee")
        elif self._config.planner.enabled and result.planner_task_updated:
            self._log("+ Tache Planner mise a jour")
        elif self._config.planner.enabled and result.planner_task_id:
            self._log("+ Tache Planner deja existante")

    def _open_project_folder(self, result: ProjectCreationResult) -> None:
        try:
            open_path(Path(result.project_dir))
        except (ProjectFlowError, OSError) as exc:
            self._log(f"! Projet cree, mais ouverture du dossier impossible: {exc}")
            return
        self._log("+ Dossier projet ouvert")

    def _show_creation_confirmation(self, result: ProjectCreationResult) -> None:
        title = "Projet cree" if result.project_dir_created else "Projet pret"
        message = (
            "Le projet a ete cree avec succes."
            if result.project_dir_created
            else "Le projet existant a ete reapplique avec succes."
        )
        QMessageBox.information(
            self._window,
            title,
            f"{message}\n\nDossier:\n{result.project_dir}",
        )

    def _save_config_if_available(self) -> None:
        if self._save_config is not None:
            self._save_config()

    def _schedule_task(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _error(self, message: str) -> None:
        self._window.creation_tab.append_log(f"! {message}")
        QMessageBox.critical(self._window, "ProjectFlow", message)


def _non_empty_changed(current: str, existing: str) -> bool:
    normalized_current = current.strip()
    if not normalized_current:
        return False
    return normalized_current != existing.strip()


def _update_prompt_text(version: str, release_notes: str) -> str:
    notes = release_notes.strip()
    if notes:
        return (
            f"ProjectFlow {version} est disponible.\n\n"
            f"Changements:\n{_truncate_release_notes(notes)}\n\n"
            "Telecharger et installer maintenant ?"
        )
    return f"ProjectFlow {version} est disponible. Telecharger et installer maintenant ?"


def _truncate_release_notes(notes: str, *, limit: int = 1200) -> str:
    if len(notes) <= limit:
        return notes
    return f"{notes[:limit].rstrip()}\n..."
