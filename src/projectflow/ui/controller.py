from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import replace
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, Literal, ParamSpec, Protocol, TypeVar

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication, QDialog, QMessageBox, QWidget

from projectflow import __version__
from projectflow.application_settings import ApplicationSettings
from projectflow.auth.browser_sign_in import cancel_pending_sign_ins
from projectflow.auth.msal_client import PLANNER_GRAPH_SCOPES, MsalAccessTokenProvider
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.core.background_io import run_file_io
from projectflow.core.client_directory import ClientDirectory
from projectflow.core.duplication import PROJECT_WRITABLE_WIDTH
from projectflow.core.fiche_service import FicheData, FicheService, standard_fiche_path
from projectflow.core.models import PlannerTaskInput, ProjectCreationResult, ProjectInput
from projectflow.core.numero import (
    ProjectNumber,
    format_project_number,
    parse_project_number,
    project_folder_name,
)
from projectflow.core.planning.calendar_import import ImportedEvent
from projectflow.core.planning.copy import CopyReport, copy_planning
from projectflow.core.planning.local_store import LocalPlanningStore
from projectflow.core.planning.outlook_sync import CalendarGateway
from projectflow.core.planning.poste_config import (
    DEFAULT_OUTLOOK_CALENDAR,
    OutlookRoute,
    PlanningPosteConfig,
)
from projectflow.core.planning.store import PlanningStore
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireRow, RepertoireService, RepertoireSnapshot
from projectflow.core.sortie_service import SortieDossierService
from projectflow.exceptions import PrintError, ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import GraphPlannerClient
from projectflow.graph.workbook_opening import excel_document_uri
from projectflow.logging import get_logger, redact_sensitive_links
from projectflow.outlook.selection import read_outlook_selection
from projectflow.platform.filemanager import open_excel_uri, open_file_default_app, open_path
from projectflow.platform.printing import print_workbook_a4
from projectflow.platform.sync_paths import is_excel_recovery_copy
from projectflow.services import ServiceContainer, resolve_repertoire_open_url, uses_microsoft
from projectflow.ui.creation_tab import CreationFormData
from projectflow.ui.dialogs.fiche_selection import FicheSelectionDialog
from projectflow.ui.dialogs.planning_team import PlannerMember
from projectflow.ui.dialogs.print_fiche import (
    PrintFicheDialog,
    PrintFicheOptions,
    installed_printers,
)
from projectflow.ui.dialogs.quick_confirmation import QuickCreationConfirmationDialog
from projectflow.ui.dialogs.quick_create import QuickCreateDialog
from projectflow.ui.dialogs.settings import SettingsDialog
from projectflow.ui.main_window import MainWindow, MicrosoftState
from projectflow.ui.modal import ask_outside_task, show_outside_task
from projectflow.ui.planning_controller import (
    PlanningController,
    PlanningHooks,
    PlanningStoreFactory,
)
from projectflow.ui.theme import apply_theme
from projectflow.ui.widgets.planner import (
    PlannerBucketOption,
    PlannerMemberOption,
    PlannerSelectionWidget,
    default_assignees,
)
from projectflow.updates import (
    GitHubReleaseChecker,
    InstallPlan,
    RelaunchMode,
    ReleaseAsset,
    StagedUpdate,
    UpdateDownloader,
    UpdateInfo,
    begin_staged_install,
    discard_staged_update,
    file_sha256,
    installs_unattended,
    launch_install_plan,
    load_staged_update,
    prepare_install_plan,
    save_staged_update,
    select_checksum_asset,
    select_platform_asset,
    silent_updates_supported,
    verify_staged_installer,
)

QuickCreationAction = Literal["open_fiche", "open_repertoire", "edit", "next"]
# The app stays in the notification area for days: a start-only check would miss releases.
UPDATE_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000
# A downloaded release installs once the window stayed hidden for this many polls in a row.
UPDATE_IDLE_POLL_MS = 60 * 1000
UPDATE_IDLE_POLLS = 2
# The startup check must answer fast: a silent token comes from the cache or one request.
CONNECTION_CHECK_TIMEOUT_SECONDS = 10
# Closing waits this long for the planning: 5 s to send its last changes, then its cache.
PLANNING_CLOSE_TIMEOUT_SECONDS = 15.0
PlannerOptions = tuple[list[PlannerBucketOption], list[PlannerMemberOption]]
PlannerMembers = tuple[PlannerMember, ...]
Notifier = Callable[[str, str], None]
"""Shows a title and a message outside the window (the tray icon's balloon)."""
ActionArgs = ParamSpec("ActionArgs")
ReadKey = TypeVar("ReadKey")
ReadResult = TypeVar("ReadResult")
Answer = TypeVar("Answer")


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


class OutlookCalendarFactory(Protocol):
    """``ServiceContainer.open_outlook_calendar``: the browser opens only if interactive.

    ``via="desktop"`` goes through the Outlook of this poste, in the account ``mailbox``.
    """

    def __call__(
        self, *, interactive: bool = False, via: OutlookRoute = "graph", mailbox: str = ""
    ) -> Awaitable[CalendarGateway]: ...


