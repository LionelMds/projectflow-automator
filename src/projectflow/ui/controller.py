from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import replace
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, Literal, ParamSpec, Protocol, TypeVar

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from projectflow import __version__
from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import (
    PLANNER_GRAPH_SCOPES,
    MsalAccessTokenProvider,
    sign_out_microsoft,
)
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.core.background_io import run_file_io
from projectflow.core.client_directory import ClientDirectory
from projectflow.core.duplication import PROJECT_WRITABLE_WIDTH
from projectflow.core.fiche_service import FicheService, standard_fiche_path
from projectflow.core.models import PlannerTaskInput, ProjectCreationResult, ProjectInput
from projectflow.core.numero import (
    ProjectNumber,
    format_project_number,
    parse_project_number,
    project_folder_name,
)
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireRow, RepertoireService, RepertoireSnapshot
from projectflow.core.sortie_service import SortieDossierService
from projectflow.exceptions import ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import GraphPlannerClient
from projectflow.graph.workbook_opening import excel_document_uri
from projectflow.logging import redact_sensitive_links
from projectflow.platform.filemanager import open_excel_uri, open_file_default_app, open_path
from projectflow.platform.sync_paths import is_excel_recovery_copy
from projectflow.services import ServiceContainer, resolve_repertoire_open_url
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.dialogs.fiche_selection import FicheSelectionDialog
from projectflow.ui.dialogs.quick_confirmation import QuickCreationConfirmationDialog
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow
from projectflow.ui.widgets.planner import (
    PlannerBucketOption,
    PlannerMemberOption,
    PlannerSelectionWidget,
)
from projectflow.updates import (
    GitHubReleaseChecker,
    UpdateDownloader,
    launch_install_plan,
    prepare_install_plan,
    select_checksum_asset,
    select_platform_asset,
)

QuickCreationAction = Literal["open_fiche", "open_repertoire", "edit", "next"]
PlannerOptions = tuple[list[PlannerBucketOption], list[PlannerMemberOption]]
ActionArgs = ParamSpec("ActionArgs")
ReadKey = TypeVar("ReadKey")
ReadResult = TypeVar("ReadResult")


def _exclusive_action(
    action: Callable[Concatenate[ProjectFlowController, ActionArgs], Coroutine[Any, Any, None]],
) -> Callable[Concatenate[ProjectFlowController, ActionArgs], Coroutine[Any, Any, None]]:
    @wraps(action)
    async def guarded(
        controller: ProjectFlowController,
        /,
        *args: ActionArgs.args,
        **kwargs: ActionArgs.kwargs,
    ) -> None:
        if controller._mutation_busy or controller._closing:  # noqa: SLF001
            controller._log("-> Une operation est deja en cours. Patientez avant de recommencer.")  # noqa: SLF001
            return
        controller._set_mutation_busy(busy=True)  # noqa: SLF001
        controller._invalidate_repertoire_data()  # noqa: SLF001
        task = asyncio.current_task()
        if task is not None:
            controller._mutation_tasks.add(task)  # noqa: SLF001
        try:
            await action(controller, *args, **kwargs)
        finally:
            controller._invalidate_repertoire_data()  # noqa: SLF001
            controller._set_mutation_busy(busy=False)  # noqa: SLF001
            if task is not None:
                controller._mutation_tasks.discard(task)  # noqa: SLF001

    return guarded