class ProjectFlowController:
    def __init__(
        self,
        *,
        window: MainWindow,
        config: AppConfig,
        services: ServiceProvider | None = None,
        save_config: Callable[[], None] | None = None,
        planning_config: PlanningPosteConfig | None = None,
        planning_store_factory: PlanningStoreFactory | None = None,
        save_planning_config: Callable[[PlanningPosteConfig], None] | None = None,
        outlook_calendar_factory: OutlookCalendarFactory | None = None,
    ) -> None:
        """Without ``planning_config`` and ``planning_store_factory`` the planning stays off.

        ``outlook_calendar_factory`` opens the Outlook calendars (the copy of the planning
        for the phones); without it that copy is not offered.
        """
        self._window = window
        self._config = config
        self._services = services or ServiceContainer(config, interactive_sign_in=False)
        self._save_config = save_config
        self._planning_store_factory = planning_store_factory
        self._save_planning_config = save_planning_config
        self._outlook_calendar_factory = outlook_calendar_factory
        self._planning: PlanningController | None = None
        self._planning_started = False
        # Planning work is logged, never shown as an error dialog, and is closed apart:
        # its sync engine must still send the last changes when the others are cancelled.
        self._planning_tasks: set[asyncio.Task[None]] = set()
        self._member_tasks: dict[tuple[int, str], asyncio.Task[PlannerMembers]] = {}
        self._notify: Notifier | None = None
        self._showing_in_repertoire = False
        self._repertoire_load_task: asyncio.Task[Any] | None = None
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
        # A load stuck on a retired connection must not block the next configuration.
        self._repertoire_loading_generation: int | None = None
        self._reset_requested = False
        self._connection_check_generation = 0
        self._staged_update: StagedUpdate | None = None
        self._install_plan: InstallPlan | None = None
        self._update_check_running = False
        self._install_update_now = False
        self._declined_update = ""
        self._update_idle_polls = 0
        self._update_check_timer = QTimer(window)
        self._update_check_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self._update_check_timer.timeout.connect(
            lambda: self.request_update_check(show_no_update=False),
        )
        self._update_install_timer = QTimer(window)
        self._update_install_timer.setInterval(UPDATE_IDLE_POLL_MS)
        self._update_install_timer.timeout.connect(self._install_staged_update_when_idle)
        self._connect()
        if planning_config is not None and planning_store_factory is not None:
            self._planning = PlanningController(
                self._window.planning_tab,
                poste_config=planning_config,
                store_factory=self._open_planning_store,
                hooks=self._planning_hooks(),
            )

    @property
    def planning(self) -> PlanningController | None:
        return self._planning

    def set_notifier(self, notify: Notifier | None) -> None:
        """Where to announce what the planning did on its own (a card « À planifier »)."""
        self._notify = notify

    def _connect(self) -> None:
        tab = self._window.creation_tab
        tab.create_requested.connect(lambda: self._schedule_task(self.create_project()))
        self._window.update_confirmed.connect(lambda: self._schedule_task(self.update_project()))
        tab.load_requested.connect(self.load_project)
        tab.open_folder_requested.connect(self.open_folder)
        tab.open_fiche_requested.connect(self.open_fiche)
        tab.print_fiche_requested.connect(self.print_fiche)
        tab.print_after_save_checkbox.setChecked(self._config.printing.print_fiche_on_save)
        tab.print_after_save_checkbox.toggled.connect(self._remember_print_after_save)
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
        self._window.repertoire_tab.open_folder_requested.connect(
            self.open_folder_from_repertoire,
        )
        self._window.repertoire_tab.open_fiche_requested.connect(
            self.open_fiche_from_repertoire,
        )
        self._window.repertoire_tab.print_fiche_requested.connect(
            self.print_fiche_from_repertoire,
        )
        self._window.repertoire_tab.prepare_sortie_requested.connect(
            self.prepare_sortie_from_repertoire,
        )
        self._window.repertoire_tab.new_project_with_number_requested.connect(
            self.new_project_with_repertoire_number,
        )
        self._window.tabs.currentChanged.connect(self._on_tab_changed)
        tab.planner_options_requested.connect(
            lambda: self._schedule_task(self._load_planner_options(tab.planner_widget)),
        )
        tab.client_suggestions_requested.connect(
            lambda: self._request_client_suggestions(tab),
        )
        self._window.settings_requested.connect(self.open_settings)
        self._window.microsoft_sign_in_requested.connect(
            lambda: self._schedule_task(self.reconnect_microsoft()),
        )
        self._window.connection_check_requested.connect(self.request_connection_check)
        self._window.reset_requested.connect(self.request_reset)
        self._window.quick_create_requested.connect(self.show_quick_create)
        self._window.update_check_requested.connect(
            self.request_update_check,
        )
        self._window.presence_changed.connect(self._update_planning_visibility)

    def show_window(self) -> None:
        self._window.show_and_raise()

    def show_planning(self) -> None:
        self._window.show_planning_tab()

    # ------------------------------------------------------------ planning ----

    def start_planning(self) -> None:
        """Load the planning once the window is shown; the window never waits for it."""
        if self._planning is None or self._planning_started or self._closing:
            return
        self._planning_started = True
        self._schedule_planning(self._start_planning(self._planning))

    async def _start_planning(self, planning: PlanningController) -> None:
        await planning.start()
        self._update_planning_visibility()

    def _planning_hooks(self) -> PlanningHooks:
        return PlanningHooks(
            read_repertoire=self._read_snapshot,
            open_project_dir=self.open_project_dir,
            open_fiche=self._deferred(self.open_project_fiche),
            show_in_repertoire=self.show_in_repertoire,
            load_in_creation=self._deferred(self.load_in_creation),
            prepare_sortie=self.prepare_sortie,
            open_settings=self._deferred(self.open_settings),
            planner_members=self._planner_members,
            set_nav_badge=self._set_planning_badge,
            schedule=self._schedule_planning,
            ask=self._ask,
            log=self._log,
            save_config=self._store_planning_config,
            open_outlook_calendar=(
                self._open_outlook_calendar if self._outlook_calendar_factory else None
            ),
            read_outlook_selection=(
                _read_outlook_selection if sys.platform.startswith("win") else None
            ),
        )

    async def _open_outlook_calendar(self) -> CalendarGateway:
        factory = self._outlook_calendar_factory
        if factory is None:
            raise ProjectFlowError("Calendrier Outlook indisponible sur ce poste.")
        planning = self._planning
        if planning is None:
            return await factory(interactive=False)
        config = planning.poste_config
        return await factory(
            interactive=False, via=config.outlook_via, mailbox=config.outlook_mailbox
        )

    async def connect_outlook_calendar(
        self,
        dialog: SettingsDialog,
        name: str,
        via: str = "graph",
        mailbox: str = "",
    ) -> None:
        """Find or create the calendar ``name``, after the Microsoft permission (``graph``)."""
        route: OutlookRoute = "desktop" if via == "desktop" else "graph"
        factory = self._outlook_calendar_factory
        if factory is None:
            dialog.outlook_connect_finished("Calendrier Outlook indisponible.", connected=False)
            return
        calendar = " ".join(name.split()) or DEFAULT_OUTLOOK_CALENDAR
        try:
            gateway = await factory(interactive=True, via=route, mailbox=mailbox)
            await gateway.ensure_calendar(calendar)
        except ProjectFlowError as exc:
            self._log(f"-> Calendrier Outlook : {exc}")
            dialog.outlook_connect_finished(f"Connexion impossible : {exc}", connected=False)
            return
        except Exception:
            dialog.outlook_connect_finished("Connexion interrompue.", connected=False)
            raise
        where = f" dans {mailbox.strip()}" if route == "desktop" and mailbox.strip() else ""
        self._log(f"+ Calendrier Outlook « {calendar} »{where} prêt.")
        dialog.outlook_connect_finished(
            f"Calendrier « {calendar} »{where} prêt. Cochez la copie, enregistrez, puis publiez-le "
            "depuis Outlook sur le web.",
            connected=True,
        )

    def _deferred(self, action: Callable[[str], None]) -> Callable[[str], None]:
        """Run ``action`` from the Qt loop: a dialog it opens never blocks a running task."""

        def defer(value: str) -> None:
            if not self._closing:
                QTimer.singleShot(0, lambda: action(value))

        return defer

    def _schedule_planning(self, coroutine: Coroutine[Any, Any, None]) -> None:
        if self._closing:
            coroutine.close()
            return
        task = asyncio.create_task(self._run_planning_task(coroutine))
        self._planning_tasks.add(task)
        task.add_done_callback(self._planning_tasks.discard)

    async def _run_planning_task(self, coroutine: Coroutine[Any, Any, None]) -> None:
        try:
            await coroutine
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the planning never stops the other work
            get_logger(__name__).exception("planning.task.failed")
            self._log(f"! Planning : {exc}")

    async def _open_planning_store(self, poste_config: PlanningPosteConfig) -> PlanningStore:
        factory = self._planning_store_factory
        if factory is None:
            raise ProjectFlowError("Planning non disponible sur ce poste.")
        known = _resolved_ids(poste_config)
        store = await factory(poste_config)
        if _resolved_ids(poste_config) != known:
            # Found once: the next start opens the SharePoint list without searching it,
            # and the OneDrive planning in the cache of its drive.
            self._store_planning_config(poste_config)
        return store

    def _store_planning_config(self, poste_config: PlanningPosteConfig) -> None:
        if self._save_planning_config is None:
            return
        try:
            self._save_planning_config(poste_config)
        except (ProjectFlowError, OSError) as exc:
            self._log(f"! Réglages du planning non enregistrés : {exc}")

    def _set_planning_badge(self, count: int) -> None:
        self._window.set_nav_badge(self._window.planning_index, count)

    def _update_planning_visibility(self) -> None:
        planning = self._planning
        if planning is None or not self._planning_started or self._closing:
            return
        window = self._window
        shown = window.isVisible() and not window.isMinimized()
        planning.set_visible(
            visible=shown and window.tabs.currentWidget() is window.planning_tab,
            window_active=shown and window.isActiveWindow(),
        )

    async def _planner_members(self) -> PlannerMembers:
        """Members of the Planner plan's group (id, name, e-mail); none without Planner."""
        planner = self._config.planner
        plan_id = planner.target_plan_id
        if not planner.enabled or not plan_id:
            return ()
        key = (self._planner_generation, plan_id)
        task = self._member_tasks.get(key)
        if task is None:
            task = asyncio.create_task(_fetch_planner_members(plan_id))
            self._member_tasks[key] = task
            # Kept once read: the roster dialog and the settings ask for them again.
            task.add_done_callback(lambda done: _forget_failed(self._member_tasks, key, done))
        try:
            return await asyncio.shield(task)
        except (ProjectFlowError, OSError, ValueError) as exc:
            self._log(f"! Membres Microsoft 365 indisponibles : {exc}")
            return ()

    async def _offer_planning_members(self, dialog: SettingsDialog) -> None:
        members = await self._planner_members()
        if members and dialog.isVisible():
            dialog.set_planning_members(members)

    async def _reconfigure_planning(
        self,
        planning: PlanningController,
        poste_config: PlanningPosteConfig,
    ) -> None:
        try:
            await planning.reconfigure(poste_config)
        except BaseException:
            # The choice of the user is kept for the next start all the same.
            self._store_planning_config(poste_config)
            raise
        self._store_planning_config(planning.poste_config)

    async def _add_sortie_card(self, number: ProjectNumber, fiche_path: Path) -> None:
        """Add the « Fabrication » card of the dossier to « À planifier » after a sortie."""
        planning = self._planning
        if planning is None:
            return
        details = await self._sortie_card_details(number, fiche_path)
        created = await planning.ensure_backlog_card(
            str(number),
            designation=details.designation,
            client=details.societe,
            contact=details.contact,
            managed_by=details.gere_par,
            released=date.today(),
        )
        if not created:
            return
        message = f"{number} ajouté à planifier"
        self._window.sortie_tab.append_log(f"+ Planning : {message}")
        if self._notify is not None:
            self._notify("Planning", message)

    async def _sortie_card_details(self, number: ProjectNumber, fiche_path: Path) -> FicheData:
        """Dossier details from the fiche, completed from the répertoire row."""
        data = FicheData()
        try:
            data = await run_file_io(self._services.fiche().read_fiche, fiche_path)
        except Exception:  # noqa: BLE001 - an unreadable fiche falls back on the répertoire
            get_logger(__name__).warning("planning.sortie_card.fiche_unreadable")
        if data.designation and data.societe and data.contact:
            return data
        try:
            snapshot = await self._read_snapshot(number.year)
        except (ProjectFlowError, ValueError, OSError):
            return data
        row = next((row for row in snapshot.rows if row.number == str(number)), None)
        if row is None:
            return data
        return replace(
            data,
            societe=data.societe or _repertoire_cell_text(row.values[2]),
            contact=data.contact or _repertoire_cell_text(row.values[3]),
            designation=data.designation or _repertoire_cell_text(row.values[4]),
        )

    async def _close_planning(self) -> None:
        planning = self._planning
        if planning is not None:
            try:
                await asyncio.wait_for(planning.aclose(), timeout=PLANNING_CLOSE_TIMEOUT_SECONDS)
            except Exception:  # noqa: BLE001 - closing goes on with the other services
                get_logger(__name__).exception("planning.close.failed")
        pending = set(self._planning_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ------------------------------------------------- dossier by number ----

    def open_project_dir(self, number_text: str) -> None:
        try:
            number = parse_project_number(number_text)
        except ValueError as exc:
            self._error(str(exc))
            return
        self._open_number_dir(number)

    def open_project_fiche(self, number_text: str) -> None:
        if self._mutation_busy:
            self._log("-> Patientez jusqu'a la fin de l'operation avant d'ouvrir la fiche.")
            return
        try:
            number = parse_project_number(number_text)
            project_dir = self._project_dir(number)
            fiche_path = standard_fiche_path(project_dir, number)
            if not fiche_path.exists():
                fiche_path = self._choose_fiche(project_dir, number)
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def load_in_creation(self, number_text: str) -> None:
        try:
            number = parse_project_number(number_text)
        except ValueError as exc:
            self._error(str(exc))
            return
        if self._load_project_number(number):
            self._window.show_creation_tab()

    def prepare_sortie(self, number_text: str) -> None:
        try:
            number = parse_project_number(number_text)
        except ValueError as exc:
            self._error(str(exc))
            return
        tab = self._window.sortie_tab
        tab.set_project_identity(year=str(number.year), project_id=str(number))
        self._window.tabs.setCurrentWidget(tab)
        self._schedule_task(self.load_sortie_dossier())

    def show_in_repertoire(self, number_text: str) -> None:
        try:
            number = parse_project_number(number_text)
        except ValueError as exc:
            self._error(str(exc))
            return
        self._schedule_task(self._show_in_repertoire(number))

    async def _show_in_repertoire(self, number: ProjectNumber) -> None:
        tab = self._window.repertoire_tab
        tab.search_edit.clear()
        tab.set_year(number.year)
        self._showing_in_repertoire = True
        try:
            # Read below, once the year is set: not also by the tab change.
            self._window.tabs.setCurrentWidget(tab)
        finally:
            self._showing_in_repertoire = False
        running = self._repertoire_load_task
        if running is not None and not running.done():
            await asyncio.wait({running})
        if self._select_repertoire_row(str(number)):
            return
        await self.load_repertoire()
        if not self._select_repertoire_row(str(number)):
            tab.set_status_message(f"{number} est introuvable dans le répertoire {number.year}.")

    def _select_repertoire_row(self, number_text: str) -> bool:
        table = self._window.repertoire_tab.table
        model = table.model()
        for row in range(model.rowCount()):
            index = model.index(row, 0)
            if _repertoire_cell_text(index.data()) == number_text:
                table.selectRow(row)
                table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
                table.setFocus()
                return True
        return False

    @property
    def reset_requested(self) -> bool:
        """True once the user confirmed the full reset: the app erases its data on exit."""
        return self._reset_requested

    def request_connection_check(self) -> None:
        self._schedule_task(self.check_microsoft_connection())

    async def check_microsoft_connection(self) -> None:
        """Test the stored Microsoft connection without ever opening the browser."""
        self._connection_check_generation += 1
        generation = self._connection_check_generation
        if not uses_microsoft(self._config):
            self._window.set_microsoft_status("hidden")
            return
        self._window.set_microsoft_status("checking")
        state, detail = await self._connection_state()
        if generation != self._connection_check_generation:
            return  # A sign-in or a newer check already reported the state.
        self._window.set_microsoft_status(state, detail)
        if state == "disconnected":
            self._log(f"-> {detail}")
        elif state == "warning":
            self._log("-> Planner: cliquez sur le bouton Microsoft en haut pour l'autoriser.")

    async def _connection_state(self) -> tuple[MicrosoftState, str]:
        client_id = ApplicationSettings.load().microsoft_client_id
        provider = MsalAccessTokenProvider(client_id=client_id, allow_interactive=False)
        try:
            username = await asyncio.wait_for(
                provider.signed_in_account(),
                timeout=CONNECTION_CHECK_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return "disconnected", (
                f"Microsoft ne repond pas en {CONNECTION_CHECK_TIMEOUT_SECONDS} s. "
                "Verifiez la connexion internet."
            )
        except ProjectFlowError as exc:
            return "disconnected", str(exc)
        if self._config.planner.enabled:
            planner = MsalAccessTokenProvider(
                client_id=client_id,
                scopes=PLANNER_GRAPH_SCOPES,
                allow_interactive=False,
            )
            try:
                await asyncio.wait_for(
                    planner.access_token(),
                    timeout=CONNECTION_CHECK_TIMEOUT_SECONDS,
                )
            except (TimeoutError, ProjectFlowError):
                return "warning", "Planner à autoriser"
        return "connected", username or "connecté"

    @property
    def install_plan(self) -> InstallPlan | None:
        """The installer to start once the app has closed everything, or None."""
        return self._install_plan

    def start_update_checks(self) -> None:
        """Look for a release now, then every few hours while the app keeps running."""
        self.request_update_check(show_no_update=False)
        self._update_check_timer.start()

    def request_update_check(self, *, show_no_update: bool = True) -> None:
        self._schedule_task(self.check_updates(show_no_update=show_no_update))

    def announce_update_installed(self) -> None:
        """Say once, after the installer restarted the app, which version now runs."""
        self._log(f"+ ProjectFlow mis à jour : version {__version__}")
        if self._notify is not None:
            self._notify("ProjectFlow mis à jour", f"Version {__version__} installée.")

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
        tab.create_button.setText("Opération en cours…" if busy else "Créer")
        if busy:
            tab.show_operation_progress("Opération en cours")
        else:
            tab.clear_operation_progress()

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
            assignees=default_assignees(self._config.planner),
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
            self._window.creation_tab.show_operation_progress(
                f"Création en cours — {project.number}",
            )
            created = await self._create_project_for_input(project)
            if created is None:
                return
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        result, existing_update = created
        self._window.creation_tab.show_operation_success(
            f"Projet {project.number} créé"
            if result.project_dir_created
            else f"Projet {project.number} prêt",
        )
        self._window.creation_tab.reset_cad_options()
        self._save_config_if_available()
        self._log_creation_result(result, existing_update=existing_update)
        self._log_creation_integrations(result)
        await self._print_saved_fiche(result, project.number)
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
        existing_update = await self._existing_project_update_decision(project)
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
        await self._print_saved_fiche(result, project.number)

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
        self._update_planning_visibility()
        if (
            index != self._window.tabs.indexOf(self._window.repertoire_tab)
            or self._showing_in_repertoire
        ):
            return
        self._schedule_task(self.load_repertoire())

    async def load_repertoire(self) -> None:
        if self._repertoire_loading_generation == self._repertoire_generation or self._closing:
            return
        tab = self._window.repertoire_tab
        try:
            year = tab.year()
        except ValueError:
            tab.set_error("L'année doit être un nombre valide.")
            return
        generation = self._repertoire_generation
        self._repertoire_loading_generation = generation
        self._repertoire_load_task = asyncio.current_task()
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
            if self._repertoire_load_task is asyncio.current_task():
                self._repertoire_load_task = None
            if self._repertoire_loading_generation == generation:
                self._repertoire_loading_generation = None
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
            fiche_path = await self._ask(lambda: self._choose_fiche(project_dir, number))
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

    def new_project_with_repertoire_number(self) -> None:
        """Prepare the form on the free number of the selected row."""
        payload = self._window.repertoire_tab.selected_row_payload()
        if payload is None:
            self._error("Sélectionnez une ligne de projet dans le répertoire.")
            return
        _row_index, values, _original = payload
        try:
            number = parse_project_number(_repertoire_cell_text(values[0]))
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        if any(_repertoire_cell_text(value) for value in values[1:]):
            self._error(f"Le numéro {number} est déjà utilisé par un projet.")
            return
        self._window.creation_tab.reset_form_fields()
        self._window.creation_tab.set_project_identity(
            year=str(number.year),
            project_id=number.project_id,
            subproject_id=number.subproject_id or "",
        )
        self._window.show_creation_tab()
        self._log(f"+ Nouveau projet préparé sur le numéro {number}")

    def open_project_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        _row_index, number, _values, _original = selected
        if self._load_project_number(number):
            self._window.show_creation_tab()

    def open_folder_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is not None:
            self._open_number_dir(selected[1])

    def open_fiche_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is not None:
            self.open_project_fiche(str(selected[1]))

    def print_fiche_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is None:
            return
        if self._mutation_busy:
            self._log("-> Patientez jusqu'a la fin de l'operation avant d'imprimer la fiche.")
            return
        number = selected[1]
        try:
            project_dir = self._project_dir(number)
            fiche_path = standard_fiche_path(project_dir, number)
            if not fiche_path.exists():
                fiche_path = self._choose_fiche(project_dir, number)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        self._schedule_task(self._print_fiche(fiche_path))

    def prepare_sortie_from_repertoire(self) -> None:
        selected = self._selected_repertoire_project()
        if selected is not None:
            self.prepare_sortie(str(selected[1]))

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
        self._show_dialog(lambda: QMessageBox.information(self._window, "Projet supprimé", message))

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
            fiche_path = selection.fiche_path
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
        # Before the question: the card reaches the other postes while it waits. Apart
        # from the sortie, so that a planning failure is only logged.
        self._schedule_planning(self._add_sortie_card(number, fiche_path))
        if self._closing:
            return
        answer = await self._ask(
            lambda: QMessageBox.question(
                self._window,
                "Sortie dossier",
                f"Le dossier de sortie a ete cree ici :\n{output_dir}\n\n"
                "Voulez-vous l'ouvrir maintenant ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            ),
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
            fiche_path = self._form_fiche_path()
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def print_fiche(self) -> None:
        if self._mutation_busy:
            self._log("-> Patientez jusqu'a la fin de l'operation avant d'imprimer la fiche.")
            return
        try:
            fiche_path = self._form_fiche_path()
        except (ProjectFlowError, ValueError, OSError) as exc:
            self._error(str(exc))
            return
        self._schedule_task(self._print_fiche(fiche_path))

    async def _print_saved_fiche(
        self,
        result: ProjectCreationResult,
        number: ProjectNumber,
    ) -> None:
        if not self._window.creation_tab.print_after_save_checkbox.isChecked():
            return
        try:
            fiche_path = self._fiche_path_from_result(result, number)
        except (ProjectFlowError, OSError) as exc:
            self._error(f"Fiche non imprimée: {exc}")
            return
        await self._print_fiche(fiche_path)

    async def _print_fiche(self, fiche_path: Path) -> None:
        if self._closing:
            return
        try:
            options = await self._ask(lambda: self._ask_print_options(fiche_path))
        except ProjectFlowError as exc:
            self._error(str(exc))
            return
        if options is None:
            self._log("-> Impression de la fiche annulée")
            return
        self._log(f"-> Impression de {fiche_path.name} sur {options.printer_name}...")
        try:
            printer = await run_file_io(
                print_workbook_a4,
                fiche_path,
                printer_name=options.printer_name,
                copies=options.copies,
            )
        except (ProjectFlowError, OSError) as exc:
            self._error(f"Fiche non imprimée: {exc}")
            return
        self._log(f"+ Fiche envoyée à l'imprimante {printer}")

    def _ask_print_options(self, fiche_path: Path) -> PrintFicheOptions | None:
        printers, default_printer = installed_printers()
        if not printers:
            raise PrintError("Aucune imprimante n'est installée sur ce poste.")
        dialog = PrintFicheDialog(
            fiche_name=fiche_path.name,
            printers=printers,
            preferred_printer=self._config.printing.printer_name,
            default_printer=default_printer,
            parent=self._window,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return None
        options = dialog.options()
        if options.printer_name != self._config.printing.printer_name:
            self._config.printing.printer_name = options.printer_name
            self._save_config_if_available()
        return options

    def _remember_print_after_save(self, checked: bool) -> None:  # noqa: FBT001
        self._config.printing.print_fiche_on_save = checked
        self._save_config_if_available()

    def _form_fiche_path(self) -> Path:
        number = parse_project_number(self._number_from_form())
        project_dir = self._project_dir(number)
        fiche_path = standard_fiche_path(project_dir, number)
        if not fiche_path.exists():
            fiche_path = self._services.fiche().locate_fiche(project_dir, number)
        return fiche_path

    def open_folder(self) -> None:
        try:
            number = parse_project_number(self._number_from_form())
        except (ProjectFlowError, ValueError) as exc:
            self._error(str(exc))
            return
        self._open_number_dir(number)

    def _open_number_dir(self, number: ProjectNumber) -> None:
        try:
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

    def open_settings(self, section: str | None = None) -> None:
        if self._mutation_busy:
            self._log("-> Attendez la fin de l'operation avant de modifier les parametres.")
            return
        dialog = self._settings_dialog(section)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._apply_planning_settings(dialog)
        previous_repertoire = self._config.paths.repertoire_chantier.model_copy(deep=True)
        previous_planner = self._config.planner.model_copy(deep=True)
        dialog.apply_to_config(self._config)
        app = QApplication.instance()
        if isinstance(app, QApplication) and app.styleSheet():
            apply_theme(app, self._config.appearance)
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
        if repertoire_changed:
            self._reset_repertoire_connection()
        if planner_changed:
            self._reset_planner_connection()
        self._window.apply_config_labels()
        self._apply_quick_config(planner_changed=previous_planner != self._config.planner)
        self._save_config_if_available()
        self._log("+ Parametres enregistres")
        self._log_integration_settings()
        if repertoire_changed or planner_changed:
            self.request_connection_check()

    def _settings_dialog(self, section: str | None) -> SettingsDialog:
        planning = self._planning
        dialog = SettingsDialog(
            self._config,
            parent=self._window,
            planning_config=planning.poste_config if planning is not None else None,
        )
        dialog.microsoft_sign_in_requested.connect(
            lambda: self._schedule_task(self.reconnect_microsoft(dialog)),
        )
        dialog.reset_requested.connect(lambda: self.request_reset(dialog))
        if planning is not None:
            # The shared roster is applied at once, in its own dialog, never on OK.
            dialog.planning_team_requested.connect(planning.open_team_dialog)
            dialog.set_outlook_available(available=self._outlook_calendar_factory is not None)
            dialog.outlook_connect_requested.connect(
                lambda name, via, mailbox: self._schedule_planning(
                    self.connect_outlook_calendar(dialog, name, via, mailbox)
                ),
            )
            dialog.planning_copy_requested.connect(
                lambda folder: self._schedule_planning(
                    self._copy_planning_to_onedrive(dialog, folder),
                ),
            )
            if self._config.planner.enabled and self._config.planner.target_plan_id:
                self._schedule_planning(self._offer_planning_members(dialog))
        if section:
            dialog.show_section(section)
        return dialog

    def _apply_planning_settings(self, dialog: SettingsDialog) -> None:
        planning = self._planning
        if planning is None:
            return
        # A copy: the planning compares it with its current settings to know what changed.
        poste_config = planning.poste_config.model_copy(deep=True)
        dialog.apply_to_planning_config(poste_config)
        self._schedule_planning(self._reconfigure_planning(planning, poste_config))

    async def _copy_planning_to_onedrive(self, dialog: SettingsDialog, folder: str) -> None:
        """Add this poste's local planning to the OneDrive folder, after confirmation.

        Nothing in OneDrive is replaced (``copy_planning``). When the planning already
        uses that folder, it synchronises at once to show what was copied; otherwise
        « Enregistrer » with OneDrive selected switches to it.
        """
        try:
            report = await self._run_planning_copy(dialog, folder)
        except (ProjectFlowError, OSError) as exc:
            self._log(f"! Copie du planning vers OneDrive impossible : {exc}")
            _copy_finished(dialog, f"Copie impossible : {exc}", copied=False)
            return
        except BaseException:
            _copy_finished(dialog, "Copie interrompue.", copied=False)
            raise
        if report is None:
            _copy_finished(dialog, "Copie annulée.", copied=False)
            return
        self._log(f"+ Planning copié vers OneDrive « {folder} » : {report.summary()}")
        _copy_finished(dialog, f"Copie terminée : {report.summary()}", copied=not report.failed)
        planning = self._planning
        if (
            planning is not None
            and planning.poste_config.backend == "onedrive"
            and planning.poste_config.onedrive_folder.casefold() == folder.casefold()
        ):
            planning.wake()

    async def _run_planning_copy(
        self,
        dialog: SettingsDialog,
        folder: str,
    ) -> CopyReport | None:
        """Return the copy report, or None when the person declined the copy."""
        planning = self._planning
        factory = self._planning_store_factory
        if planning is None or factory is None:
            raise ProjectFlowError("Planning non disponible sur ce poste.")
        source_directory = planning.poste_config.local_store_directory()
        target_config = planning.poste_config.model_copy(deep=True)
        target_config.backend = "onedrive"
        target_config.onedrive_folder = folder
        stores: list[PlanningStore] = []
        try:
            stores.append(LocalPlanningStore(source_directory))
            stores.append(await factory(target_config))
            source, target = stores
            preview = await copy_planning(source, target, dry_run=True)
            if not preview.created:
                return preview
            answer = await self._ask(
                lambda: QMessageBox.question(
                    dialog if dialog.isVisible() else self._window,
                    "Copier le planning vers OneDrive",
                    f"{preview.created} élément(s) du planning local de ce poste vont être "
                    f"ajoutés au dossier OneDrive « {folder} ».\n"
                    f"{preview.skipped_existing} élément(s) y sont déjà : ils ne sont pas "
                    "remplacés.\n\nContinuer ?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            return await copy_planning(source, target)
        finally:
            for store in stores:
                try:
                    await store.aclose()
                except Exception:  # noqa: BLE001 - closing never hides the copy's outcome
                    get_logger(__name__).exception("planning.copy.close_failed")

    async def reconnect_microsoft(self, dialog: SettingsDialog | None = None) -> None:
        """Open the Microsoft sign-in at once; nothing is saved or reloaded meanwhile."""
        # A sign-in still waiting (browser never shown) would otherwise hold the
        # browser sign-in turn for several minutes.
        cancel_pending_sign_ins()
        self._connection_check_generation += 1
        self._window.set_microsoft_status("checking")
        client_id = ApplicationSettings.load().microsoft_client_id
        provider = MsalAccessTokenProvider(client_id=client_id)
        try:
            username = await provider.sign_in()
        except ProjectFlowError as exc:
            self._log(f"-> {exc}")
            if dialog is not None:
                dialog.microsoft_sign_in_finished(str(exc))
            # A cancelled sign-in keeps the previous account: show what still works.
            self.request_connection_check()
            return
        except Exception:
            if dialog is not None:
                dialog.microsoft_sign_in_finished("Connexion Microsoft interrompue.")
            self.request_connection_check()
            raise
        # The services still hold tokens of the previous account in memory.
        self._reset_repertoire_connection()
        self._reset_planner_connection()
        account = username or "compte Microsoft"
        self._log(f"+ Connecte a Microsoft: {account}")
        state: MicrosoftState = "connected"
        detail = account
        if self._config.planner.enabled:
            # Same account: the browser only asks for Planner consent if it is missing.
            planner = MsalAccessTokenProvider(client_id=client_id, scopes=PLANNER_GRAPH_SCOPES)
            try:
                await planner.access_token()
            except ProjectFlowError as exc:
                self._log(f"-> Planner: {exc}")
                state, detail = "warning", "Planner à autoriser"
        self._window.set_microsoft_status(state, detail)
        if dialog is not None:
            dialog.microsoft_sign_in_finished(f"Connecté : {account}")

    def request_reset(self, parent: QWidget | None = None) -> None:
        """Erase everything ProjectFlow stores, after confirmation, then restart it."""
        planning = self._planning
        erased, kept = planning_reset_notice(
            planning.poste_config if planning is not None else None,
        )
        answer = QMessageBox.warning(
            parent or self._window,
            "Tout reinitialiser ProjectFlow",
            "ProjectFlow va effacer sur ce poste :\n"
            f"{erased}"
            "- la connexion Microsoft ;\n"
            "- tous les parametres (chemins, Outlook, Planner, modeles CAO, apparence) ;\n"
            "- la cle Document Manager ;\n"
            "- les mises a jour telechargees.\n\n"
            f"Les dossiers projets et le repertoire chantier ne sont pas touches.{kept} Le "
            "journal est conserve. ProjectFlow redemarre ensuite sur l'assistant de premier "
            "lancement.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Reset:
            return
        self._reset_requested = True
        # Nothing may wait for a browser while the application shuts down.
        cancel_pending_sign_ins()
        if isinstance(parent, QDialog):
            parent.reject()
        self._log("+ Reinitialisation complete: ProjectFlow redemarre.")
        QTimer.singleShot(0, self._quit_for_reset)

    def _quit_for_reset(self) -> None:
        self._window.request_quit()
        QApplication.quit()

    def _reset_repertoire_connection(self) -> None:
        if isinstance(self._services, ServiceContainer):
            self._services.reset_repertoire()
        self._invalidate_repertoire_data()

    def _reset_planner_connection(self) -> None:
        if isinstance(self._services, ServiceContainer):
            self._services.reset_planner()
        self._planner_generation += 1
        self._planner_cache_plan = ""
        self._planner_bucket_options = []
        self._planner_member_options = []

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
                assignees=default_assignees(self._config.planner),
            )

    async def check_updates(self, *, show_no_update: bool = True) -> None:
        """Find a newer release and install it; ``show_no_update`` marks the user's own check.

        The installed Windows app downloads it in the background and installs it without a
        question: at once when the user asked, otherwise when the window is hidden. Elsewhere
        (macOS, a release without installer) the user is asked first, as before.
        """
        if self._update_check_running:
            if show_no_update:
                # The download already running installs as soon as it ends.
                self._install_update_now = True
                self._log("-> Recherche de mise à jour déjà en cours.")
            return
        self._update_check_running = True
        try:
            await self._check_updates(requested=show_no_update)
        finally:
            self._update_check_running = False
            self._install_update_now = False

    async def _check_updates(self, *, requested: bool) -> None:
        try:
            update = await GitHubReleaseChecker(ApplicationSettings.load()).check(
                current_version=__version__,
            )
        except (ProjectFlowError, OSError) as exc:
            self._update_failed(str(exc), requested=requested)
            return
        if update is None:
            if requested:
                self._log("+ ProjectFlow est a jour")
            return
        asset = select_platform_asset(update)
        if asset is None:
            if requested or self._declined_update != update.latest_version:
                self._log(
                    f"! Mise a jour disponible sans artefact compatible: {update.release_url}",
                )
            self._declined_update = update.latest_version
            return
        if silent_updates_supported() and installs_unattended(asset):
            await self._update_unattended(update, asset, requested=requested)
        else:
            await self._offer_update(update, asset, requested=requested)

    async def _update_unattended(
        self,
        update: UpdateInfo,
        asset: ReleaseAsset,
        *,
        requested: bool,
    ) -> None:
        try:
            staged = await self._stage_update(update, asset)
        except (ProjectFlowError, OSError) as exc:
            self._update_failed(str(exc), requested=requested)
            return
        if staged.exhausted:
            # The installer failed without a window: show it and let the user decide.
            await self._offer_update(update, asset, requested=requested)
            return
        self._staged_update = staged
        if requested or self._install_update_now:
            await self._install_staged_update(relaunch="window", unattended=False)
        elif not self._update_install_timer.isActive():
            self._update_idle_polls = 0
            self._update_install_timer.start()

    def _update_failed(self, message: str, *, requested: bool) -> None:
        if requested:
            self._error(message)
            return
        # A background check never interrupts: offline, it simply tries again later.
        get_logger(__name__).warning("update.check.failed", error=message)

    async def _stage_update(self, update: UpdateInfo, asset: ReleaseAsset) -> StagedUpdate:
        """Download the installer once; later checks find it already on this poste."""
        known = load_staged_update(current_version=__version__)
        attempts = 0
        if known is not None and known.version == update.latest_version:
            if known.exhausted or await asyncio.to_thread(verify_staged_installer, known):
                return known
            attempts = known.attempts
        installer = await UpdateDownloader().download(
            asset,
            version=update.latest_version,
            checksum_asset=select_checksum_asset(update, asset),
        )
        staged = StagedUpdate(
            version=update.latest_version,
            installer=installer,
            sha256=await asyncio.to_thread(file_sha256, installer),
            attempts=attempts,
        )
        save_staged_update(staged)
        self._log(f"+ Mise à jour {staged.version} téléchargée")
        return staged

    def _install_staged_update_when_idle(self) -> None:
        if self._staged_update is None or self._install_plan is not None:
            self._update_install_timer.stop()
            return
        if not self._unattended_install_allowed():
            self._update_idle_polls = 0
            return
        self._update_idle_polls += 1
        if self._update_idle_polls < UPDATE_IDLE_POLLS:
            return
        self._update_idle_polls = 0
        self._schedule_task(self._install_staged_update(relaunch="background", unattended=True))

    def _unattended_install_allowed(self) -> bool:
        """Tell whether a restart would go unnoticed: window hidden, no dialog, no work."""
        if self._closing or self._mutation_busy or self._mutation_tasks:
            return False
        if self._update_check_running or self._window.isVisible():
            return False
        return not any(
            isinstance(widget, QDialog) and widget.isVisible()
            for widget in QApplication.topLevelWidgets()
        )

    async def _install_staged_update(self, *, relaunch: RelaunchMode, unattended: bool) -> None:
        staged = self._staged_update
        if staged is None or self._closing or self._install_plan is not None:
            return
        if not unattended and self._mutation_busy:
            self._log(
                f"-> Mise à jour {staged.version} prête : elle s'installera après "
                "l'opération en cours, dès que la fenêtre sera fermée.",
            )
            self._update_install_timer.start()
            return
        if not await asyncio.to_thread(verify_staged_installer, staged):
            self._drop_staged_update(staged, "installateur téléchargé introuvable ou modifié.")
            return
        if unattended and not self._unattended_install_allowed():
            return  # The window came back meanwhile: the next quiet moment will do.
        try:
            plan = begin_staged_install(staged, relaunch=relaunch)
        except (ProjectFlowError, OSError) as exc:
            self._drop_staged_update(staged, str(exc))
            return
        self._log(f"+ Mise à jour {staged.version} : installation, ProjectFlow redémarre")
        self._quit_to_install(plan)

    def _drop_staged_update(self, staged: StagedUpdate, reason: str) -> None:
        """Forget an installer that cannot run: the next check downloads it again."""
        self._staged_update = None
        discard_staged_update()
        get_logger(__name__).warning("update.install.failed", error=reason)
        self._log(f"! Mise à jour {staged.version} : {reason}")

    def _quit_to_install(self, plan: InstallPlan) -> None:
        # Started by the app once everything is closed: the planning sends its last
        # changes first, and the installer never finds the old version still running.
        self._install_plan = plan
        self._update_check_timer.stop()
        self._update_install_timer.stop()
        QApplication.quit()

    async def _offer_update(
        self,
        update: UpdateInfo,
        asset: ReleaseAsset,
        *,
        requested: bool,
    ) -> None:
        """Ask before downloading; a release refused once is not offered again by itself."""
        if not requested and self._declined_update == update.latest_version:
            return
        answer = await self._ask(
            lambda: QMessageBox.question(
                self._window,
                "Mise a jour disponible",
                _update_prompt_text(update.latest_version, update.release_notes),
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._declined_update = update.latest_version
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
            if not plan.should_quit_app:
                launch_install_plan(plan)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        self._log(f"+ Mise a jour telechargee: {downloaded_path.name}")
        if plan.should_quit_app:
            self._quit_to_install(plan)

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

    async def _existing_project_update_decision(self, project: ProjectInput) -> bool | None:
        if project.is_subproject:
            return True
        project_dir = self._project_dir(project.number)
        if not project_dir.exists():
            return True
        if not self._project_info_would_change(project_dir, project):
            return False

        answer = await self._ask(
            lambda: QMessageBox.question(
                self._window,
                "Projet existant",
                "Ce projet existe deja et les informations du formulaire different "
                "de la fiche existante.\n\n"
                "Oui : mettre a jour la fiche et le repertoire, puis relancer Outlook/epingle.\n"
                "Non : conserver les informations existantes et relancer seulement "
                "Outlook/epingle.\n"
                "Annuler : ne rien faire.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.No,
            ),
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
        self._show_dialog(
            lambda: QMessageBox.information(
                self._window,
                title,
                f"{message}{_cad_summary(result)}\n\nDossier:\n{result.project_dir}",
            ),
        )

    def _show_quick_creation_confirmation(
        self,
        result: ProjectCreationResult,
        data: CreationFormData,
        project: ProjectInput,
    ) -> None:
        self._show_dialog(lambda: self._run_quick_creation_confirmation(result, data, project))

    def _run_quick_creation_confirmation(
        self,
        result: ProjectCreationResult,
        data: CreationFormData,
        project: ProjectInput,
    ) -> None:
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
            fiche_path = self._fiche_path_from_result(result, project.number)
            opened = open_file_default_app(fiche_path)
        except (ProjectFlowError, OSError) as exc:
            self._error(str(exc))
            return
        if not opened:
            self._error("Impossible d'ouvrir la fiche avec l'application par defaut.")
            return
        self._log(f"+ Fiche ouverte: {fiche_path.name}")

    def _fiche_path_from_result(
        self,
        result: ProjectCreationResult,
        number: ProjectNumber,
    ) -> Path:
        fiche_path = (
            Path(result.fiche_path)
            if result.fiche_path is not None
            else standard_fiche_path(Path(result.project_dir), number)
        )
        if not fiche_path.exists():
            fiche_path = self._services.fiche().locate_fiche(Path(result.project_dir), number)
        return fiche_path

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
            *self._member_tasks.values(),
        }
        # Finish committed user actions, including their repertoire/integration writes.
        # Cancelling between the local save and these writes would leave a partial project.
        for task in tasks - self._mutation_tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # The planning is not among them: it sends its last changes, then saves its cache,
        # before its Microsoft connection closes with the other services.
        await self._close_planning()
        for dialog in self._window.findChildren(SettingsDialog):
            await dialog.aclose()
        if isinstance(self._services, ServiceContainer):
            await self._services.close()

    def _error(self, message: str) -> None:
        message = redact_sensitive_links(message)
        self._window.creation_tab.append_log(f"! {message}")
        self._window.creation_tab.show_operation_error(message)
        self._show_dialog(lambda: QMessageBox.critical(self._window, "ProjectFlow", message))

    def _output_error(self, message: str) -> None:
        message = redact_sensitive_links(message)
        self._window.sortie_tab.append_log(f"! {message}")
        self._show_dialog(lambda: QMessageBox.critical(self._window, "Sortie dossier", message))

    def _show_dialog(self, show: Callable[[], object]) -> None:
        # Never inside a task step: the modal loop would stall every other task.
        show_outside_task(show, skip=lambda: self._closing)

    async def _ask(self, ask: Callable[[], Answer]) -> Answer:
        return await ask_outside_task(ask, skip=lambda: self._closing)


def _copy_finished(dialog: SettingsDialog, message: str, *, copied: bool) -> None:
    if dialog.isVisible():
        dialog.planning_copy_finished(message, copied=copied)


def _resolved_ids(config: PlanningPosteConfig) -> tuple[str, str, str]:
    return (config.sharepoint_site_id, config.sharepoint_list_id, config.onedrive_drive_id)


def planning_reset_notice(config: PlanningPosteConfig | None) -> tuple[str, str]:
    """Say what « Tout reinitialiser » does to the planning: lines erased, then what stays.

    The shared planning (OneDrive or SharePoint) is never touched; the reset keeps the
    local planning kept on this poste from before OneDrive (``reset_local_data``).
    """
    default_store = PlanningPosteConfig(local_directory="").local_store_directory()
    kept_local = (
        " Le planning enregistre sur ce poste (taches, personnes, absences) est conserve."
        if _holds_files(default_store)
        else ""
    )
    erased = "- les reglages du planning de ce poste et sa copie locale ;\n"
    shared = "SharePoint" if config is not None and config.backend == "sharepoint" else "OneDrive"
    return erased, f" Le planning partage sur {shared} n'est pas touche." + kept_local


def _holds_files(directory: Path) -> bool:
    try:
        return directory.is_dir() and any(directory.iterdir())
    except OSError:
        return False


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
        if number.parent != parent or not (number.subproject_id or "").isdigit():
            continue  # « -A1 », « -REV03 » written by hand take no number
        used_ids.add(int(number.subproject_id or "0"))
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


async def _fetch_planner_members(plan_id: str) -> PlannerMembers:
    client = _planner_client()
    try:
        members = await client.list_members(plan_id=plan_id)
    finally:
        await client.aclose()
    return tuple(
        (member.id, member.display_name or member.email or member.id, member.email)
        for member in members
    )


def _forget_failed(
    requests: dict[ReadKey, asyncio.Task[ReadResult]],
    key: ReadKey,
    task: asyncio.Task[ReadResult],
) -> None:
    """Drop a failed shared read so that the next request tries again; keep a success."""
    if (task.cancelled() or task.exception() is not None) and requests.get(key) is task:
        del requests[key]


def _planner_client() -> GraphPlannerClient:
    settings = ApplicationSettings.load()
    token_provider = MsalAccessTokenProvider(
        client_id=settings.microsoft_client_id,
        scopes=PLANNER_GRAPH_SCOPES,
        allow_interactive=False,
    )
    return GraphPlannerClient(
        graph=GraphClient(token_provider=token_provider, request_timeout=60.0)
    )


async def _read_outlook_selection() -> list[ImportedEvent]:
    """Items selected in the classic Outlook of the poste, read in a thread of their own."""
    return await asyncio.to_thread(read_outlook_selection)