class ClientSuggestionsTarget(Protocol):
    def data(self) -> CreationFormData:
        """Return current creation form values."""

    def set_client_directory(self, directory: ClientDirectory) -> None:
        """Apply company and contact suggestions."""


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
        self._mutation_tasks: set[asyncio.Task[Any]] = set()
        self._closing = False
        self._mutation_busy = False
        self._repertoire_generation = 0
        self._snapshot_tasks: dict[tuple[int, int], asyncio.Task[RepertoireSnapshot]] = {}
        self._planner_tasks: dict[tuple[int, str], asyncio.Task[PlannerOptions]] = {}
        self._planner_generation = 0
        self._planner_cache_plan = ""
        self._quick_dialog: QuickCreateDialog | None = None
        self._planner_bucket_options: list[PlannerBucketOption] = []
        self._planner_member_options: list[PlannerMemberOption] = []
        self._client_directories: dict[int, ClientDirectory] = {}
        self._client_directory_loading: set[int] = set()
        self._client_directory_failed: set[int] = set()
        self._sortie_service = SortieDossierService(self._services.fiche())
        self._sortie_project_dir: Path | None = None
        self._sortie_number: ProjectNumber | None = None
        self._repertoire_loading = False
        self._connect()

    def _connect(self) -> None:
        tab = self._window.creation_tab
        tab.create_requested.connect(lambda: self._schedule_task(self.create_project()))
        self._window.update_confirmed.connect(lambda: self._schedule_task(self.update_project()))
        tab.load_requested.connect(self.load_project)
        tab.open_folder_requested.connect(self.open_folder)
        tab.open_fiche_requested.connect(self.open_fiche)
        tab.open_repertoire_requested.connect(self.open_repertoire)
        tab.next_available_requested.connect(lambda: self._schedule_task(self.next_available()))
        self._window.sortie_tab.load_requested.connect(
            lambda: self._schedule_task(self.load_sortie_dossier()),
        )
        self._window.sortie_tab.create_output_requested.connect(
            lambda: self._schedule_task(self.create_sortie_dossier()),
        )
        self._window.repertoire_tab.load_requested.connect(
            lambda: self._schedule_task(self.load_repertoire()),
        )
        self._window.repertoire_tab.save_requested.connect(
            lambda row_index, values, expected: self._schedule_task(
                self.save_repertoire_row(row_index, values, expected),
            ),
        )
        self._window.repertoire_tab.sync_project_requested.connect(
            self.update_project_from_repertoire,
        )
        self._window.repertoire_tab.new_project_requested.connect(
            self.new_project_from_repertoire,
        )
        self._window.repertoire_tab.open_project_requested.connect(
            self.open_project_from_repertoire,
        )
        self._window.repertoire_tab.create_subproject_requested.connect(
            self.create_subproject_from_repertoire,
        )
        self._window.repertoire_tab.duplicate_project_requested.connect(
            self.duplicate_project_from_repertoire,
        )
        self._window.repertoire_tab.delete_project_requested.connect(
            self.delete_project_from_repertoire,
        )
        self._window.tabs.currentChanged.connect(self._on_tab_changed)
        tab.planner_options_requested.connect(
            lambda: self._schedule_task(self._load_planner_options(tab.planner_widget)),
        )
        tab.client_suggestions_requested.connect(
            lambda: self._request_client_suggestions(tab),
        )
        self._window.settings_requested.connect(self.open_settings)
        self._window.update_check_requested.connect(
            self.request_update_check,
        )

    def show_window(self) -> None:
        self._window.show_and_raise()

    def request_update_check(self, *, show_no_update: bool = True) -> None:
        self._schedule_task(self.check_updates(show_no_update=show_no_update))

    async def _read_snapshot(self, year: int) -> RepertoireSnapshot:
        key = (self._repertoire_generation, year)
        task = self._snapshot_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._services.repertoire().read_snapshot(year=year))
            self._snapshot_tasks[key] = task
            task.add_done_callback(
                lambda completed: self._finish_read(self._snapshot_tasks, key, completed),
            )
        return await asyncio.shield(task)

    @staticmethod
    def _finish_read(
        requests: dict[ReadKey, asyncio.Task[ReadResult]],
        key: ReadKey,
        task: asyncio.Task[ReadResult],
    ) -> None:
        if requests.get(key) is task:
            del requests[key]
        if not task.cancelled():
            # A consumer can disappear while a shared request completes.
            # Awaiters still receive the exception; this avoids orphan warnings.
            task.exception()

    def _invalidate_repertoire_data(self) -> None:
        self._repertoire_generation += 1
        self._client_directories.clear()
        self._client_directory_loading.clear()
        self._client_directory_failed.clear()
        empty = ClientDirectory.from_repertoire_rows(())
        self._window.creation_tab.set_client_directory(empty)
        if self._quick_dialog is not None:
            self._quick_dialog.set_client_directory(empty)

    def _set_mutation_busy(self, *, busy: bool) -> None:
        self._mutation_busy = busy
        tab = self._window.creation_tab
        tab.create_button.setEnabled(not busy)
        tab.update_button.setEnabled(not busy)
        tab.reset_button.setEnabled(not busy)
        tab.load_button.setEnabled(not busy)
        tab.open_button.setEnabled(not busy)
        tab.create_button.setText("Operation en cours..." if busy else "Creer")

    def show_quick_create(self, *, reset: bool = False) -> None:
        if self._quick_dialog is not None:
            if reset:
                self._quick_dialog.set_data(self._empty_creation_data())
            self._quick_dialog.show_and_raise()
            return
        dialog = QuickCreateDialog(parent=self._window)
        self._quick_dialog = dialog
        dialog.set_user_initials(self._config.user.initials)
        dialog.apply_cad_config(self._config.cad)
        dialog.apply_planner_config(
            enabled=self._config.planner.enabled,
            bucket_id=self._config.planner.bucket_id,
            bucket_name=self._config.planner.bucket_name,
            due_days=self._config.planner.due_days,
        )
        dialog.set_data(self._empty_creation_data() if reset else self._window.creation_tab.data())
        self._apply_cached_client_directory(dialog)
        dialog.classic_requested.connect(lambda: self._show_classic_from_quick(dialog))
        dialog.next_available_requested.connect(
            lambda: self._schedule_task(self._quick_next_available(dialog)),
        )
        dialog.planner_options_requested.connect(
            lambda: self._schedule_task(self._load_planner_options(dialog.planner_widget)),
        )
        dialog.client_suggestions_requested.connect(
            lambda: self._request_client_suggestions(dialog),
        )
        try:
            result = dialog.exec()
        finally:
            if self._quick_dialog is dialog:
                self._quick_dialog = None
        if result != dialog.DialogCode.Accepted:
            return
        self._schedule_task(self._create_project_from_quick(dialog.data()))

    def _show_classic_from_quick(self, dialog: QuickCreateDialog) -> None:
        self._window.creation_tab.set_form_data(dialog.data())
        dialog.reject()
        self._window.show_and_raise()

    async def _quick_next_available(self, dialog: QuickCreateDialog) -> None:
        generation = self._repertoire_generation
        requested_year = dialog.data().year
        active_dialog = self._quick_dialog
        try:
            year = int(requested_year)
            snapshot = await self._read_snapshot(year)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if (
            generation != self._repertoire_generation
            or requested_year != dialog.data().year
            or (active_dialog is dialog and self._quick_dialog is not dialog)
        ):
            return
        self._remember_client_directory(snapshot.year, snapshot.rows)
        result = snapshot.next_available
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

    async def _load_planner_options(self, target: PlannerSelectionWidget) -> None:
        plan_id = self._config.planner.target_plan_id
        generation = self._planner_generation
        if self._config.planner.enabled and plan_id and self._planner_cache_plan == plan_id:
            target.set_options(
                buckets=self._planner_bucket_options,
                members=self._planner_member_options,
            )
            return
        if not self._config.planner.enabled:
            target.set_options_error()
            self._error("Planner n'est pas active dans les parametres.")
            return
        if not plan_id:
            target.set_options_error()
            self._error("Selectionnez d'abord un plan Planner dans les parametres.")
            return
        try:
            key = (generation, plan_id)
            task = self._planner_tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch_planner_options(plan_id))
                self._planner_tasks[key] = task
                task.add_done_callback(
                    lambda completed: self._finish_read(self._planner_tasks, key, completed)
                )
            buckets, members = await asyncio.shield(task)
        except (ProjectFlowError, ValueError) as exc:
            if (
                generation != self._planner_generation
                or plan_id != self._config.planner.target_plan_id
                or not self._config.planner.enabled
            ):
                return
            target.set_options_error()
            self._error(str(exc))
            return
        if (
            generation != self._planner_generation
            or plan_id != self._config.planner.target_plan_id
            or not self._config.planner.enabled
        ):
            return
        self._planner_bucket_options = buckets
        self._planner_member_options = members
        self._planner_cache_plan = plan_id
        target.set_options(
            buckets=self._planner_bucket_options,
            members=self._planner_member_options,
        )
        self._log("+ Options Planner chargees")

    async def _fetch_planner_options(self, plan_id: str) -> PlannerOptions:
        client = _planner_client()
        try:
            buckets, members = await asyncio.gather(
                client.list_buckets(plan_id=plan_id),
                client.list_members(plan_id=plan_id),
                return_exceptions=True,
            )
            if isinstance(buckets, BaseException):
                raise buckets
            if isinstance(members, BaseException):
                raise members
            return (
                [PlannerBucketOption(id=bucket.id, name=bucket.name) for bucket in buckets],
                [PlannerMemberOption(id=member.id, label=member.label) for member in members],
            )
        finally:
            await client.aclose()

    @_exclusive_action
    async def create_project(self) -> None:
        try:
            project = self._project_from_form()
            created = await self._create_project_for_input(project)
            if created is None:
                return
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        result, existing_update = created
        self._window.creation_tab.reset_cad_options()
        self._save_config_if_available()
        self._log_creation_result(result, existing_update=existing_update)
        self._log_creation_integrations(result)
        self._open_project_folder(result)
        self._show_creation_confirmation(result)

    @_exclusive_action
    async def _create_project_from_quick(self, data: CreationFormData) -> None:
        try:
            project = self._project_from_data(data)
            created = await self._create_project_for_input(project)
            if created is None:
                return
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        result, existing_update = created
        self._save_config_if_available()
        self._log_creation_result(result, existing_update=existing_update)
        self._log_creation_integrations(result)
        self._show_quick_creation_confirmation(result, data, project)

    async def _create_project_for_input(
        self,
        project: ProjectInput,
    ) -> tuple[ProjectCreationResult, bool] | None:
        project_exists = not project.is_subproject and self._project_dir(project.number).exists()
        existing_update = self._existing_project_update_decision(project)
        if existing_update is None:
            self._log("! Creation annulee")
            return None
        result = await self._services.project().create_project(
            project,
            force_overwrite=project_exists and existing_update,
            update_existing_info=existing_update,
        )
        return result, existing_update

    @_exclusive_action
    async def update_project(self) -> None:
        try:
            project = self._project_from_form()
            result = await self._services.project().update_project(project)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        self._window.creation_tab.reset_cad_options()
        self._save_config_if_available()
        self._log(f"+ Projet mis a jour: {result.fiche_path or result.project_dir}")
        self._log_creation_integrations(result)

    async def next_available(self) -> None:
        generation = self._repertoire_generation
        requested_year = self._window.creation_tab.data().year
        try:
            year = int(requested_year)
            snapshot = await self._read_snapshot(year)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if (
            generation != self._repertoire_generation
            or requested_year != self._window.creation_tab.data().year
        ):
            return
        self._remember_client_directory(snapshot.year, snapshot.rows)
        result = snapshot.next_available
        if result is None:
            self._log("! Aucun numero disponible trouve")
            return
        self._window.creation_tab.set_project_identity(
            year=str(result.number.year),
            project_id=result.number.project_id,
        )
        self._save_config_if_available()
        self._log(f"+ Numero disponible: {result.number}")

    def _on_tab_changed(self, index: int) -> None:
        if index != self._window.tabs.indexOf(self._window.repertoire_tab):
            return
        self._schedule_task(self.load_repertoire())

    async def load_repertoire(self) -> None:
        if self._repertoire_loading or self._closing:
            return
        tab = self._window.repertoire_tab
        try:
            year = tab.year()
        except ValueError:
            tab.set_error("L'année doit être un nombre valide.")
            return
        self._repertoire_loading = True
        generation = self._repertoire_generation
        tab.set_loading(loading=True)
        try:
            snapshot = await self._read_snapshot(year)
        except (ProjectFlowError, ValueError, OSError) as exc:
            if generation == self._repertoire_generation:
                tab.set_error(redact_sensitive_links(str(exc)))
        else:
            if generation == self._repertoire_generation and year == tab.year():
                tab.set_snapshot(snapshot)
                self._remember_client_directory(snapshot.year, snapshot.rows)
        finally:
            self._repertoire_loading = False
            tab.set_loading(loading=False)

    def _request_client_suggestions(self, target: ClientSuggestionsTarget) -> None:
        try:
            year = int(target.data().year)
        except ValueError:
            return
        cached = self._client_directories.get(year)
        if cached is not None:
            target.set_client_directory(cached)
            return
        if year in self._client_directory_loading or year in self._client_directory_failed:
            return
        self._client_directory_loading.add(year)
        self._schedule_task(self._load_client_suggestions(year))

    async def _load_client_suggestions(self, year: int) -> None:
        self._client_directory_loading.add(year)
        generation = self._repertoire_generation
        try:
            snapshot = await self._read_snapshot(year)
        except (ProjectFlowError, ValueError, OSError) as exc:
            if generation != self._repertoire_generation:
                return
            self._client_directory_failed.add(year)
            self._window.creation_tab.append_log(
                redact_sensitive_links(f"! Suggestions Societe/Contact indisponibles: {exc}"),
            )
        else:
            if generation == self._repertoire_generation:
                self._remember_client_directory(snapshot.year, snapshot.rows)
        finally:
            if generation == self._repertoire_generation:
                self._client_directory_loading.discard(year)

    def _remember_client_directory(self, year: int, rows: Sequence[RepertoireRow]) -> None:
        directory = ClientDirectory.from_repertoire_rows(row.values for row in rows)
        self._client_directories[year] = directory
        self._client_directory_failed.discard(year)
        self._apply_cached_client_directory(self._window.creation_tab)
        if self._quick_dialog is not None:
            self._apply_cached_client_directory(self._quick_dialog)

    def _apply_cached_client_directory(self, target: ClientSuggestionsTarget) -> None:
        try:
            year = int(target.data().year)
        except ValueError:
            return
        directory = self._client_directories.get(year, ClientDirectory.from_repertoire_rows(()))
        target.set_client_directory(directory)

    @_exclusive_action
    async def save_repertoire_row(
        self,
        row_index: int,
        values: object,
        expected_values: object,
    ) -> None:
        typed_values = _as_row_values(values)
        typed_expected = _as_row_values(expected_values)
        if typed_values is None or typed_expected is None:
            self._error("La ligne du répertoire est invalide.")
            return
        if (
            len(typed_values) != PROJECT_WRITABLE_WIDTH
            or len(typed_expected) != PROJECT_WRITABLE_WIDTH
        ):
            self._error("La ligne du répertoire doit contenir les colonnes A à E.")
            return
        tab = self._window.repertoire_tab
        try:
            year = tab.year()
        except ValueError:
            self._error("L'année doit être un nombre valide.")
            return
        tab.set_loading(loading=True)
        try:
            await self._services.repertoire().update_editable_row(
                year=year,
                row_index=row_index,
                values=typed_values,
                expected_values=typed_expected,
            )
        except (ProjectFlowError, ValueError, OSError) as exc:
            tab.set_error(str(exc))
        else:
            tab.mark_saved(row_index, typed_values)
            tab.set_status_message(f"Ligne Excel {row_index + 1} enregistrée dans le répertoire.")
        finally:
            tab.set_loading(loading=False)

    def update_project_from_repertoire(self) -> None:
        payload = self._window.repertoire_tab.selected_row_payload()
        if payload is None:
            self._error("Sélectionnez une ligne de projet dans le répertoire.")
            return
        row_index, values, expected_values = payload
        try:
            number = parse_project_number(_repertoire_cell_text(values[0]))
        except ValueError as exc:
            self._error(str(exc))
            return
        answer = QMessageBox.question(
            self._window,
            "Mettre à jour le projet",
            f"Mettre à jour le projet {number} et ses éléments associés ?\n\n"
            "La fiche sera réécrite avec les informations de la ligne, "
            "puis Outlook, Planner et l'épinglage seront réappliqués selon les paramètres actifs.\n"
            "Aucun fichier existant ne sera écrasé par la copie de référence.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._schedule_task(
            self._sync_project_from_repertoire(
                row_index=row_index,
                values=values,
                expected_values=expected_values,
            ),
        )

    @_exclusive_action
    async def _sync_project_from_repertoire(
        self,
        *,
        row_index: int,
        values: tuple[Any, ...],
        expected_values: tuple[Any, ...],
    ) -> None:
        tab = self._window.repertoire_tab
        try:
            year = tab.year()
            number = parse_project_number(_repertoire_cell_text(values[0]))
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if number.year != year:
            self._error(f"La ligne {number} n'appartient pas à l'année sélectionnée ({year}).")
            return
        try:
            project_dir = self._project_dir(number)
            fiche_path = self._choose_fiche(project_dir, number)
            fiche_data = self._services.fiche().read_fiche(fiche_path)
            project = ProjectInput(
                number=number,
                designation=_repertoire_cell_text(values[4]),
                societe=_repertoire_cell_text(values[2]),
                contact=_repertoire_cell_text(values[3]),
                localisation=fiche_data.localisation,
                gere_par=fiche_data.gere_par,
                planner=PlannerTaskInput(
                    enabled=self._config.planner.enabled,
                    bucket_id=self._config.planner.target_bucket_id,
                    due_days=(
                        self._config.planner.due_days if self._config.planner.due_days > 0 else None
                    ),
                ),
            )
            tab.set_loading(loading=True)
            await self._services.repertoire().update_editable_row(
                year=year,
                row_index=row_index,
                values=values,
                expected_values=expected_values,
            )
            result = await self._services.project().update_project(project)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        finally:
            tab.set_loading(loading=False)

        tab.mark_saved(row_index, values)
        integrations = ["fiche", "répertoire"]
        if self._config.outlook.enabled:
            integrations.append("Outlook")
        if self._config.planner.enabled:
            integrations.append("Planner")
        tab.set_status_message(f"Projet {project.number} mis à jour : {', '.join(integrations)}.")
        self._window.creation_tab.append_log(
            f"+ Projet {project.number} mis à jour depuis le répertoire"
        )
        self._log_creation_integrations(result)

    def new_project_from_repertoire(self) -> None:
        tab = self._window.repertoire_tab
        try:
            year = str(tab.year())
        except ValueError:
            year = str(date.today().year)
        self._window.creation_tab.reset_form_fields()
        self._window.creation_tab.set_project_identity(
            year=year,
            project_id="",
            subproject_id="",
        )
        self._window.show_creation_tab()

    def open_project_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        _row_index, number, _values, _original = selected
        if self._load_project_number(number):
            self._window.show_creation_tab()

    def create_subproject_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        _row_index, number, _values, _original = selected
        parent = number.parent
        subproject_id = _next_subproject_id(
            parent,
            self._window.repertoire_tab.original_rows(),
        )
        self._window.creation_tab.reset_form_fields()
        self._window.creation_tab.set_project_identity(
            year=str(parent.year),
            project_id=parent.project_id,
            subproject_id=subproject_id,
        )
        self._window.show_creation_tab()
        self._log(f"+ Nouveau sous-projet préparé: {parent}-{subproject_id}")

    def duplicate_project_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        _row_index, source_number, _values, _original = selected
        next_available = self._window.repertoire_tab.next_available()
        if next_available is None:
            self._error("Aucun numéro disponible n'a été détecté dans le répertoire.")
            return
        if not self._load_project_number(source_number):
            return
        target = next_available.number
        self._window.creation_tab.set_project_identity(
            year=str(target.year),
            project_id=target.project_id,
            subproject_id="",
        )
        self._window.show_creation_tab()
        self._log(f"+ Projet {source_number} dupliqué dans le formulaire sous le numéro {target}")

    def delete_project_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        _row_index, number, values, original = selected
        if values != original:
            self._error(
                "La ligne contient des modifications non enregistrées. "
                "Enregistrez ou actualisez le répertoire avant de supprimer.",
            )
            return
        rows = _project_deletion_rows(
            number,
            self._window.repertoire_tab.original_rows(),
        )
        projects = tuple(
            sorted(
                (
                    ProjectInput(
                        number=parse_project_number(row.number),
                        designation=_repertoire_cell_text(row.values[4]),
                    )
                    for row in rows
                    if any(_repertoire_cell_text(value) for value in row.values[1:])
                ),
                key=lambda item: (item.number != number, item.number.subproject_id or ""),
            )
        )
        if not projects:
            self._error(f"Aucun projet actif n'est associé au numéro {number}.")
            return

        related_note = ""
        if not number.is_subproject and len(projects) > 1:
            related_note = f"\n- {len(projects) - 1} sous-projet(s) lié(s) et leurs tâches Planner"
        outlook_note = "\n- le dossier Outlook" if self._config.outlook.enabled else ""
        planner_note = "\n- la ou les tâches Planner" if self._config.planner.enabled else ""
        answer = QMessageBox.warning(
            self._window,
            "Supprimer le projet et ses éléments liés",
            f"Confirmez-vous la suppression de {number} ?\n\n"
            "Cette opération va supprimer :\n"
            "- les informations B:E du répertoire, en conservant le numéro A\n"
            "- le dossier projet OneDrive, placé dans la corbeille du système"
            f"{outlook_note}{planner_note}{related_note}\n\n"
            "Le numéro restera disponible pour un nouveau projet.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._schedule_task(
            self._delete_project_and_related(
                project=projects[0],
                related_projects=projects,
                rows=rows,
            )
        )

    @_exclusive_action
    async def _delete_project_and_related(
        self,
        *,
        project: ProjectInput,
        related_projects: Sequence[ProjectInput],
        rows: Sequence[RepertoireRow],
    ) -> None:
        tab = self._window.repertoire_tab
        tab.set_loading(loading=True)
        try:
            result = await self._services.project().delete_project(
                project,
                related_projects=related_projects,
                repertoire_rows=rows,
            )
        except (ProjectFlowError, ValueError, OSError) as exc:
            tab.set_error(str(exc))
            self._error(str(exc))
            return
        finally:
            tab.set_loading(loading=False)

        await self.load_repertoire()
        details = [f"{len(result.numbers_released)} numéro(s) libéré(s)"]
        if result.project_path_trashed:
            details.append("dossier placé dans la corbeille")
        if result.outlook_folders_deleted:
            details.append(f"{result.outlook_folders_deleted} dossier(s) Outlook supprimé(s)")
        if result.planner_tasks_deleted:
            details.append(f"{result.planner_tasks_deleted} tâche(s) Planner supprimée(s)")
        message = f"Projet {project.number} supprimé : {', '.join(details)}."
        tab.set_status_message(message)
        self._window.creation_tab.append_log(f"+ {message}")
        if not self._closing:
            QMessageBox.information(self._window, "Projet supprimé", message)

    def _selected_repertoire_project(
        self,
    ) -> tuple[int, ProjectNumber, tuple[Any, ...], tuple[Any, ...]] | None:
        payload = self._window.repertoire_tab.selected_row_payload()
        if payload is None:
            self._error("Sélectionnez une ligne de projet dans le répertoire.")
            return None
        row_index, values, original = payload
        try:
            number = parse_project_number(_repertoire_cell_text(values[0]))
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return None
        if not any(_repertoire_cell_text(value) for value in values[1:]):
            self._error(f"Le numéro {number} est disponible et ne contient aucun projet à charger.")
            return None
        return row_index, number, values, original

    async def load_sortie_dossier(self) -> None:
        tab = self._window.sortie_tab
        if tab.is_loading:
            return
        requested_identity = tab.project_identity()
        tab.set_loading(loading=True)
        try:
            year, project_id = tab.project_identity()
            number = _parse_sortie_number(year, project_id)
            root = self._config.paths.racine_projets
            if root is None:
                self._output_error("Racine projets non configuree.")
                return
            project_dir = root / str(number.year) / project_folder_name(number)
            inventory = await run_file_io(self._sortie_service.discover, project_dir, number)
        except (ProjectFlowError, OSError, ValueError) as exc:
            self._output_error(str(exc))
            return
        finally:
            tab.set_loading(loading=False)
        if (
            requested_identity != tab.project_identity()
            or root != self._config.paths.racine_projets
        ):
            return
        tab.set_project_identity(year=str(number.year), project_id=str(number))
        tab.set_project_directory(project_dir)
        tab.set_inventory(inventory)
        self._sortie_project_dir = project_dir
        self._sortie_number = number
        tab.append_log(f"+ Projet charge: {project_dir}")

    @_exclusive_action
    async def create_sortie_dossier(self) -> None:
        tab = self._window.sortie_tab
        year, project_id = tab.project_identity()
        try:
            number = _parse_sortie_number(year, project_id)
        except (ProjectFlowError, ValueError) as exc:
            self._output_error(str(exc))
            return
        if self._sortie_project_dir is None or self._sortie_number != number:
            self._output_error("Chargez le projet avant de creer le dossier de sortie.")
            return
        project_dir = self._sortie_project_dir
        tab.set_loading(loading=True)
        try:
            selection = tab.data()
            output_dir = await run_file_io(
                self._sortie_service.create_output_folder,
                project_dir,
                number,
                selection,
            )
        except (ProjectFlowError, OSError, ValueError) as exc:
            self._output_error(str(exc))
            return
        finally:
            tab.set_loading(loading=False)
        tab.append_log(f"+ Dossier de sortie cree: {output_dir}")
        if self._closing:
            return
        answer = QMessageBox.question(
            self._window,
            "Sortie dossier",
            f"Le dossier de sortie a ete cree ici :\n{output_dir}\n\n"
            "Voulez-vous l'ouvrir maintenant ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            open_path(output_dir)
        except (ProjectFlowError, OSError) as exc:
            tab.append_log(f"! Dossier de sortie cree, mais ouverture impossible: {exc}")
            return
        tab.append_log("+ Dossier de sortie ouvert")

    def load_project(self) -> None:
        try:
            number = parse_project_number(self._number_from_form())
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return

        self._load_project_number(number)

    def _load_project_number(self, number: ProjectNumber) -> bool:
        if self._mutation_busy:
            self._log("-> Patientez jusqu'a la fin de l'operation avant de charger la fiche.")
            return False
        try:
            project_dir = self._project_dir(number)
            fiche_path = self._choose_fiche(project_dir, number)
            data = self._services.fiche().read_fiche(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return False

        self._window.creation_tab.set_project_identity(
            year=str(number.year),
            project_id=number.project_id,
            subproject_id=number.subproject_id or "",
        )
        self._window.creation_tab.designation_edit.setText(data.designation)
        self._window.creation_tab.societe_edit.setText(data.societe)
        self._window.creation_tab.contact_edit.setText(data.contact)
        self._window.creation_tab.localisation_edit.setText(data.localisation)
        self._window.creation_tab.gere_par_edit.setText(data.gere_par)
        self._window.creation_tab.set_user_initials(self._config.user.initials)
        if data.number and data.number != str(number):
            self._log(f"! C3 contient {data.number}, attendu {number}")
        self._log(f"+ Fiche chargee: {fiche_path.name}")
        return True

    def open_fiche(self) -> None:
        if self._mutation_busy:
            self._log("-> Patientez jusqu'a la fin de l'operation avant d'ouvrir la fiche.")
            return
        try:
            number = parse_project_number(self._number_from_form())
            project_dir = self._project_dir(number)
            fiche_path = standard_fiche_path(project_dir, number)
            if not fiche_path.exists():
                fiche_path = self._services.fiche().locate_fiche(project_dir, number)
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def open_folder(self) -> None:
        try:
            number = parse_project_number(self._number_from_form())
            project_dir = self._project_dir(number)
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if not project_dir.exists():
            self._error(f"Dossier projet introuvable: {project_dir}")
            return
        try:
            open_path(project_dir)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        self._log(f"+ Dossier projet ouvert: {project_dir}")

    def open_repertoire(self) -> None:
        repertoire = self._config.paths.repertoire_chantier
        display_path = repertoire.open_path.strip() or repertoire.display_path.strip()
        if not display_path and not repertoire.is_configured:
            self._error("Repertoire chantier non configure.")
            return
        if not display_path or "://" in display_path:
            self._schedule_task(self._open_cloud_repertoire(repertoire.model_copy(deep=True)))
            return
        repertoire_path = Path(display_path).expanduser()
        if is_excel_recovery_copy(repertoire_path):
            self._error("Selectionnez le repertoire d'origine, pas sa copie non fusionnee.")
            return
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

    async def _open_cloud_repertoire(self, repertoire: RepertoireChantierConfig) -> None:
        try:
            url = await resolve_repertoire_open_url(repertoire)
            current = self._config.paths.repertoire_chantier
            if (repertoire.display_path, repertoire.open_path, repertoire.cloud_only) != (
                current.display_path,
                current.open_path,
                current.cloud_only,
            ) or (
                repertoire.is_configured
                and (repertoire.drive_id, repertoire.item_id) != (current.drive_id, current.item_id)
            ):
                return
            opened = open_excel_uri(excel_document_uri(url))
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error(
                "Impossible de lancer Excel. Verifiez son installation ou selectionnez "
                "le fichier synchronise pour ouverture Excel dans les parametres.",
            )
            return
        self._log("+ Ouverture du repertoire demandee dans Excel.")

    def open_settings(self) -> None:
        if self._mutation_busy:
            self._log("-> Attendez la fin de l'operation avant de modifier les parametres.")
            return
        dialog = SettingsDialog(self._config, parent=self._window)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        previous_repertoire = self._config.paths.repertoire_chantier.model_copy(deep=True)
        previous_planner = self._config.planner.model_copy(deep=True)
        dialog.apply_to_config(self._config)
        current_repertoire = self._config.paths.repertoire_chantier
        # Opening a different local copy or changing initials does not require
        # reconnecting the cloud workbook or throwing away its client directory.
        repertoire_changed = previous_repertoire.model_dump(
            exclude={"open_path"}
        ) != current_repertoire.model_dump(exclude={"open_path"})
        planner_changed = (previous_planner.enabled, previous_planner.target_plan_id) != (
            self._config.planner.enabled,
            self._config.planner.target_plan_id,
        )
        microsoft_sign_in = dialog.microsoft_sign_in_requested
        if microsoft_sign_in:
            self._sign_out_microsoft()
        if repertoire_changed or microsoft_sign_in:
            if isinstance(self._services, ServiceContainer):
                self._services.reset_repertoire()
            self._invalidate_repertoire_data()
        if planner_changed or microsoft_sign_in:
            if isinstance(self._services, ServiceContainer):
                self._services.reset_planner()
            self._planner_generation += 1
            self._planner_cache_plan = ""
            self._planner_bucket_options = []
            self._planner_member_options = []
        self._window.apply_config_labels()
        self._apply_quick_config(planner_changed=previous_planner != self._config.planner)
        self._save_config_if_available()
        self._log("+ Parametres enregistres")
        self._log_integration_settings()
        if microsoft_sign_in:
            self._log("+ Connexion Microsoft reinitialisee: connectez-vous dans le navigateur.")
            self._window.tabs.setCurrentWidget(self._window.repertoire_tab)
            self._schedule_task(self.load_repertoire())

    def _log_integration_settings(self) -> None:
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

    def _sign_out_microsoft(self) -> None:
        try:
            sign_out_microsoft()
        except OSError as exc:
            self._error(f"Impossible d'effacer la connexion Microsoft: {exc}")

    def _apply_quick_config(self, *, planner_changed: bool) -> None:
        if self._quick_dialog is None:
            return
        self._quick_dialog.set_user_initials(self._config.user.initials)
        self._quick_dialog.apply_cad_config(self._config.cad)
        if planner_changed:
            self._quick_dialog.apply_planner_config(
                enabled=self._config.planner.enabled,
                bucket_id=self._config.planner.bucket_id,
                bucket_name=self._config.planner.bucket_name,
                due_days=self._config.planner.due_days,
            )

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
        return self._project_from_data(data)

    def _project_from_data(self, data: CreationFormData) -> ProjectInput:
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
            planner=PlannerTaskInput(
                enabled=data.planner.enabled and self._config.planner.enabled,
                bucket_id=data.planner.bucket_id,
                assignee_ids=data.planner.assignee_ids,
                due_days=data.planner.due_days if data.planner.due_enabled else None,
            ),
            add_solidworks=data.add_solidworks,
            add_autocad=data.add_autocad,
        )

    def _empty_creation_data(self) -> CreationFormData:
        current_year = self._window.creation_tab.data().year or str(date.today().year)
        return CreationFormData(
            year=current_year,
            project_id="",
            subproject_id="",
            designation="",
            societe="",
            contact="",
            localisation="",
            gere_par="",
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

    def _choose_fiche(self, project_dir: Path, number: object) -> Path:
        parsed = parse_project_number(str(number))
        candidates = self._services.fiche().list_candidates(project_dir, parsed)
        if len(candidates) <= 1:
            return self._services.fiche().locate_fiche(project_dir, parsed)
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
            return self._services.fiche().locate_fiche(project_dir, project.number)
        except ProjectFlowError:
            return None

    def _log(self, message: str) -> None:
        self._window.creation_tab.append_log(redact_sensitive_links(message))

    def _log_creation_result(
        self,
        result: ProjectCreationResult,
        *,
        existing_update: bool,
    ) -> None:
        if result.project_dir_created:
            self._log(f"+ Projet cree: {result.project_dir}")
        else:
            self._log(f"+ Projet existant reapplique: {result.project_dir}")
        if not result.project_dir_created and not existing_update:
            self._log("+ Informations existantes conservees")

    def _log_creation_integrations(self, result: ProjectCreationResult) -> None:
        self._log_cad_result(result)
        if result.outlook_error:
            self._log(f"! Dossiers Outlook non crees: {result.outlook_error}")
        elif self._config.outlook.enabled and result.outlook_folder_created:
            self._log("+ Dossiers Outlook crees")
        elif self._config.outlook.enabled:
            self._log("! Outlook active mais aucun dossier Outlook cree")
        else:
            self._log("-> Outlook desactive")
        if result.planner_error:
            self._log(f"! Tache Planner non creee: {result.planner_error}")
        elif self._config.planner.enabled and result.planner_task_created:
            self._log("+ Tache Planner creee")
        elif self._config.planner.enabled and result.planner_task_updated:
            self._log("+ Tache Planner mise a jour")
        elif self._config.planner.enabled and result.planner_task_id:
            self._log("+ Tache Planner deja existante")
        elif self._config.planner.enabled:
            self._log("! Planner actif mais aucune tache n'a ete associee a ce projet")

    def _log_cad_result(self, result: ProjectCreationResult) -> None:
        for item in result.cad_files:
            if item.status == "created":
                self._log(f"+ Fichier CAO cree: {item.name}")
            elif item.status == "skipped":
                self._log(f"-> Fichier CAO ignore: {item.name} ({item.detail})")
            else:
                self._log(f"! Fichier CAO en erreur: {item.name} - {item.detail}")
        for warning in result.cad_warnings:
            self._log(f"! CAO: {warning}")
        if result.cad_error:
            self._log(f"! Fichiers CAO non crees: {result.cad_error}")

    def _open_project_folder(self, result: ProjectCreationResult) -> None:
        if self._closing:
            return
        try:
            open_path(Path(result.project_dir))
        except (ProjectFlowError, OSError) as exc:
            self._log(f"! Projet cree, mais ouverture du dossier impossible: {exc}")
            return
        self._log("+ Dossier projet ouvert")

    def _show_creation_confirmation(self, result: ProjectCreationResult) -> None:
        if self._closing:
            return
        title = "Projet cree" if result.project_dir_created else "Projet pret"
        message = (
            "Le projet a ete cree avec succes."
            if result.project_dir_created
            else "Le projet existant a ete reapplique avec succes."
        )
        QMessageBox.information(
            self._window,
            title,
            f"{message}{_cad_summary(result)}\n\nDossier:\n{result.project_dir}",
        )

    def _show_quick_creation_confirmation(
        self,
        result: ProjectCreationResult,
        data: CreationFormData,
        project: ProjectInput,
    ) -> None:
        if self._closing:
            return
        title = "Projet cree" if result.project_dir_created else "Projet pret"
        message = (
            "Le projet a ete cree avec succes."
            if result.project_dir_created
            else "Le projet existant a ete reapplique avec succes."
        )
        dialog = QuickCreationConfirmationDialog(
            title=title,
            message=f"{message}{_cad_summary(result)}",
            project_dir=result.project_dir,
            parent=self._window if self._window.isVisible() else None,
        )
        dialog.open_fiche_requested.connect(
            lambda: self._handle_quick_creation_action("open_fiche", result, data, project),
        )
        dialog.open_repertoire_requested.connect(
            lambda: self._handle_quick_creation_action("open_repertoire", result, data, project),
        )
        dialog.exec()

        selected_action = dialog.selected_action()
        if selected_action == "edit":
            self._handle_quick_creation_action("edit", result, data, project)
        elif selected_action == "next":
            self._handle_quick_creation_action("next", result, data, project)

    def _handle_quick_creation_action(
        self,
        action: QuickCreationAction,
        result: ProjectCreationResult,
        data: CreationFormData,
        project: ProjectInput,
    ) -> None:
        if action == "open_fiche":
            self._open_fiche_from_result(result, project)
            return
        if action == "open_repertoire":
            self.open_repertoire()
            return
        if action == "edit":
            # The CAD files were created: editing must not offer to add them again.
            self._window.creation_tab.set_form_data(
                replace(data, add_solidworks=False, add_autocad=False),
            )
            self._window.show_and_raise()
            return
        if action == "next":
            QTimer.singleShot(0, lambda: self.show_quick_create(reset=True))

    def _open_fiche_from_result(
        self,
        result: ProjectCreationResult,
        project: ProjectInput,
    ) -> None:
        try:
            fiche_path = (
                Path(result.fiche_path)
                if result.fiche_path is not None
                else standard_fiche_path(Path(result.project_dir), project.number)
            )
            if not fiche_path.exists():
                fiche_path = self._services.fiche().locate_fiche(
                    Path(result.project_dir),
                    project.number,
                )
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def _save_config_if_available(self) -> None:
        if self._save_config is not None:
            self._save_config()

    def _schedule_task(self, coroutine: Coroutine[Any, Any, None]) -> None:
        if self._closing:
            coroutine.close()
            return
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._error(f"L'operation n'a pas pu etre terminee : {error}")

    async def aclose(self) -> None:
        self._closing = True
        tasks = {
            *self._background_tasks,
            *self._mutation_tasks,
            *self._snapshot_tasks.values(),
            *self._planner_tasks.values(),
        }
        # Finish committed user actions, including their repertoire/integration writes.
        # Cancelling between the local save and these writes would leave a partial project.
        for task in tasks - self._mutation_tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for dialog in self._window.findChildren(SettingsDialog):
            await dialog.aclose()
        if isinstance(self._services, ServiceContainer):
            await self._services.close()

    def _error(self, message: str) -> None:
        message = redact_sensitive_links(message)
        self._window.creation_tab.append_log(f"! {message}")
        if not self._closing:
            QMessageBox.critical(self._window, "ProjectFlow", message)

    def _output_error(self, message: str) -> None:
        message = redact_sensitive_links(message)
        self._window.sortie_tab.append_log(f"! {message}")
        if not self._closing:
            QMessageBox.critical(self._window, "Sortie dossier", message)


def _cad_summary(result: ProjectCreationResult) -> str:
    if not (result.cad_files or result.cad_warnings or result.cad_error):
        return ""
    counts = {
        status: sum(1 for item in result.cad_files if item.status == status)
        for status in ("created", "skipped", "error")
    }
    summary = (
        f"\n\nFichiers CAO : {counts['created']} cree(s), {counts['skipped']} ignore(s), "
        f"{counts['error']} en erreur."
    )
    if result.cad_warnings or result.cad_error:
        summary += " Details dans le journal."
    return summary


def _non_empty_changed(current: str, existing: str) -> bool:
    normalized_current = current.strip()
    if not normalized_current:
        return False
    return normalized_current != existing.strip()


def _as_row_values(value: object) -> tuple[Any, ...] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return tuple(value)


def _repertoire_cell_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _next_subproject_id(parent: ProjectNumber, rows: Sequence[RepertoireRow]) -> str:
    used_ids: set[int] = set()
    for row in rows:
        try:
            number = parse_project_number(row.number)
        except ValueError:
            continue
        if number.parent != parent or number.subproject_id is None:
            continue
        used_ids.add(int(number.subproject_id))
    candidate = 2
    while candidate in used_ids:
        candidate += 1
    return str(candidate)


def _project_deletion_rows(
    number: ProjectNumber,
    rows: Sequence[RepertoireRow],
) -> tuple[RepertoireRow, ...]:
    if number.is_subproject:
        return tuple(row for row in rows if row.number == str(number))
    prefix = f"{number}-"
    return tuple(row for row in rows if row.number == str(number) or row.number.startswith(prefix))


def _parse_sortie_number(year: str, project_id: str) -> ProjectNumber:
    raw_project_id = project_id.strip()
    if "-" in raw_project_id:
        return parse_project_number(raw_project_id)
    return parse_project_number(format_project_number(year, raw_project_id))


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


def _planner_client() -> GraphPlannerClient:
    settings = ApplicationSettings.load()
    token_provider = MsalAccessTokenProvider(
        client_id=settings.microsoft_client_id,
        scopes=PLANNER_GRAPH_SCOPES,
    )
    return GraphPlannerClient(
        graph=GraphClient(token_provider=token_provider, request_timeout=60.0)
    )
