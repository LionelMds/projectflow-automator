"""Controller of the « Planning » tab: store, cache, live synchronisation and every edit.

``PlanningController`` owns the planning settings of this poste, the store and its local
cache, the ``PlanningService`` and its ``SyncEngine`` task. It turns every intent signal of
the tab's widgets (grid, « À planifier », detail, side panel, quick entry, cell editor,
toolbar) into a command applied as one undo step with a French label, and redraws the
tab from the service's events: a debounced grid model, the cards, the detail, the team
dialog, flashes and toasts for the changes of the other postes, conflicts.

Nothing here opens a message box: a background problem goes to the sync badge, to a
toast and to ``PlanningHooks.log``. A store that cannot be opened (no permission yet, no
connection, wrong settings) leaves the planning readable from its cache, read-only.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, cast, get_args

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QWidget

from projectflow.core.planning import commands
from projectflow.core.planning.cache import PlanningCache
from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import DocumentChange, PlanningSnapshot
from projectflow.core.planning.commands import (
    UNASSIGNED,
    CommandContext,
    PlanningCommandError,
    calendar_resolver,
    task_days,
)
from projectflow.core.planning.conflicts import Finding, findings_by_bar, resource_load
from projectflow.core.planning.layout import (
    UNASSIGNED_ROW_ID,
    row_kind,
)
from projectflow.core.planning.models import (
    ABSENCE_LABELS,
    DEFAULT_TASK_TYPE,
    STATUS_LABELS,
    Absence,
    AbsenceReason,
    DayPart,
    PlanningDocument,
    PlanningTask,
    ProjectCache,
    TaskStatus,
    utc_now_text,
)
from projectflow.core.planning.night import MOON, elapsed_text, rest_text
from projectflow.core.planning.outlook_sync import CalendarGateway, OutlookCalendarSync, SyncReport
from projectflow.core.planning.poste_config import PlanningDensity, PlanningPosteConfig
from projectflow.core.planning.quick_entry import (
    EntryContext,
    ParsedEntry,
    ProjectInfo,
    fold_text,
    format_days,
    format_duration,
)
from projectflow.core.planning.service import PlanningEvent, PlanningService
from projectflow.core.planning.store import (
    PlanningStore,
    StoreChanges,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.core.planning.sync import SyncEngine, SyncMode, SyncStatus
from projectflow.core.planning.timescale import WEEKDAY_NAMES, week_start
from projectflow.core.planning.walltime import WallTime, wall_date, wall_time
from projectflow.core.repertoire_service import RepertoireSnapshot
from projectflow.exceptions import AuthError, ConfigError, GraphError, ProjectCreationError
from projectflow.logging import get_logger
from projectflow.platform.filemanager import open_file_default_app
from projectflow.platform.paths import documents_dir
from projectflow.ui.dialogs.planning_absence import AbsenceDialog, absence_dates_text
from projectflow.ui.dialogs.planning_team import (
    CommandBuilder,
    PlannerMember,
    TeamDialog,
    task_type_counts,
)
from projectflow.ui.planning.backlog import release_day
from projectflow.ui.planning.detail import MULTI_SELECTION, author_text
from projectflow.ui.planning.grid import bars_by_task
from projectflow.ui.planning.grid_model import (
    build_grid_model,
    moment_label,
    recurring_resource_id,
    span_label,
)
from projectflow.ui.planning.hovercard import HOVER_DELAY_MS, HoverInfo
from projectflow.ui.planning.planning_tab import PlanningTab, period_text
from projectflow.ui.planning.printing import export_pdf, print_with_dialog
from projectflow.ui.planning.quick_entry_bar import Availability
from projectflow.ui.theme import current_theme

Changes = tuple[DocumentChange, ...]
Step = Callable[[CommandContext], Changes]
BarKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PlanningHooks:
    """What the Planning tab needs from the rest of the application."""

    read_repertoire: Callable[[int], Awaitable[RepertoireSnapshot]]
    open_project_dir: Callable[[str], None]
    open_fiche: Callable[[str], None]
    show_in_repertoire: Callable[[str], None]
    load_in_creation: Callable[[str], None]
    prepare_sortie: Callable[[str], None]
    open_settings: Callable[[str], None]
    """Open the settings on a section key (``"planning"``)."""
    planner_members: Callable[[], Awaitable[Sequence[PlannerMember]]]
    set_nav_badge: Callable[[int], None]
    schedule: Callable[[Coroutine[Any, Any, None]], None]
    """The main controller's ``_schedule_task``."""
    ask: Callable[[Callable[[], Any]], Awaitable[Any]]
    """The main controller's ``_ask``: runs a dialog outside the task step."""
    log: Callable[[str], None]
    save_config: Callable[[PlanningPosteConfig], None] | None = None
    """Optional: keep the display choices of this poste (view, density, panel width…)
    where its settings live. Without it they change in ``poste_config`` only."""
    find_project: Callable[[str], Awaitable[ProjectInfo | None]] | None = None
    """Optional: details of a dossier older than the répertoire years read (its fiche)."""
    open_outlook_calendar: Callable[[], Awaitable[CalendarGateway]] | None = None
    """Optional: the Outlook calendars of the account, for the copy for the phones."""


PlanningStoreFactory = Callable[[PlanningPosteConfig], Awaitable[PlanningStore]]
"""Opens the store of a poste config; may raise StoreUnavailableError or ConfigError."""

REBUILD_DELAY_MS: Final = 30
PROJECTS_REFRESH_MS: Final = 10 * 60 * 1000
PROJECTS_STALE_SECONDS: Final = 60.0
PRELOADED_YEARS: Final = 3
"""Répertoire sheets read at the start: this year and the two before."""
LOOKUPS_AT_ONCE: Final = 8
OUTLOOK_DELAY_MS: Final = 8_000
"""Calm after the last change before the Outlook calendar is brought up to date."""
OUTLOOK_REPAIR_MS: Final = 15 * 60 * 1000
"""The whole calendar read again, to put back what was changed in Outlook."""
"""Older dossiers looked up in one go (their fiche), the others at the next pass."""
START_WAIT_SECONDS: Final = 3.0
STOP_BUDGET_SECONDS: Final = 4.5
"""Stopping a session (engine flush, end of its task) waits this long in all."""
CANCEL_GRACE_SECONDS: Final = 0.5
"""Then the task of a session stuck on the network is cancelled, and given this long."""
CARD_WAIT_SECONDS: Final = 10.0
"""A « Sortie dossier » card waits this long for a planning still opening."""
RETRY_SECONDS: Final[Mapping[str, float]] = {
    "offline": 15.0,
    "auth_required": 60.0,
    "forbidden": 300.0,
    "error": 300.0,
}
"""A store that could not be opened is tried again after this long (like ``SyncEngine``)."""
RETRY_ON_SHOW_SECONDS: Final = 15.0
"""Showing the tab tries such a store again at once, but not more often than this."""
MEMBERS_TIMEOUT_SECONDS: Final = 5.0
REMOTE_TOAST_SECONDS: Final = 4.0
UNDO_TOAST_MS: Final = 8000
CONFLICT_TOAST_MS: Final = 10000
NAME_LENGTH: Final = 56
MAX_ADVANCE_DAYS: Final = 60
DAY_VIEW_CELL_MINUTES: Final = 60
REPERTOIRE_COLUMNS: Final = 5
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403

NOT_LOADED_MESSAGE: Final = "Le planning n'est pas encore chargé."
READ_ONLY_MESSAGE: Final = "Planning en lecture seule : la modification n'est pas enregistrée."
SAVE_MESSAGE: Final = "Le planning s'enregistre tout seul."
RECURRING_MESSAGE: Final = "Jour récurrent : il se modifie dans Équipe, page Personnes."
NO_PERSON_ABSENCE: Final = "Absence sans personne : indiquez les initiales (ex. /vac KDU 26-30.10)."
UNREADABLE_ENTRY: Final = "Saisie non comprise."
UNAVAILABLE_MESSAGE: Final = "Planning indisponible."
OTHER_POSTE: Final = "Un autre poste"
FIELD_LABELS: Final[Mapping[str, str]] = {
    "title": "le titre",
    "project_number": "le dossier",
    "task_type": "le type",
    "required_people": "les personnes requises",
    "urgent": "l'urgence",
    "deadline": "l'échéance",
    "notes": "les notes",
    "estimate_days": "l'estimation",
    "estimate_minutes": "l'estimation",
}
STATUSES: Final[tuple[TaskStatus, ...]] = ("provisional", "confirmed", "done")
NAVIGATION_ACTIONS: Final = frozenset({"folder", "fiche", "repertoire", "creation", "sortie"})
DENSITIES: Final[Mapping[str, PlanningDensity]] = {
    "compact": "compact",
    "normal": "normal",
    "large": "large",
}
_FORBIDDEN_WORDS: Final = ("autorisation", "consentement", "refuse", "403")
_AUTH_WORDS: Final = ("connexion", "reconnect", "sign", "401")
_NUMBER_PARTS_RE: Final = re.compile(r"\d+")


# --------------------------------------------------------------- helpers ----


def step(func: Callable[..., Changes], *args: object, **kwargs: object) -> Step:
    """Bind a command to its arguments; the context comes when it runs."""

    def run(ctx: CommandContext) -> Changes:
        return func(ctx, *args, **kwargs)

    return run


def chain(steps: Iterable[Step]) -> Step:
    """Run ``steps`` one after the other, each on the snapshot the previous ones left."""
    pending = list(steps)

    def run(ctx: CommandContext) -> Changes:
        snapshot = ctx.snapshot
        collected: list[DocumentChange] = []
        for item in pending:
            current = CommandContext(
                snapshot=snapshot,
                author=ctx.author,
                now=ctx.now,
                calendar_for=calendar_resolver(snapshot),
            )
            changes = item(current)
            snapshot = snapshot.apply(changes)
            collected.extend(changes)
        return tuple(collected)

    return run


def merge_changes(changes: Iterable[DocumentChange]) -> Changes:
    """One change per document: the first ``before`` and the last ``after``."""
    merged: dict[tuple[str, str], DocumentChange] = {}
    for change in changes:
        key = (change.kind, change.doc_id)
        first = merged.get(key)
        if first is None:
            merged[key] = change
        elif first.before is None and change.after is None:
            del merged[key]
        else:
            merged[key] = DocumentChange(
                kind=change.kind, doc_id=change.doc_id, before=first.before, after=change.after
            )
    return tuple(merged.values())


def clip(text: str, length: int = NAME_LENGTH) -> str:
    return text if len(text) <= length else f"{text[: length - 1].rstrip()}…"


def _same_slot_other_person(task: PlanningTask, from_row: str, to_row: str, start: int) -> bool:
    """Whether a Ctrl+drag lands on another person's row at the task's own start.

    A drag keeps the duration, so the same start means the same slot: the user wants that
    person on the task, not a second task.
    """
    return (
        task.start == start and to_row != from_row and to_row not in {UNASSIGNED_ROW_ID, UNASSIGNED}
    )


def outlook_problem(exc: BaseException) -> str:
    """French reason of a failed copy to Outlook, with what to do."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, AuthError) or status == HTTP_UNAUTHORIZED:
        return (
            "connexion au calendrier à autoriser : Paramètres > Planning > "
            "« Connecter le calendrier Outlook »."
        )
    if status == HTTP_FORBIDDEN:
        return "Microsoft refuse l'accès au calendrier (autorisation Calendars.ReadWrite)."
    return str(exc) or type(exc).__name__


def task_name(task: PlanningTask) -> str:
    """« 2026-118 - Montage garde-corps », the way undo labels and toasts name a task."""
    return clip(task.label or "tâche sans titre")


def remote_verb(before: PlanningTask | None, after: PlanningTask | None) -> str:
    """Name what another poste did to a task, such as « a déplacé » or « a planifié »."""
    if after is None or after.deleted:
        return "a supprimé"
    if before is None or before.deleted:
        return "a planifié" if after.scheduled else "a ajouté à planifier"
    if before.scheduled != after.scheduled:
        return "a planifié" if after.scheduled else "a renvoyé à planifier"
    if (before.start, before.end) != (after.start, after.end):
        return "a déplacé"
    return "a réaffecté" if before.assignments != after.assignments else "a modifié"


def project_from_row(values: Sequence[object]) -> ProjectInfo | None:
    """Dossier of a répertoire row A:E (No., Date, Client, Contact, Désignation)."""
    cells = ["" if value is None else str(value).strip() for value in values[:REPERTOIRE_COLUMNS]]
    cells += [""] * (REPERTOIRE_COLUMNS - len(cells))
    number, _created, client, contact, designation = cells
    if not number or not any(cells[1:]):
        return None
    return ProjectInfo(number=number, client=client, contact=contact, designation=designation)


def absence_reason(value: str) -> AbsenceReason | None:
    """Return the absence reason named ``value`` (« vacances »…), None when unknown."""
    for reason in get_args(AbsenceReason):
        if reason == value:
            return cast("AbsenceReason", reason)
    return None


def number_key(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in _NUMBER_PARTS_RE.findall(number))


def _problem(error: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AuthError):
            return "auth"
        if isinstance(current, GraphError):
            if current.status_code == HTTP_UNAUTHORIZED:
                return "auth"
            if current.status_code == HTTP_FORBIDDEN:
                return "forbidden"
        current = current.__cause__ or current.__context__
    return None


def failure_status(error: BaseException) -> SyncStatus:
    """Badge state of a store that could not be opened, never a crash nor a browser."""
    message = str(error).strip() or UNAVAILABLE_MESSAGE
    problem = _problem(error)
    if problem == "auth":
        return SyncStatus("auth_required", None, 0, message)
    if problem == "forbidden":
        return SyncStatus("forbidden", None, 0, message)
    if isinstance(error, StoreUnavailableError):
        folded = fold_text(message)
        if any(word in folded for word in _FORBIDDEN_WORDS):
            return SyncStatus("forbidden", None, 0, message)
        if any(word in folded for word in _AUTH_WORDS):
            return SyncStatus("auth_required", None, 0, message)
        return SyncStatus("offline", None, 0, message)
    return SyncStatus("error", None, 0, message)


class UnavailableStore:
    """Store of a poste whose real store could not be opened: every call fails.

    The service still reads its cache, so the planning stays readable.
    """

    def __init__(self, error: BaseException) -> None:
        self._error = error
        self._message = str(error).strip() or UNAVAILABLE_MESSAGE

    def _failure(self) -> StoreUnavailableError:
        failure = StoreUnavailableError(self._message)
        failure.__cause__ = self._error
        return failure

    async def changes(self, cursor: str | None) -> StoreChanges:  # noqa: ARG002
        raise self._failure()

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:  # noqa: ARG002
        raise self._failure()

    async def create(
        self,
        kind: str,  # noqa: ARG002
        doc_id: str,  # noqa: ARG002
        data: Mapping[str, Any],  # noqa: ARG002
        *,
        project: str = "",  # noqa: ARG002
    ) -> StoredDocument:
        raise self._failure()

    async def update(
        self,
        kind: str,  # noqa: ARG002
        doc_id: str,  # noqa: ARG002
        etag: str,  # noqa: ARG002
        data: Mapping[str, Any],  # noqa: ARG002
        *,
        project: str | None = None,  # noqa: ARG002
    ) -> str:
        raise self._failure()

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:  # noqa: ARG002
        raise self._failure()

    async def aclose(self) -> None:
        return


@dataclass(eq=False)
class _Session:
    """One opened store with its cache, service and engine (rebuilt by ``reconfigure``)."""

    store: PlanningStore
    service: PlanningService
    engine: SyncEngine | None
    store_key: str
    failure: SyncStatus | None
    loaded: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    stopping: bool = False
    unsubscribe: Callable[[], None] | None = None
    task: asyncio.Task[Any] | None = None
    """The task running ``PlanningController._run`` (cancelled when it does not stop)."""

    @property
    def store_ok(self) -> bool:
        return self.failure is None


def _identity(config: PlanningPosteConfig) -> tuple[str, str, str, str]:
    return (config.user_initials, config.user_name, config.poste_name, config.poste_id)


def _member_tuple(member: object) -> PlannerMember | None:
    """Read a Microsoft 365 member as ``(id, name, e-mail)`` (tuple or ``PlannerMember``)."""
    if isinstance(member, tuple) and len(member) == len(("id", "name", "mail")):
        return (str(member[0]), str(member[1]), str(member[2]))
    member_id = getattr(member, "id", None)
    if not member_id:
        return None
    name = str(getattr(member, "display_name", "") or getattr(member, "name", ""))
    return (str(member_id), name, str(getattr(member, "email", "")))


# ------------------------------------------------------------ controller ----


class PlanningController:
    """Drives a ``PlanningTab``; see the module docstring."""

    def __init__(
        self,
        tab: PlanningTab,
        *,
        poste_config: PlanningPosteConfig,
        store_factory: PlanningStoreFactory,
        hooks: PlanningHooks,
        today: Callable[[], date] = date.today,
        clock: Callable[[], str] = utc_now_text,
    ) -> None:
        self._tab = tab
        self._config = poste_config
        self._factory = store_factory
        self._hooks = hooks
        self._today = today
        self._clock = clock
        self._session: _Session | None = None
        self._lifecycle = asyncio.Lock()
        """Held while a session closes or opens: one store opening at a time."""
        self._opened_at = float("-inf")
        self._failure_logged: tuple[str, str] | None = None
        self._missing_years: set[int] = set()
        self._started = False
        self._closed = False
        self._quiet = True
        self._applying = False
        self._visible = False
        self._window_active = True
        self._last_snapshot = PlanningSnapshot()
        self._projects: dict[str, ProjectInfo] = {}
        self._project_years: frozenset[int] = frozenset()
        self._older_projects: dict[str, ProjectInfo] = {}
        """Dossiers of older years found from their fiche, kept for the session."""
        self._looked_up: set[str] = set()
        self._lookup_queue: set[str] = set()
        self._lookup_running = False
        self._projects_at: float | None = None
        self._projects_running = False
        self._members: list[PlannerMember] | None = None
        self._team_dialog: TeamDialog | None = None
        self._team_opening = False
        self._team_state: tuple[object, ...] | None = None
        self._team_focus = ""
        self._team_types = False
        """Show the « Types de tâche » page once the team dialog opens."""
        self._absence_dialog: AbsenceDialog | None = None
        self._menu: QMenu | None = None
        self._unseen: set[str] = set()
        self._remote_toast_at = float("-inf")
        self._hover_task = ""
        self._hover_absence = ""
        self._hover_pos = QPoint()
        self._flash_after: set[str] = set()
        self._new_after: set[str] = set()
        self._scroll_after: tuple[WallTime, str | None] | None = None
        self._select_after: list[BarKey] = []
        self._card_after = ""
        self._editor_rows: tuple[str, ...] = ()
        self._read_only_shown: tuple[bool, str] = (False, "")

        self._rebuild_timer = QTimer(tab)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(REBUILD_DELAY_MS)
        self._rebuild_timer.timeout.connect(self.refresh_now)
        self._hover_timer = QTimer(tab)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(HOVER_DELAY_MS)
        self._hover_timer.timeout.connect(self._show_hover)
        self._projects_timer = QTimer(tab)
        self._projects_timer.setInterval(PROJECTS_REFRESH_MS)
        self._projects_timer.timeout.connect(self._request_projects)
        self._retry_timer = QTimer(tab)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._on_retry)
        self._outlook: OutlookCalendarSync | None = None
        self._outlook_running = False
        self._outlook_again = False
        self._outlook_problem = ""
        self.outlook_report: SyncReport | None = None
        """Outcome of the last copy to Outlook (tests and the settings read it)."""
        self._outlook_timer = QTimer(tab)
        self._outlook_timer.setSingleShot(True)
        self._outlook_timer.setInterval(OUTLOOK_DELAY_MS)
        self._outlook_timer.timeout.connect(self._request_outlook_sync)
        self._outlook_repair_timer = QTimer(tab)
        self._outlook_repair_timer.setInterval(OUTLOOK_REPAIR_MS)
        self._outlook_repair_timer.timeout.connect(self._repair_outlook)

        self._wire()
        self._apply_config(initial=True)
        self._apply_status()

    # -- contract -------------------------------------------------------------

    @property
    def poste_config(self) -> PlanningPosteConfig:
        return self._config

    @property
    def service(self) -> PlanningService | None:
        return self._session.service if self._session is not None else None

    @property
    def sync_status(self) -> SyncStatus:
        session = self._session
        if session is None:
            return SyncStatus()
        if session.failure is not None:
            return session.failure
        return session.engine.status if session.engine is not None else SyncStatus()

    @property
    def read_only(self) -> bool:
        """Edits are refused: no store yet, a store that failed, or a read-only sync."""
        session = self._session
        return session is None or not session.store_ok or self.sync_status.read_only

    def is_closed(self) -> bool:
        """Whether ``aclose`` was called (a method: read again after every ``await``)."""
        return self._closed

    @property
    def team_dialog(self) -> TeamDialog | None:
        return self._team_dialog

    @property
    def absence_dialog(self) -> AbsenceDialog | None:
        """The « Absence » dialog opened from the grid, while it is open."""
        return self._absence_dialog

    @property
    def projects(self) -> Mapping[str, ProjectInfo]:
        return dict(self._projects)

    async def start(self) -> None:
        """Show the cached planning, start the synchronisation; never raises."""
        if self._started or self._closed:
            return
        self._started = True
        try:
            async with self._lifecycle:
                if not self._closed:
                    await self._open()
                session = self._session
            if session is not None:
                await self._wait_loaded(session)
        except Exception:  # noqa: BLE001 - the tab stays usable, the badge tells
            get_logger(__name__).exception("planning.start.failed")
        if self._visible and not self._closed:
            self._projects_timer.start()
        self._request_projects()
        self._update_mode()

    async def aclose(self) -> None:
        """Stop the engine (sending what waits, 5 s at most), save the cache, close the store."""
        if self._closed:
            return
        self._closed = True
        for timer in (
            self._rebuild_timer,
            self._hover_timer,
            self._projects_timer,
            self._retry_timer,
            self._outlook_timer,
            self._outlook_repair_timer,
        ):
            timer.stop()
        self._tab.hover.hide()
        self._close_menu()
        absence_dialog, self._absence_dialog = self._absence_dialog, None
        if absence_dialog is not None:
            try:
                absence_dialog.reject()
                absence_dialog.deleteLater()
            except RuntimeError:  # already deleted with its window
                get_logger(__name__).info("planning.absence_dialog.gone")
        dialog, self._team_dialog = self._team_dialog, None
        if dialog is not None:
            try:
                dialog.close()
                dialog.deleteLater()
            except RuntimeError:  # already deleted with the window it belonged to
                get_logger(__name__).info("planning.team_dialog.gone")
        try:
            await self._close_session()
        except Exception:  # noqa: BLE001 - closing must go on
            get_logger(__name__).exception("planning.close.failed")

    def set_visible(self, *, visible: bool, window_active: bool = True) -> None:
        """Planning tab shown or not, window active or not: the cadence of the sync."""
        appeared = visible and not self._visible
        self._visible = visible
        self._window_active = window_active
        self._update_mode()
        if not visible:
            # The répertoire is read again when the tab comes back (if older than 1 min).
            self._projects_timer.stop()
            self._hide_hover()
        elif self._started and not self._closed and not self._projects_timer.isActive():
            self._projects_timer.start()
        if appeared:
            self._tab.set_today(self._today(), show=False)
            self._unseen.clear()
            self._hooks.set_nav_badge(0)
            self._request_projects(force=False)
            self._wake(force=False)
            self._schedule_rebuild()

    async def reconfigure(self, poste_config: PlanningPosteConfig) -> None:
        """Take new settings: a new identity signs the next changes, another store reopens."""
        if self._closed:
            return
        previous, self._config = self._config, poste_config
        try:
            session = self._session
            if session is not None:
                session.service.set_author(poste_config.author())
            self._apply_config(initial=False, previous=previous)
            reopened: _Session | None = None
            if self._started:
                async with self._lifecycle:
                    session = self._session
                    if not self._closed and (
                        session is None
                        or session.store_key != poste_config.cache_key()
                        or not session.store_ok
                    ):
                        await self._shutdown()
                        if not self._closed:
                            await self._open()
                            reopened = self._session
            if reopened is not None:
                await self._wait_loaded(reopened)
            self._apply_status()
            self._schedule_rebuild()
        except Exception:  # noqa: BLE001 - settings stay saved; the badge tells
            get_logger(__name__).exception("planning.reconfigure.failed")

    async def ensure_backlog_card(
        self,
        number: str,
        *,
        designation: str,
        client: str,
        contact: str = "",
        managed_by: str = "",
        released: date | None = None,
    ) -> bool:
        """Card « Fabrication - <désignation> » (en dash) for a dossier released to the workshop.

        Idempotent: an open card of a previous release of the dossier flashes instead.
        Returns True when a card was created and waits to be sent to the shared store;
        never raises (a planning failure must not break the release). A planning still
        opening is waited for a few seconds; one whose store could not be opened makes no
        card. Each refusal but the shared setting being off is written to the log.
        """
        try:
            return await self._ensure_card(
                number,
                designation=designation,
                client=client,
                contact=contact,
                managed_by=managed_by,
                released=released,
            )
        except Exception:  # noqa: BLE001 - the release of the dossier went through
            get_logger(__name__).exception("planning.sortie_card.failed", number=number)
            self._log(f"Planning : carte à planifier non créée pour {number}.")
            return False

    def open_team_dialog(self) -> None:
        """Open « Équipe du planning » (non modal); a second call brings it to the front.

        Asked from a modal window (Settings), the dialog belongs to it so that the modal
        window does not block it; it goes back to the main window when that one closes.
        """
        dialog = self._team_dialog
        if dialog is not None:
            self._adopt_team_dialog(dialog)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        if self._session is None:
            self._toast(NOT_LOADED_MESSAGE)
            return
        if self._members is not None:
            self._show_team_dialog()
        elif not self._team_opening:
            self._team_opening = True
            self._hooks.schedule(self._open_team_dialog_later())

    # -- extra public API -----------------------------------------------------

    def wake(self) -> None:
        """Synchronise now; a store that could not be opened is tried again at once.

        Meant for a new Microsoft sign-in too: the planning then reopens its SharePoint list.
        """
        self._wake(force=True)

    def _wake(self, *, force: bool) -> None:
        session = self._session
        if session is None or self._closed:
            return
        if session.store_ok:
            if session.engine is not None:
                session.engine.wake()
            return
        if force or time.monotonic() - self._opened_at >= RETRY_ON_SHOW_SECONDS:
            self._on_retry()

    def refresh_now(self) -> None:
        """Redraw the tab from the service at once (otherwise debounced by 30 ms)."""
        self._rebuild_timer.stop()
        session = self._session
        if session is None or self._closed:
            return
        try:
            self._rebuild(session)
        except Exception:  # noqa: BLE001 - one broken redraw must not stop the planning
            get_logger(__name__).exception("planning.rebuild.failed")

    def undo(self) -> None:
        session = self._session
        if session is None:
            return
        if self.read_only:
            self._toast(self._read_only_message())
            return
        label = session.service.undo_label
        reason = session.service.undo()
        if reason:
            self._toast(reason)
        elif label:
            self._toast(f"Annulé : {label}", action_text="Rétablir", action=self.redo)

    def redo(self) -> None:
        session = self._session
        if session is None:
            return
        if self.read_only:
            self._toast(self._read_only_message())
            return
        label = session.service.redo_label
        reason = session.service.redo()
        if reason:
            self._toast(reason)
        elif label:
            self._toast(f"Rétabli : {label}", action_text="Annuler", action=self.undo)

    def hover_info(self, task_id: str) -> HoverInfo | None:
        """Build what the hover card shows for ``task_id`` (None when it is gone)."""
        session = self._session
        if session is None:
            return None
        snapshot = session.service.snapshot
        task = snapshot.tasks.get(task_id)
        if task is None or task.deleted:
            return None
        return self._hover_info(task, snapshot)

    def entry_context(
        self,
        *,
        cells: tuple[str, ...] = (),
        start: WallTime | None = None,
        end: WallTime | None = None,
    ) -> EntryContext:
        """Build the parsing context of the quick entry: week shown, people, dossiers."""
        session = self._session
        snapshot = session.service.snapshot if session is not None else PlanningSnapshot()
        calendar_for = calendar_resolver(snapshot)
        first, last = self._tab.period_range()
        today = self._today()
        reference = today if first <= today <= last else first
        return EntryContext(
            today=today,
            week_start=week_start(reference),
            calendar=calendar_for(None),
            resources=snapshot.active_resources(),
            teams=snapshot.meta.teams,
            task_types=snapshot.meta.task_types,
            find_project=self._find_project,
            default_year=self._tab.anchor.year,
            cell_resource_ids=cells,
            cell_start=start,
            cell_end=end,
            calendar_for=calendar_for,
        )

    def availability(self, resource_id: str, entry: ParsedEntry | None) -> Availability:
        """« libre », « 62 % » or « absent » over the entry's dates (else the period shown)."""
        session = self._session
        if session is None:
            return Availability("")
        snapshot = session.service.snapshot
        resource = snapshot.resources.get(resource_id)
        if resource is not None and resource.is_subcontractor:
            return Availability("")
        if entry is not None and entry.start is not None and entry.end is not None:
            first, last = wall_date(entry.start), wall_date(entry.end - 1)
        else:
            first, last = self._tab.period_range()
        calendar = calendar_resolver(snapshot)(resource_id)
        planned, available = resource_load(snapshot, resource_id, first, last, calendar)
        if available <= 0:
            return Availability("absent", available=False)
        if planned <= 0:
            return Availability("libre")
        return Availability(f"{round(100 * planned / available)} %", available=planned < available)

    # -- session --------------------------------------------------------------

    async def _open(self) -> None:
        """Open the store of the settings, with its cache, service and engine.

        Called with ``_lifecycle`` held, after ``_shutdown``: never two sessions at once.
        A store that cannot be opened leaves a read-only session on its cache, tried
        again later (``RETRY_SECONDS``).
        """
        config = self._config
        self._quiet = True
        self._retry_timer.stop()
        self._opened_at = time.monotonic()
        failure: SyncStatus | None = None
        try:
            store = await self._factory(config)
        except Exception as exc:  # noqa: BLE001 - the planning stays readable from its cache
            failure = failure_status(exc)
            get_logger(__name__).warning("planning.store.unavailable", state=failure.state)
            store = UnavailableStore(exc)
        if self._closed:
            await _close_quietly(store)
            return
        self._note_failure(failure)
        cache = PlanningCache(config.cache_directory())
        service = PlanningService(store, cache, config.author(), clock=self._clock)
        engine = SyncEngine(service, store) if failure is None else None
        session = _Session(
            store=store,
            service=service,
            engine=engine,
            store_key=config.cache_key(),
            failure=failure,
        )
        session.unsubscribe = service.subscribe(lambda event: self._on_event(session, event))
        self._session = session
        self._last_snapshot = PlanningSnapshot()
        if engine is not None:
            engine.set_mode(self._mode())
        self._apply_status()
        self._hooks.schedule(self._run(session))
        if failure is not None:
            seconds = RETRY_SECONDS.get(failure.state, max(RETRY_SECONDS.values()))
            self._retry_timer.start(round(seconds * 1000))
        self._schedule_rebuild()

    def _note_failure(self, failure: SyncStatus | None) -> None:
        """Log a store that cannot be opened once, not at every automatic new attempt."""
        key = (failure.state, failure.message) if failure is not None else None
        if key is not None and key != self._failure_logged:
            self._log(f"Planning : {key[1]}")
        self._failure_logged = key

    async def _wait_loaded(self, session: _Session) -> None:
        """Wait (3 s at most) for the cache of a session just opened, then redraw."""
        try:
            async with asyncio.timeout(START_WAIT_SECONDS):
                await session.loaded.wait()
        except TimeoutError:
            get_logger(__name__).info("planning.start.slow")
        self._schedule_rebuild()

    async def _run(self, session: _Session) -> None:
        """Load the cache and the server, then keep the planning in sync until stopped."""
        session.task = asyncio.current_task()
        if session.stopping:  # closed before it ever ran
            session.loaded.set()
            session.done.set()
            return
        session.running = True
        try:
            try:
                await session.service.load()
            except Exception:  # noqa: BLE001 - the engine retries and reports
                get_logger(__name__).exception("planning.load.failed")
            finally:
                session.loaded.set()
            if session is self._session:
                self._loaded(session)
            if session.engine is not None and not session.stopping:
                await session.engine.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never reported as a failed operation
            get_logger(__name__).exception("planning.sync.crashed")
            self._log("Planning : la synchronisation s'est arrêtée (voir le journal).")
        finally:
            session.done.set()

    def _loaded(self, session: _Session) -> None:
        self._quiet = False
        self._last_snapshot = session.service.snapshot
        self._apply_status()
        self._schedule_rebuild()

    async def _close_session(self) -> None:
        """``aclose``: let an opening in progress end (it closes its own store), then stop.

        Everything shares one budget: a store that hangs never holds the application.
        """
        deadline = asyncio.get_running_loop().time() + STOP_BUDGET_SECONDS
        locked = False
        try:
            async with asyncio.timeout_at(deadline):
                await self._lifecycle.acquire()
            locked = True
        except TimeoutError:
            get_logger(__name__).warning("planning.close.opening_still_running")
        try:
            await self._shutdown(deadline=deadline)
        finally:
            if locked:
                self._lifecycle.release()

    async def _shutdown(self, *, deadline: float | None = None) -> None:
        """Stop the session: engine flush and end of its task within ``deadline``.

        The outbox is saved as the engine stops, so a task still stuck on the network
        after that is cancelled: what it did not send goes out at the next start.
        """
        session, self._session = self._session, None
        if session is None:
            return
        loop = asyncio.get_running_loop()
        limit = loop.time() + STOP_BUDGET_SECONDS if deadline is None else deadline
        session.stopping = True
        if session.unsubscribe is not None:
            session.unsubscribe()
        if session.engine is not None:
            try:
                async with asyncio.timeout_at(limit):
                    await session.engine.stop()
            except TimeoutError:
                get_logger(__name__).warning("planning.engine.stop_slow")
            except Exception:  # noqa: BLE001 - what is not sent stays in the cache
                get_logger(__name__).exception("planning.engine.stop_failed")
        if session.running and not session.done.is_set():
            try:
                async with asyncio.timeout_at(limit):
                    await session.done.wait()
            except TimeoutError:
                get_logger(__name__).warning("planning.engine.cancelled")
                await _cancel(session.task)
        try:
            await session.service.save()
        except Exception:  # noqa: BLE001
            get_logger(__name__).exception("planning.cache.save_failed")
        await _close_quietly(session.store)

    async def _retry(self) -> None:
        """« Réessayer », the badge, the timer: open again a store that could not be."""
        if self._closed or self._lifecycle.locked():
            return  # an opening is running: a second one would leave a session behind
        async with self._lifecycle:
            session = self._session
            if session is not None and session.store_ok:
                if session.engine is not None:
                    session.engine.wake()
                return
            await self._shutdown()
            if not self._closed:
                await self._open()
            session = self._session
        self._apply_status()
        if session is not None:
            await self._wait_loaded(session)

    def _mode(self) -> SyncMode:
        """Live (2 s) while the tab is on screen, active window or not (a workshop screen)."""
        if self._visible:
            return "visible"
        window = self._tab.window()
        if window.isVisible() and not window.isMinimized():
            return "background"
        return "hidden"

    def _update_mode(self) -> None:
        session = self._session
        if session is not None and session.engine is not None:
            session.engine.set_mode(self._mode())

    # -- wiring ---------------------------------------------------------------

    def _wire(self) -> None:
        tab = self._tab
        grid = tab.grid
        grid.move_requested.connect(self._on_move)
        grid.resize_requested.connect(self._on_resize)
        grid.range_selected.connect(self._on_range)
        grid.cell_activated.connect(self._on_cell_activated)
        grid.card_dropped.connect(self._on_card_dropped)
        grid.shift_requested.connect(self._on_shift)
        grid.reassign_requested.connect(self._on_reassign)
        grid.delete_requested.connect(self._on_delete)
        grid.detail_requested.connect(self._on_detail_requested)
        grid.bar_double_clicked.connect(self._on_detail_requested)
        grid.bar_clicked.connect(self._on_bar_clicked)
        grid.selection_changed.connect(self._on_selection_changed)
        grid.context_menu_requested.connect(self._on_context_menu)
        grid.hover_changed.connect(self._on_hover)
        grid.absence_hovered.connect(self._on_absence_hover)
        grid.absence_activated.connect(self.edit_absence)
        grid.absence_menu_requested.connect(self._on_absence_menu)
        grid.team_toggled.connect(self._on_team_toggled)
        grid.row_menu_requested.connect(self._on_row_menu)
        grid.drag_started.connect(self._on_drag_started)
        grid.drag_finished.connect(self._on_drag_finished)
        # grid.bar_drag_exported is left alone: the backlog's unschedule_requested
        # already sends a bar dropped on « À planifier » back.

        backlog = tab.backlog
        backlog.card_activated.connect(self._on_card_activated)
        backlog.card_action.connect(self._on_card_action)
        backlog.reorder_requested.connect(self._on_reorder)
        backlog.unschedule_requested.connect(self._on_unschedule)
        backlog.new_entry_requested.connect(tab.focus_quick_entry)
        backlog.filters_changed.connect(self._on_backlog_filters)

        detail = tab.detail
        detail.field_changed.connect(self._on_field_changed)
        detail.people_changed.connect(self._on_people_changed)
        detail.dates_changed.connect(self._on_dates_changed)
        detail.status_changed.connect(self._on_status_changed)
        detail.shift_requested.connect(self._on_detail_shift)
        detail.action_requested.connect(self._on_detail_action)

        side = tab.side
        side.width_changed.connect(self._on_panel_width)
        side.collapsed_changed.connect(self._on_panel_collapsed)
        side.face_changed.connect(self._on_face_changed)

        quick = tab.quick
        quick.set_context_provider(self.entry_context)
        quick.set_availability_provider(self.availability)
        quick.entry_submitted.connect(self._on_quick_submitted)
        quick.entry_rejected.connect(self._on_entry_rejected)
        quick.cancelled.connect(self._focus_grid)

        editor = tab.editor
        editor.set_availability_provider(self.availability)
        editor.submitted.connect(self._on_editor_submitted)
        editor.cancelled.connect(self._on_editor_cancelled)
        editor.bar.entry_rejected.connect(self._on_entry_rejected)

        tab.view_changed.connect(self._on_view_changed)
        tab.density_chosen.connect(self._on_density_chosen)
        tab.display_size_changed.connect(self._on_display_size_changed)
        tab.undo_requested.connect(self.undo)
        tab.redo_requested.connect(self.redo)
        tab.print_requested.connect(self._on_print)
        tab.pdf_requested.connect(self._on_pdf)
        tab.team_requested.connect(self.open_team_dialog)
        tab.settings_requested.connect(self._on_settings)
        tab.retry_requested.connect(self._on_retry)
        tab.save_requested.connect(lambda: self._toast(SAVE_MESSAGE))
        tab.cell_typed.connect(self._on_cell_typed)
        tab.visibility_changed.connect(self._on_tab_visibility)

    def _apply_config(self, *, initial: bool, previous: PlanningPosteConfig | None = None) -> None:
        """Show the display settings; after OK in the settings, only those that changed.

        « Vue à l'ouverture » is the view of the next opening: switching the view in the
        toolbar does not change it, choosing another one in the settings shows it now.
        """
        config = self._config
        tab = self._tab

        def changed(name: str) -> bool:
            return initial or previous is None or getattr(previous, name) != getattr(config, name)

        self._applying = True
        try:
            if initial:
                tab.apply_preferences(
                    view=config.default_view,
                    density=config.density,
                    color_by=config.color_by,
                    row_heights=config.row_heights,
                    zooms=config.column_zooms,
                )
                tab.set_today(self._today())
            else:
                tab.apply_preferences(
                    view=config.default_view if changed("default_view") else None,
                    density=config.density,
                    color_by=config.color_by,
                    row_heights=config.row_heights,
                    zooms=config.column_zooms,
                )
            if changed("panel_width"):
                tab.side.set_panel_width(config.panel_width)
            if changed("panel_open"):
                tab.side.set_collapsed(not config.panel_open)
            tab.backlog.set_sort(config.backlog_sort)
            if not config.outlook_sync:
                self._outlook = None
                self._outlook_timer.stop()
                self._outlook_repair_timer.stop()
            elif not initial and (
                previous is None
                or not previous.outlook_sync
                or previous.outlook_calendar != config.outlook_calendar
                or previous.outlook_hide_details != config.outlook_hide_details
            ):
                self._outlook = None
                self._outlook_timer.start()
            tab.backlog.set_my_initials(config.user_initials)
            tab.set_identity(identified=config.is_identified, label_text=config.author().label)
        finally:
            self._applying = False

    def _save_config(self) -> None:
        save = self._hooks.save_config
        if self._applying or save is None:
            return
        try:
            save(self._config)
        except (ConfigError, OSError) as exc:
            get_logger(__name__).warning("planning.config.save_failed", error=str(exc))

    # -- events of the service ------------------------------------------------

    def _on_event(self, session: _Session, event: PlanningEvent) -> None:
        if session is not self._session or self._closed:
            return
        if event.origin == "status":
            self._apply_status()
            return
        snapshot = session.service.snapshot
        previous = self._last_snapshot
        self._last_snapshot = snapshot
        if event.origin == "conflict":
            self._announce_conflict(session, event)
        elif event.origin == "remote" and not self._quiet:
            self._announce_remote(event, previous, snapshot)
        if event.origin != "local" and not self._quiet:
            self._detect_new_cards(event, previous, snapshot)
        self._schedule_rebuild()

    def _announce_remote(
        self, event: PlanningEvent, previous: PlanningSnapshot, snapshot: PlanningSnapshot
    ) -> None:
        author = event.author
        if author is not None and author.poste_id and author.poste_id == self._config.poste_id:
            return
        task_ids = [doc_id for kind, doc_id in event.changed if kind == "task"]
        if not task_ids:
            return
        self._flash_after.update(task_ids)
        moment = time.monotonic()
        if moment - self._remote_toast_at < REMOTE_TOAST_SECONDS:
            return
        self._remote_toast_at = moment
        who = author.label if author is not None and author.label else OTHER_POSTE
        if len(task_ids) == 1:
            before = previous.tasks.get(task_ids[0])
            after = snapshot.tasks.get(task_ids[0])
            subject = after or before
            name = task_name(subject) if subject is not None else "une tâche"
            message = f"{remote_verb(before, after)} « {name} »"
        else:
            message = f"a modifié {len(task_ids)} tâches"
        self._tab.show_toast(message, author_tag=who)

    def _announce_conflict(self, session: _Session, event: PlanningEvent) -> None:
        if not event.message:
            return
        documents = event.restorable
        action: Callable[[], object] | None = None
        action_text = ""
        if documents:
            snapshot = session.service.snapshot
            alive = all(
                (current := snapshot.get(document.kind, document.id)) is not None
                and not current.deleted
                for document in documents
            )
            action_text = "Réappliquer" if alive else "Restaurer"

            def restore() -> None:
                self._restore(documents)

            action = restore
        author, message = conflict_text(event, session.service.snapshot)
        self._tab.show_toast(
            message,
            action_text=action_text,
            action=action,
            author_tag=author,
            timeout_ms=CONFLICT_TOAST_MS,
        )
        self._log(f"Planning : {author} {message}" if author else f"Planning : {message}")

    def _detect_new_cards(
        self, event: PlanningEvent, previous: PlanningSnapshot, snapshot: PlanningSnapshot
    ) -> None:
        poste = self._config.poste_id
        new = [
            doc_id
            for kind, doc_id in event.changed
            if kind == "task"
            and doc_id not in previous.tasks
            and (task := snapshot.tasks.get(doc_id)) is not None
            and not task.deleted
            and not task.scheduled
            and task.created_by.poste_id != poste
        ]
        if not new:
            return
        self._new_after.update(new)
        if not self._visible:
            self._unseen.update(new)
            self._hooks.set_nav_badge(len(self._unseen))

    def _apply_status(self) -> None:
        status = self.sync_status
        tab = self._tab
        tab.set_sync_status(status, local=False)
        session = self._session
        read_only = session is not None and self.read_only
        message = self._read_only_message() if read_only else ""
        if (read_only, message) != self._read_only_shown:
            self._read_only_shown = (read_only, message)
            tab.set_read_only(read_only, message)
        if session is not None:
            tab.grid.set_pending(self._pending_ids(session))
            tab.set_undo_labels(session.service.undo_label, session.service.redo_label)
        else:
            tab.set_undo_labels(None, None)

    def _read_only_reason(self) -> str:
        """Why edits are refused right now, "" when they are not."""
        return self._read_only_message() if self.read_only else ""

    def _read_only_message(self) -> str:
        if self._session is None:
            return NOT_LOADED_MESSAGE
        status = self.sync_status
        return status.message or READ_ONLY_MESSAGE

    @staticmethod
    def _pending_ids(session: _Session) -> list[str]:
        service = session.service
        return [
            task_id for task_id in service.snapshot.tasks if service.is_pending("task", task_id)
        ]

    # -- redraw ---------------------------------------------------------------

    def _schedule_rebuild(self) -> None:
        if not self._closed:
            self._rebuild_timer.start()

    def _rebuild(self, session: _Session) -> None:
        tab = self._tab
        service = session.service
        snapshot = self._display_snapshot(service.snapshot)
        today = self._today()
        model = build_grid_model(
            snapshot,
            view=tab.view,
            anchor=tab.anchor,
            viewport_width=tab.grid.viewport_width(),
            today=today,
            now=self._now(today),
            density=tab.row_height,
            color_by=tab.color_by,
            hidden_resource_ids=self._config.hidden_resource_ids,
            collapsed_teams=self._config.collapsed_teams,
            project_exists=self._project_exists,
            zoom=tab.zoom,
        )
        tab.set_model(model)
        tab.backlog.set_snapshot(snapshot, today=today)
        tab.grid.set_pending(self._pending_ids(session))
        self._after_rebuild()
        self._refresh_detail(service.snapshot)
        self._refresh_team_dialog(service.snapshot)
        tab.set_undo_labels(service.undo_label, service.redo_label)
        if self._config.outlook_sync and session.loaded.is_set():
            self._outlook_timer.start()

    # -- copy to Outlook ------------------------------------------------------

    def sync_outlook_now(self) -> None:
        """Bring the Outlook calendar up to date at once (tests, « Synchroniser »)."""
        self._outlook_timer.stop()
        self._request_outlook_sync()

    def _repair_outlook(self) -> None:
        if self._outlook is not None:
            self._outlook.forget()
        self._request_outlook_sync()

    def _request_outlook_sync(self) -> None:
        if self._closed or not self._config.outlook_sync:
            return
        if self._outlook_running:
            self._outlook_again = True
            return
        self._outlook_running = True
        self._hooks.schedule(self._sync_outlook())

    async def _sync_outlook(self) -> None:
        """Copy the planning into the Outlook calendar; problems are logged, never raised."""
        try:
            while not self._closed and self._config.outlook_sync:
                self._outlook_again = False
                session = self._session
                if session is None or not session.loaded.is_set():
                    return
                outlook = await self._outlook_sync()
                if outlook is None:
                    return
                snapshot = self._display_snapshot(session.service.snapshot)
                report = await outlook.sync(
                    snapshot,
                    today=self._today(),
                    calendar_for=calendar_resolver(snapshot),
                    hide_details=self._config.outlook_hide_details,
                )
                self.outlook_report = report
                self._outlook_problem = ""
                if report.created or report.updated or report.deleted or report.failed:
                    name = outlook.calendar_name
                    self._log(f"Calendrier Outlook « {name} » : {report.summary()}.")
                if report.errors:
                    get_logger(__name__).warning(
                        "planning.outlook.partial", failed=report.failed, first=report.errors[0]
                    )
                if not self._outlook_again:
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the planning works on without the copy
            self._outlook = None
            message = outlook_problem(exc)
            if message != self._outlook_problem:
                self._outlook_problem = message
                self._log(f"Calendrier Outlook non mis à jour : {message}")
            get_logger(__name__).warning("planning.outlook.failed", error=type(exc).__name__)
        finally:
            self._outlook_running = False

    async def _outlook_sync(self) -> OutlookCalendarSync | None:
        opener = self._hooks.open_outlook_calendar
        if opener is None:
            return None
        name = self._config.outlook_calendar
        if self._outlook is None or self._outlook.calendar_name != name:
            self._outlook = OutlookCalendarSync(await opener(), calendar_name=name)
            if not self._outlook_repair_timer.isActive() and not self._closed:
                self._outlook_repair_timer.start()
        return self._outlook

    def _display_snapshot(self, snapshot: PlanningSnapshot) -> PlanningSnapshot:
        """Return the snapshot to draw, with the dossier details the tasks lack filled in.

        Only what is shown changes (« 2026-5271 - Désignation », the client); the shared
        planning keeps the tasks as they are.
        """
        self._want_task_projects(snapshot)
        filled: dict[str, PlanningTask] = {}
        for task in snapshot.tasks.values():
            info = self._projects.get(task.project_number.strip()) if task.project_number else None
            cache = task.project_cache
            if info is None or (cache.designation and cache.client):
                continue
            filled[task.id] = task.model_copy(
                update={
                    "project_cache": cache.model_copy(
                        update={
                            "designation": cache.designation or info.designation,
                            "client": cache.client or info.client,
                            "contact": cache.contact or info.contact,
                        }
                    )
                }
            )
        if not filled:
            return snapshot
        return replace(snapshot, tasks=MappingProxyType({**snapshot.tasks, **filled}))

    def _after_rebuild(self) -> None:
        tab = self._tab
        if self._flash_after:
            tab.grid.flash(self._flash_after)
            self._flash_after = set()
        if self._new_after:
            tab.backlog.mark_new(self._new_after)
            self._new_after = set()
        tab.side.set_new_cards(has_new=bool(tab.backlog.new_ids()))
        if self._scroll_after is not None:
            start, row_id = self._scroll_after
            tab.grid.scroll_to(start, row_id)
            self._scroll_after = None
        if self._select_after:
            tab.grid.select(self._select_after)
            self._select_after = []
        if self._card_after:
            tab.backlog.select_card(self._card_after)
            self._card_after = ""

    def _now(self, today: date) -> WallTime:
        local = datetime.now(UTC).astimezone()
        return wall_time(today, local.hour, local.minute)

    def _refresh_detail(self, snapshot: PlanningSnapshot) -> None:
        detail = self._tab.detail
        if detail.mode() == "task":
            task = snapshot.tasks.get(detail.task_id())
            if task is None or task.deleted:
                detail.clear()
            else:
                self._show_task(task, None)
        elif detail.mode() == "multi":
            self._show_selection_detail()

    def _refresh_team_dialog(self, snapshot: PlanningSnapshot) -> None:
        dialog = self._team_dialog
        if dialog is None:
            return
        state = _team_state(snapshot)
        if self._team_state is not None and state == self._team_state:
            return
        self._team_state = state
        dialog.refresh()

    # -- commands -------------------------------------------------------------

    def _context(self, snapshot: PlanningSnapshot) -> CommandContext:
        return CommandContext(
            snapshot=snapshot,
            author=self._config.author(),
            now=self._clock(),
            calendar_for=calendar_resolver(snapshot),
        )

    def _execute(
        self, label: str, build: Step, *, refuse: Callable[[], None] | None = None
    ) -> Changes | None:
        """Run a command on a fresh context and apply it as one undo step.

        None when it was refused (read-only planning, ``PlanningCommandError``): a toast
        says why and ``refuse`` is called (the editor keeps its text, the bar shakes).
        """
        session = self._session
        if session is None or self.read_only:
            self._refuse(refuse, self._read_only_message())
            return None
        try:
            changes = merge_changes(build(self._context(session.service.snapshot)))
        except PlanningCommandError as exc:
            self._refuse(refuse, str(exc) or "Modification impossible.")
            return None
        except ValueError as exc:
            get_logger(__name__).warning("planning.command.invalid", error=str(exc))
            self._refuse(refuse, "Modification impossible avec les horaires du planning.")
            return None
        if changes:
            session.service.apply(changes, label=label)
        return changes

    def _refuse(self, refuse: Callable[[], None] | None, message: str) -> None:
        self._toast(message)
        if refuse is not None:
            refuse()

    def _apply_builder(self, build: CommandBuilder, label: str) -> None:
        """``TeamDialog.apply``: errors go back to the dialog, which shows them."""
        session = self._session
        if session is None or self.read_only:
            raise PlanningCommandError(self._read_only_message())
        changes = merge_changes(build(self._context(session.service.snapshot)))
        if changes:
            session.service.apply(changes, label=label)

    def _restore(self, documents: Sequence[PlanningDocument]) -> None:
        session = self._session
        if session is None or self.read_only:
            self._toast(self._read_only_message())
            return
        for document in documents:
            name = task_name(document) if isinstance(document, PlanningTask) else "élément"
            try:
                session.service.restore(document, label=f"Restaurer {name}")
            except Exception:  # noqa: BLE001 - shown, not raised from a toast button
                get_logger(__name__).exception("planning.restore.failed")
                self._toast("Restauration impossible.")

    def _task(self, task_id: str) -> PlanningTask | None:
        session = self._session
        if session is None:
            return None
        task = session.service.snapshot.tasks.get(task_id)
        return task if task is not None and not task.deleted else None

    def _who(self, row_id: str | None) -> str:
        if not row_id or row_id in {UNASSIGNED_ROW_ID, UNASSIGNED}:
            return "Non affecté"
        session = self._session
        resource = session.service.snapshot.resources.get(row_id) if session else None
        return resource.short_label if resource is not None else row_id

    def _names(self, task_ids: Sequence[str]) -> str:
        if len(task_ids) == 1:
            task = self._task(task_ids[0])
            return task_name(task) if task is not None else "la tâche"
        return f"{len(task_ids)} tâches"

    # -- grid -----------------------------------------------------------------

    def _on_move(self, task_id: str, from_row: str, to_row: str, new_start: int, mode: str) -> None:
        task = self._task(task_id)
        if task is None:
            return
        name = task_name(task)
        if mode == "copy" and _same_slot_other_person(task, from_row, to_row, new_start):
            # Dropped on someone else at the very same time: the same task, one more person.
            mode = "add"
        if mode == "copy":
            changes = self._execute(
                f"Copier {name}",
                step(commands.duplicate_task, task_id, new_start=new_start, resource_ids=(to_row,)),
            )
            if changes:
                self._select_after = [(changes[0].doc_id, to_row)]
            return
        if mode == "add":
            self._execute(
                f"Ajouter {self._who(to_row)} à {name}", step(commands.assign, task_id, to_row)
            )
            return
        label = f"Déplacer {name}"
        if to_row != from_row:
            label = f"Déplacer {name} vers {self._who(to_row)}"
        self._execute(
            label,
            step(
                commands.move_task,
                task_id,
                new_start=new_start,
                from_resource=from_row,
                to_resource=to_row,
            ),
        )

    def _on_resize(self, task_id: str, _row_id: str, start: object, end: object) -> None:
        task = self._task(task_id)
        if task is None:
            return
        self._execute(
            f"Modifier la durée de {task_name(task)}",
            step(
                commands.resize_task,
                task_id,
                start=start if isinstance(start, int) else None,
                end=end if isinstance(end, int) else None,
            ),
        )

    def _on_shift(self, bars: object, minutes: int, resize_end: bool) -> None:  # noqa: FBT001
        task_ids = list(bars_by_task(_bar_keys(bars)))
        if not task_ids or not minutes:
            return
        names = self._names(task_ids)
        if resize_end:
            verb = "Allonger" if minutes > 0 else "Raccourcir"
            self._execute(
                f"{verb} {names}",
                chain(step(_resize_end, task_id, minutes) for task_id in task_ids),
            )
            return
        self._execute(f"Décaler {names}", step(commands.shift_tasks, task_ids, minutes=minutes))

    def _on_reassign(self, task_id: str, from_row: str, to_row: str, add: bool) -> None:  # noqa: FBT001
        task = self._task(task_id)
        if task is None or task.start is None:
            return
        name = task_name(task)
        if add:
            self._execute(
                f"Ajouter {self._who(to_row)} à {name}", step(commands.assign, task_id, to_row)
            )
            return
        self._execute(
            f"Réaffecter {name} à {self._who(to_row)}",
            step(
                commands.move_task,
                task_id,
                new_start=task.start,
                from_resource=from_row,
                to_resource=to_row,
            ),
        )

    def _on_delete(self, bars: object, permanent: bool) -> None:  # noqa: FBT001
        grouped = bars_by_task(_bar_keys(bars))
        if not grouped:
            return
        task_ids = list(grouped)
        names = self._names(task_ids)
        if permanent:
            changes = self._execute(
                f"Supprimer {names}",
                chain(step(commands.delete_task, task_id) for task_id in task_ids),
            )
            if changes:
                self._toast_undo(f"{names} : supprimé pour tous")
            return
        steps: list[Step] = []
        for task_id, rows in grouped.items():
            if UNASSIGNED_ROW_ID in rows:
                steps.append(step(commands.unschedule_task, task_id))
            else:
                steps.extend(step(commands.unassign, task_id, row) for row in rows)
        label = self._removal_label(grouped, names)
        changes = self._execute(label, chain(steps))
        if not changes:
            return
        sent_back = [
            change.after
            for change in changes
            if isinstance(change.after, PlanningTask)
            and isinstance(change.before, PlanningTask)
            and change.before.scheduled
            and not change.after.scheduled
        ]
        if len(sent_back) == 1:
            task = sent_back[0]
            # A bar of « Non affecté » never had anybody: it is only sent back.
            unassigned = UNASSIGNED_ROW_ID in grouped.get(task.id, ())
            reason = "" if unassigned else " n'a plus personne :"
            self._toast_undo(f"{task_name(task)}{reason} renvoyée à planifier")
        elif sent_back:
            self._toast_undo(f"{len(sent_back)} tâches renvoyées à planifier")

    def _removal_label(self, grouped: Mapping[str, Sequence[str]], names: str) -> str:
        if len(grouped) != 1:
            return f"Retirer les personnes de {names}"
        rows = next(iter(grouped.values()))
        if UNASSIGNED_ROW_ID in rows:
            return f"Renvoyer {names} à planifier"
        people = ", ".join(self._who(row) for row in rows)
        return f"Retirer {people} de {names}"

    def _on_card_dropped(self, task_id: str, row_id: str, start: int) -> None:
        task = self._task(task_id)
        if task is None:
            return
        resource_ids: tuple[str, ...]
        if row_id == UNASSIGNED_ROW_ID:
            resource_ids = (UNASSIGNED,)
        elif row_id in task.assignments:
            resource_ids = ()  # every person foreseen
        else:
            resource_ids = (row_id,)
        self._execute(
            f"Planifier {task_name(task)}",
            step(commands.schedule_task, task_id, start=start, resource_ids=resource_ids),
        )

    def _on_detail_requested(self, task_id: str, row_id: str) -> None:
        focus = row_id if row_id and row_kind(row_id) == "resource" else None
        self._open_detail(task_id, focus)

    def _on_bar_clicked(self, task_id: str, row_id: str, _modifiers: object) -> None:
        side = self._tab.side
        if side.face() == "detail" and not side.is_collapsed() and len(self._selected_ids()) <= 1:
            self._on_detail_requested(task_id, row_id)

    def _on_selection_changed(self, _bars: object) -> None:
        side = self._tab.side
        if side.face() == "detail" and not side.is_collapsed() and self._selected_ids():
            self._show_selection_detail()

    def _on_drag_started(self, task_id: str) -> None:
        self._hide_hover()
        session = self._session
        if session is not None:
            session.service.hold(task_id)

    def _on_drag_finished(self, task_id: str) -> None:
        session = self._session
        if session is not None:
            session.service.release(task_id)

    def _on_team_toggled(self, team_key: str) -> None:
        collapsed = list(self._config.collapsed_teams)
        if team_key in collapsed:
            collapsed.remove(team_key)
        else:
            collapsed.append(team_key)
        self._config.collapsed_teams = collapsed
        self._save_config()
        self.refresh_now()

    # -- cell editor ----------------------------------------------------------

    def _on_range(self, row_ids: object, start: int, end: int) -> None:
        rows = tuple(str(row) for row in row_ids) if isinstance(row_ids, list | tuple) else ()
        if rows:
            self._open_cell_editor(rows, start, end)

    def _on_cell_activated(self, row_id: str, start: int) -> None:
        start = self._cell_start(row_id, start)
        self._open_cell_editor((row_id,), start, self._default_end(row_id, start))

    def _on_cell_typed(self, text: str) -> None:
        cell = self._tab.cell_cursor() or self._first_cell()
        if cell is None:
            return
        row_id, start = cell
        start = self._cell_start(row_id, start)
        self._open_cell_editor((row_id,), start, self._default_end(row_id, start), prefill=text)

    def _cell_start(self, row_id: str, start: WallTime) -> WallTime:
        """Start a task of a week cell with its half day, wherever the pointer was.

        The day view keeps the half hour pointed at (drags snap to it in every view).
        """
        if self._tab.view != "week":
            return start
        return half_day_start(self._row_calendar(row_id), start)

    def _first_cell(self) -> tuple[str, WallTime] | None:
        model = self._tab.grid.model
        if model is None:
            return None
        rows = [row for row in model.editable_row_ids() if row != UNASSIGNED_ROW_ID]
        row_id = rows[0] if rows else UNASSIGNED_ROW_ID
        calendar = model.calendar_of(row_id)
        today = self._today()
        day = today if model.first_day <= today <= model.last_day else model.first_day
        return row_id, calendar.working_start(wall_time(day))

    def _default_end(self, row_id: str, start: WallTime) -> WallTime:
        """Length of a new task on a cell: 1 h in the day view, ½ j by week, 1 j by month."""
        calendar = self._row_calendar(row_id)
        view = self._tab.view
        if view == "day":
            return calendar.add_work(start, DAY_VIEW_CELL_MINUTES)
        return calendar.add_days(start, 1.0 if view == "month" else 0.5)

    def _row_calendar(self, row_id: str) -> WorkCalendar:
        session = self._session
        snapshot = session.service.snapshot if session is not None else PlanningSnapshot()
        person = row_id if row_kind(row_id) == "resource" else None
        return calendar_resolver(snapshot)(person)

    def _open_cell_editor(
        self, rows: tuple[str, ...], start: WallTime, end: WallTime, *, prefill: str = ""
    ) -> None:
        if self.read_only:
            self._toast(self._read_only_message())
            self._tab.grid.clear_range()
            return
        people = tuple(row for row in rows if row_kind(row) == "resource")
        context = self.entry_context(cells=people, start=start, end=end)
        calendar = self._row_calendar(rows[0])
        names = ", ".join(self._who(row) for row in rows)
        label = f"{names} · {span_label(calendar, start, end)}"
        self._editor_rows = rows
        rect = self._tab.cell_rect(rows, start, end)
        self._tab.open_editor(rect, context, label_text=label, prefill=prefill)

    def _on_editor_submitted(self, entry: object, advance: bool) -> None:  # noqa: FBT001
        if not isinstance(entry, ParsedEntry):
            return
        editor = self._tab.editor
        created = self._create_from_entry(entry, refuse=editor.refuse, origin="grid")
        if created is None:
            return
        self._tab.grid.clear_range()
        if advance:
            self._advance_editor(created)

    def _on_editor_cancelled(self, _reason: str) -> None:
        self._tab.grid.clear_range()

    def _advance_editor(self, created: Sequence[str]) -> None:
        """Tab: open the editor on the next free half-day of the same row."""
        session = self._session
        rows = self._editor_rows
        if session is None or not rows:
            return
        snapshot = session.service.snapshot
        task = snapshot.tasks.get(created[0]) if created else None
        after = task.end if task is not None and task.end is not None else None
        if after is None:
            return
        row = rows[0]
        calendar = self._row_calendar(row)
        cell = next_free_half_day(snapshot, row, after, calendar)
        if cell is None:
            return
        start, end = cell
        first, last = self._tab.period_range()
        if not first <= wall_date(start) <= last:
            self._tab.set_anchor(wall_date(start))
            self.refresh_now()
        self._open_cell_editor(rows, start, end)

    # -- quick entry ----------------------------------------------------------

    def _on_quick_submitted(self, entry: object, _keep_project: bool) -> None:  # noqa: FBT001
        if isinstance(entry, ParsedEntry):
            self._create_from_entry(entry, refuse=self._tab.quick.refuse, origin="quick_entry")

    def _on_entry_rejected(self, entry: object) -> None:
        if isinstance(entry, ParsedEntry) and entry.warnings:
            self._toast(entry.warnings[0])

    def _create_from_entry(
        self, entry: ParsedEntry, *, refuse: Callable[[], None], origin: str
    ) -> list[str] | None:
        """Create what a typed line asks for; the ids created, None when refused."""
        if entry.destination == "invalid":
            self._refuse(refuse, entry.warnings[0] if entry.warnings else UNREADABLE_ENTRY)
            return None
        if entry.destination == "absence":
            return self._create_absences(entry, refuse=refuse)
        cache = self._project_cache(entry.project_number, entry.project)
        # Saved as the preview of the entry shows it: « Pose garde-corps ».
        typed_title = capitalized(entry.title.strip())
        title = typed_title or cache.designation
        label = f"Créer {clip(f'{entry.project_number} {title}'.strip() or 'une tâche')}"
        changes = self._execute(
            label,
            step(
                commands.create_task,
                title=typed_title,
                task_type=entry.task_type,
                project_number=entry.project_number,
                project_cache=cache,
                start=entry.start,
                end=entry.end,
                resource_ids=entry.resource_ids,
                estimate_minutes=entry.estimate_minutes,
                estimate_days=entry.estimate_days,
                required_people=entry.required_people,
                status="provisional" if entry.provisional else "confirmed",
                deadline=entry.deadline,
                urgent=entry.urgent,
                notes=entry.notes,
                origin=origin,
                off_hours=entry.off_hours,
            ),
            refuse=refuse,
        )
        if not changes:
            return None
        created = changes[0].after
        if isinstance(created, PlanningTask):
            self._reveal(created)
        return [changes[0].doc_id]

    def _create_absences(
        self, entry: ParsedEntry, *, refuse: Callable[[], None]
    ) -> list[str] | None:
        draft = entry.absence
        if draft is None or not draft.resource_ids:
            self._refuse(refuse, NO_PERSON_ABSENCE)
            return None
        who = ", ".join(self._who(rid) for rid in draft.resource_ids)
        reason = ABSENCE_LABELS.get(draft.reason, draft.reason)
        days = f"{draft.first_day:%d.%m}"
        if draft.last_day != draft.first_day:
            days = f"{days} → {draft.last_day:%d.%m}"
        label = f"Absence {reason} {who} {days}"
        changes = self._execute(
            label,
            chain(
                step(
                    commands.create_absence,
                    resource_id=resource_id,
                    first_day=draft.first_day,
                    last_day=draft.last_day,
                    reason=draft.reason,
                    part=draft.part,
                    note=draft.note,
                )
                for resource_id in draft.resource_ids
            ),
            refuse=refuse,
        )
        if not changes:
            return None
        self._toast(f"Absence enregistrée : {reason} {who} {days}")
        if not draft.first_day <= self._tab.period_range()[1]:
            self._tab.set_anchor(draft.first_day)
        return [change.doc_id for change in changes]

    def _reveal(self, task: PlanningTask) -> None:
        """Bring a task just created into view: its bar flashes, or its card is selected."""
        tab = self._tab
        if task.start is None:
            tab.side.show_backlog()
            tab.side.set_collapsed(False)
            self._card_after = task.id
            return
        first, last = tab.period_range()
        if not first <= wall_date(task.start) <= last:
            tab.set_anchor(wall_date(task.start))
        row = next(iter(task.assignments), UNASSIGNED_ROW_ID)
        self._flash_after.add(task.id)
        self._scroll_after = (task.start, row)
        self._select_after = [(task.id, row)]

    def _project_cache(self, number: str, project: ProjectInfo | None) -> ProjectCache:
        """Dossier details copied once into a new task (the shared display copy)."""
        if not number:
            return ProjectCache()
        info = project or self._projects.get(number)
        if info is None:
            return ProjectCache()
        return ProjectCache(
            client=info.client,
            contact=info.contact,
            designation=info.designation,
            managed_by=info.managed_by,
            read_at=self._clock(),
        )

    # -- « À planifier » ------------------------------------------------------

    def _on_card_activated(self, task_id: str) -> None:
        self._open_detail(task_id, None)

    def _on_card_action(self, task_id: str, action: str) -> None:
        task = self._task(task_id)
        if task is None:
            return
        if action == "detail":
            self._open_detail(task_id, None)
        elif action == "duplicate":
            self._execute(f"Dupliquer {task_name(task)}", step(commands.duplicate_task, task_id))
        elif action == "folder" and task.project_number:
            self._hooks.open_project_dir(task.project_number)
        elif action == "delete":
            changes = self._execute(
                f"Supprimer {task_name(task)}", step(commands.delete_task, task_id)
            )
            if changes:
                self._toast_undo(f"{task_name(task)} : supprimé pour tous")

    def _on_reorder(self, task_id: str, above_id: str, below_id: str) -> None:
        task = self._task(task_id)
        if task is None:
            return
        self._execute(
            f"Réordonner {task_name(task)}",
            step(
                commands.move_backlog, task_id, above_id=above_id or None, below_id=below_id or None
            ),
        )

    def _on_unschedule(self, task_id: str) -> None:
        task = self._task(task_id)
        if task is None:
            return
        changes = self._execute(
            f"Renvoyer {task_name(task)} à planifier", step(commands.unschedule_task, task_id)
        )
        if changes:
            self._toast_undo(f"{task_name(task)} renvoyée à planifier")

    def _on_backlog_filters(self) -> None:
        sort = self._tab.backlog.sort()
        if sort != self._config.backlog_sort:
            self._config.backlog_sort = sort
            self._save_config()

    # -- detail ---------------------------------------------------------------

    def _open_detail(self, task_id: str, focus: str | None) -> None:
        task = self._task(task_id)
        if task is None:
            return
        self._show_task(task, focus)
        self._tab.side.show_detail()

    def _show_task(self, task: PlanningTask, focus: str | None) -> None:
        session = self._session
        if session is None:
            return
        snapshot = session.service.snapshot
        calendar = calendar_resolver(snapshot)(next(iter(task.assignments), None))
        release = dossier_release(snapshot, task)
        if release != task.project_cache.atelier_release:
            # Shown only: « Sorti atelier » of the dossier, kept on its « Fabrication » card.
            cache = task.project_cache.model_copy(update={"atelier_release": release})
            task = task.model_copy(update={"project_cache": cache})
        self._tab.detail.set_task(
            task,
            snapshot,
            calendar=calendar,
            project=self._project_info(task),
            findings=self._task_findings(task, snapshot),
            read_only=self.read_only,
            focus_resource_id=focus,
        )

    def _show_selection_detail(self) -> None:
        session = self._session
        if session is None:
            return
        snapshot = session.service.snapshot
        ids = self._selected_ids()
        tasks = [task for task_id in ids if (task := self._task(task_id)) is not None]
        if len(tasks) == 1:
            bar = next(
                (key for key in self._tab.grid.selected_bars() if key[0] == tasks[0].id), None
            )
            focus = bar[1] if bar is not None and row_kind(bar[1]) == "resource" else None
            self._show_task(tasks[0], focus)
            return
        if not tasks:
            return
        common: dict[str, object] = {}
        for name in ("task_type", "status", "urgent"):
            values = {getattr(task, name) for task in tasks}
            if len(values) == 1:
                common[name] = values.pop()
        shared = set(tasks[0].assignments)
        for task in tasks[1:]:
            shared &= set(task.assignments)
        common["resource_ids"] = [rid for rid in tasks[0].assignments if rid in shared]
        self._tab.detail.set_multi(len(tasks), common, snapshot=snapshot, read_only=self.read_only)

    def _selected_ids(self) -> list[str]:
        return list(bars_by_task(self._tab.grid.selected_bars()))

    def _targets(self, task_id: str) -> list[str]:
        if task_id == MULTI_SELECTION:
            return [task_id for task_id in self._selected_ids() if self._task(task_id)]
        return [task_id] if self._task(task_id) is not None else []

    def _on_field_changed(self, task_id: str, name: str, value: object) -> None:
        targets = self._targets(task_id)
        if not targets:
            return
        fields: dict[str, object] = {name: value}
        if name == "project_number":
            number = str(value or "").strip()
            fields = {"project_number": number}
            info = self._projects.get(number)
            if info is not None:
                fields["project_cache"] = self._project_cache(number, info)
        if name == "off_hours":
            night = bool(value)
            verb = "Passer en travail de nuit" if night else "Remettre en horaire de jour"
            changes = self._execute(
                f"{verb} {self._names(targets)}",
                chain(step(commands.set_night, target, night) for target in targets),
            )
            if changes is None:
                self._refresh_from_service()
            return
        subject = FIELD_LABELS.get(name, name)
        changes = self._execute(
            f"Modifier {subject} de {self._names(targets)}",
            chain(step(commands.update_task, target, **fields) for target in targets),
        )
        if changes is None:
            self._refresh_from_service()

    def _on_people_changed(self, task_id: str, added: object, removed: object) -> None:
        targets = self._targets(task_id)
        adds = [str(rid) for rid in added] if isinstance(added, list) else []
        removals = [str(rid) for rid in removed] if isinstance(removed, list) else []
        if not targets or not (adds or removals):
            return
        steps: list[Step] = []
        for target in targets:
            steps.extend(step(commands.assign, target, rid) for rid in adds)
            steps.extend(step(commands.unassign, target, rid) for rid in removals)
        names = self._names(targets)
        if adds and not removals:
            label = f"Ajouter {', '.join(self._who(rid) for rid in adds)} à {names}"
        elif removals and not adds:
            label = f"Retirer {', '.join(self._who(rid) for rid in removals)} de {names}"
        else:
            label = f"Modifier les personnes de {names}"
        if self._execute(label, chain(steps)) is None:
            self._refresh_from_service()

    def _on_dates_changed(self, task_id: str, start: int, end: int) -> None:
        task = self._task(task_id)
        if task is None:
            return
        changes = self._execute(
            f"Modifier les dates de {task_name(task)}",
            step(commands.resize_task, task_id, start=start, end=end),
        )
        if changes is None:
            self._refresh_from_service()

    def _on_status_changed(self, task_id: str, status: str) -> None:
        targets = self._targets(task_id)
        if not targets or status not in STATUS_LABELS:
            return
        changes = self._execute(
            f"Statut « {STATUS_LABELS[status]} » : {self._names(targets)}",
            chain(step(commands.set_status, target, status) for target in targets),
        )
        if changes is None:
            self._refresh_from_service()

    def _on_detail_shift(self, task_id: str, days: int) -> None:
        targets = [
            target
            for target in self._targets(task_id)
            if (task := self._task(target)) is not None and task.scheduled
        ]
        if not targets or not days:
            return
        self._execute(
            f"Décaler {self._names(targets)} de {days:+d} j",
            step(commands.shift_tasks, targets, days=days),
        )

    def _on_detail_action(self, task_id: str, action: str) -> None:
        targets = self._targets(task_id)
        first = self._task(targets[0]) if targets else None
        if first is None:
            return
        if action in NAVIGATION_ACTIONS:
            self._navigate(action, first.project_number)
        elif action == "duplicate":
            self._execute(
                f"Dupliquer {self._names(targets)}",
                chain(step(commands.duplicate_task, target) for target in targets),
            )
        elif action == "detach":
            resource_id = self._tab.detail.focus_resource_id()
            if resource_id:
                self._detach(first, resource_id)
        elif action in {"unschedule", "delete"}:
            self._remove_tasks(targets, permanent=action == "delete")

    def _remove_tasks(self, targets: Sequence[str], *, permanent: bool) -> None:
        """« Renvoyer à planifier » or « Supprimer » from the detail, with « Annuler »."""
        if permanent:
            names = self._names(targets)
            if self._execute(
                f"Supprimer {names}",
                chain(step(commands.delete_task, target) for target in targets),
            ):
                self._toast_undo(f"{names} : supprimé pour tous")
            return
        scheduled = [t for t in targets if (task := self._task(t)) is not None and task.scheduled]
        names = self._names(scheduled)
        if scheduled and self._execute(
            f"Renvoyer {names} à planifier",
            chain(step(commands.unschedule_task, target) for target in scheduled),
        ):
            self._toast_undo(f"{names} : renvoyé à planifier")

    def _detach(self, task: PlanningTask, resource_id: str) -> None:
        changes = self._execute(
            f"Détacher {self._who(resource_id)} de {task_name(task)}",
            step(commands.detach_assignment, task.id, resource_id),
        )
        if changes and len(changes) > 1:
            self._select_after = [(changes[1].doc_id, resource_id)]

    def _navigate(self, action: str, number: str) -> None:
        if not number:
            self._toast("Tâche interne : aucun dossier lié.")
            return
        handlers: Mapping[str, Callable[[str], None]] = {
            "folder": self._hooks.open_project_dir,
            "fiche": self._hooks.open_fiche,
            "repertoire": self._hooks.show_in_repertoire,
            "creation": self._hooks.load_in_creation,
            "sortie": self._hooks.prepare_sortie,
        }
        try:
            handlers[action](number)
        except Exception as exc:  # noqa: BLE001 - shown in a toast, never a dialog
            get_logger(__name__).exception("planning.navigate.failed", action=action)
            self._toast(f"Action impossible : {exc}")

    def _refresh_from_service(self) -> None:
        session = self._session
        if session is not None:
            self._refresh_detail(session.service.snapshot)

    def _project_info(self, task: PlanningTask) -> ProjectInfo | None:
        number = task.project_number
        if not number:
            return None
        found = self._projects.get(number)
        if found is not None:
            return found
        cache = task.project_cache
        return ProjectInfo(
            number=number,
            client=cache.client,
            contact=cache.contact,
            designation=cache.designation,
            managed_by=cache.managed_by,
        )

    def _task_findings(self, task: PlanningTask, snapshot: PlanningSnapshot) -> list[Finding]:
        if task.start is None or task.end is None:
            return []
        found = findings_by_bar(
            snapshot,
            wall_date(task.start),
            wall_date(task.end - 1),
            calendar_resolver(snapshot),
            self._project_exists,
        )
        return [
            finding
            for (task_id, _row), items in found.items()
            if task_id == task.id
            for finding in items
        ]

    # -- side panel -----------------------------------------------------------

    def _on_panel_width(self, width: int) -> None:
        if width != self._config.panel_width:
            self._config.panel_width = width
            self._save_config()

    def _on_panel_collapsed(self, collapsed: bool) -> None:  # noqa: FBT001
        if self._tab.panel_adapting:
            return  # folded for lack of room, not by choice: the preference stays
        if self._config.panel_open == collapsed:
            self._config.panel_open = not collapsed
            self._save_config()

    def _on_face_changed(self, face: str) -> None:
        if face == "detail" and self._tab.detail.mode() == "empty" and self._selected_ids():
            self._show_selection_detail()

    # -- toolbar --------------------------------------------------------------

    def _on_view_changed(self) -> None:
        # The view switched here is not « Vue à l'ouverture » (settings of the planning).
        tab = self._tab
        if tab.color_by != self._config.color_by:
            self._config.color_by = tab.color_by
            self._save_config()
        self._schedule_rebuild()

    def _on_display_size_changed(self) -> None:
        tab = self._tab
        heights, zooms = tab.row_heights(), tab.zooms()
        if (heights, zooms) != (self._config.row_heights, self._config.column_zooms):
            self._config.row_heights = heights
            self._config.column_zooms = zooms
            self._save_config()

    def _on_density_chosen(self, density: str) -> None:
        value = DENSITIES.get(density)
        if value is not None and value != self._config.density:
            self._config.density = value
            self._save_config()

    def _on_settings(self) -> None:
        try:
            self._hooks.open_settings("planning")
        except Exception:  # noqa: BLE001
            get_logger(__name__).exception("planning.settings.failed")

    def _on_retry(self) -> None:
        if not self._closed:
            self._hooks.schedule(self._guarded(self._retry(), "planning.retry.failed"))

    def _on_print(self) -> None:
        if self._tab.grid.model is not None:
            self._hooks.schedule(self._guarded(self._print(), "planning.print.failed"))

    def _on_pdf(self) -> None:
        if self._tab.grid.model is not None:
            self._hooks.schedule(self._guarded(self._export_pdf(), "planning.pdf.failed"))

    async def _print(self) -> None:
        tab = self._tab
        poste = self._config.poste_name
        await self._hooks.ask(lambda: print_with_dialog(tab.grid, poste=poste, dialog_parent=tab))

    async def _export_pdf(self) -> None:
        tab = self._tab
        default = documents_dir() / f"Planning {period_text(tab.view, tab.anchor)}.pdf"

        def ask_path() -> str:
            path, _filter = QFileDialog.getSaveFileName(
                tab, "Exporter le planning en PDF", str(default), "PDF (*.pdf)"
            )
            return path

        chosen = await self._hooks.ask(ask_path)
        if not chosen:
            return
        path = Path(str(chosen))
        pages = export_pdf(tab.grid, path, poste=self._config.poste_name)
        self._toast(
            f"PDF enregistré ({pages} page{'s' if pages > 1 else ''}) : {path.name}",
            action_text="Ouvrir",
            action=lambda: open_file_default_app(path),
        )

    async def _guarded(self, work: Awaitable[None], event: str) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background work: a toast, no dialog
            get_logger(__name__).exception(event)
            self._toast(f"Action impossible : {exc}")

    def _on_tab_visibility(self) -> None:
        tab = self._tab
        shown = tab.isVisible() and not tab.window().isMinimized()
        self.set_visible(visible=shown, window_active=shown and tab.isActiveWindow())

    def _focus_grid(self) -> None:
        self._tab.grid.view.setFocus(Qt.FocusReason.OtherFocusReason)

    # -- menus ----------------------------------------------------------------

    def _close_menu(self) -> None:
        menu, self._menu = self._menu, None
        if menu is not None:
            menu.hide()  # aboutToHide deletes it

    def _popup(self, menu: QMenu, position: QPoint) -> None:
        self._close_menu()
        self._menu = menu
        menu.aboutToHide.connect(lambda: self._menu_hidden(menu))
        menu.popup(position)

    def _menu_hidden(self, menu: QMenu) -> None:
        # Deleted once its action ran: Qt hides a menu before it triggers the action.
        if self._menu is menu:
            self._menu = None
        menu.deleteLater()

    def _on_context_menu(self, task_id: str, row_id: str, start: int, position: QPoint) -> None:
        self._hide_hover()
        if task_id:
            menu = self.bar_menu(task_id, row_id)
        elif row_id:
            menu = self.cell_menu(row_id, start)
        else:
            return
        if menu is not None:
            self._popup(menu, position)

    def bar_menu(self, task_id: str, row_id: str) -> QMenu | None:
        """Build the menu of a bar: status, type, people, detail, dossier actions."""
        session = self._session
        task = self._task(task_id)
        if session is None or task is None:
            return None
        snapshot = session.service.snapshot
        editable = not self.read_only
        menu = QMenu(self._tab)
        status_menu = menu.addMenu("Statut")
        for status in STATUSES:
            action = status_menu.addAction(STATUS_LABELS[status])
            action.setCheckable(True)
            action.setChecked(task.status == status)
            action.setEnabled(editable)
            action.triggered.connect(
                lambda _checked=False, value=status: self._on_status_changed(task_id, value)
            )
        type_menu = menu.addMenu("Type")
        for kind in snapshot.meta.task_types:
            action = type_menu.addAction(kind.label)
            action.setCheckable(True)
            action.setChecked(task.task_type == kind.key)
            action.setEnabled(editable)
            action.triggered.connect(
                lambda _checked=False, key=kind.key: self._on_field_changed(
                    task_id, "task_type", key
                )
            )
        type_menu.addSeparator()
        type_menu.addAction("Configurer les types…").triggered.connect(
            lambda _checked=False: self._types_in_team()
        )
        people_menu = menu.addMenu("Affecter")
        people_menu.setEnabled(editable)
        entry_like = _span_entry(task)
        for resource in snapshot.active_resources():
            if resource.id in task.assignments:
                continue
            note = self.availability(resource.id, entry_like).text
            text = f"{resource.short_label}  {resource.name}" + (f" · {note}" if note else "")
            action = people_menu.addAction(text)
            action.triggered.connect(
                lambda _checked=False, rid=resource.id: self._on_move(
                    task_id, row_id, rid, task.start or 0, "add"
                )
            )
        menu.addSeparator()
        menu.addAction("Détail\tEntrée").triggered.connect(
            lambda: self._open_detail(task_id, row_id if row_kind(row_id) == "resource" else None)
        )
        duplicate = menu.addAction("Dupliquer")
        duplicate.setEnabled(editable)
        duplicate.triggered.connect(lambda: self._on_card_action(task_id, "duplicate"))
        person = row_kind(row_id) == "resource" and row_id in task.assignments
        if person:
            who = self._who(row_id)
            detach = menu.addAction(f"Détacher {who}")
            detach.setEnabled(editable and len(task.assignments) > 1)
            detach.triggered.connect(lambda: self._detach(task, row_id))
            remove = menu.addAction(f"Retirer {who}\tSuppr")
            remove.setEnabled(editable)
            remove.triggered.connect(lambda: self._on_delete([(task_id, row_id)], False))  # noqa: FBT003
        unschedule = menu.addAction("Renvoyer à planifier")
        unschedule.setEnabled(editable)
        unschedule.triggered.connect(lambda: self._on_unschedule(task_id))
        menu.addSeparator()
        number = task.project_number
        for key, text in (
            ("folder", "Ouvrir le dossier"),
            ("fiche", "Ouvrir la fiche"),
            ("repertoire", "Voir dans Répertoire"),
            ("creation", "Charger dans Création"),
            ("sortie", "Préparer la sortie"),
        ):
            action = menu.addAction(text)
            action.setEnabled(bool(number))
            action.triggered.connect(lambda _checked=False, name=key: self._navigate(name, number))
        menu.addAction("Copier le résumé").triggered.connect(lambda: self._copy_summary(task_id))
        menu.addSeparator()
        delete = menu.addAction("Supprimer pour tous\tMaj+Suppr")
        delete.setEnabled(editable)
        delete.triggered.connect(lambda: self._on_delete([(task_id, row_id)], True))  # noqa: FBT003
        return menu

    def cell_menu(self, row_id: str, start: int) -> QMenu | None:
        """Build the menu of an empty cell: a new task here, an absence of that person."""
        if self._session is None:
            return None
        editable = not self.read_only
        menu = QMenu(self._tab)
        new = menu.addAction("Nouvelle tâche ici…")
        new.setEnabled(editable)
        new.triggered.connect(
            lambda: self._open_cell_editor((row_id,), start, self._default_end(row_id, start))
        )
        if row_kind(row_id) == "resource":
            absence_menu = menu.addMenu("Absence ce jour")
            absence_menu.setEnabled(editable)
            for reason, text in ABSENCE_LABELS.items():
                action = absence_menu.addAction(text)
                action.triggered.connect(
                    lambda _checked=False, value=reason: self._create_day_absence(
                        row_id, wall_date(start), value
                    )
                )
        return menu

    def _create_day_absence(self, row_id: str, day: date, reason: str) -> None:
        value = absence_reason(reason)
        if value is None:
            return
        self._execute(
            f"Absence {ABSENCE_LABELS[value]} {self._who(row_id)} {day:%d.%m}",
            step(commands.create_absence, resource_id=row_id, first_day=day, reason=value),
        )

    def _on_row_menu(self, row_id: str, position: QPoint) -> None:
        menu = self.row_menu(row_id)
        if menu is not None:
            self._popup(menu, position)

    def row_menu(self, row_id: str) -> QMenu | None:
        """Build the menu of a name: an absence, hide the row here, edit it in the team."""
        menu = QMenu(self._tab)
        if row_kind(row_id) == "resource":
            who = self._who(row_id)
            absence = menu.addAction("Ajouter une absence…")
            absence.setEnabled(not self.read_only)
            absence.triggered.connect(lambda: self.new_absence(row_id))
            absences = menu.addAction(f"Absences de {who}…")
            absences.triggered.connect(lambda: self._absences_in_team(row_id))
            hide = menu.addAction("Masquer sur ce poste")
            hide.triggered.connect(lambda: self._hide_row(row_id))
            edit = menu.addAction("Modifier dans Équipe…")
            edit.triggered.connect(lambda: self._edit_in_team(row_id))
        hidden = len(self._config.hidden_resource_ids)
        if hidden:
            show = menu.addAction(f"Afficher les lignes masquées ({hidden})")
            show.triggered.connect(self._show_hidden_rows)
        return menu if menu.actions() else None

    def _hide_row(self, row_id: str) -> None:
        self._config.hidden_resource_ids = [*self._config.hidden_resource_ids, row_id]
        self._save_config()
        self.refresh_now()

    def _show_hidden_rows(self) -> None:
        self._config.hidden_resource_ids = []
        self._save_config()
        self.refresh_now()

    def _edit_in_team(self, row_id: str) -> None:
        dialog = self._team_dialog
        if dialog is None:
            # Opened later once the Microsoft 365 members are read: select it then.
            self._team_focus = row_id
        self.open_team_dialog()
        dialog = self._team_dialog
        if dialog is not None:
            self._team_focus = ""
            dialog.select_resource(row_id)

    def _types_in_team(self) -> None:
        self._team_types = self._team_dialog is None
        self.open_team_dialog()
        dialog = self._team_dialog
        if dialog is not None:
            self._team_types = False
            dialog.show_page("types")

    def _absences_in_team(self, row_id: str) -> None:
        self.open_team_dialog()
        dialog = self._team_dialog
        if dialog is not None:
            dialog.show_absences(row_id)

    # -- absences -------------------------------------------------------------

    def _absence(self, absence_id: str) -> Absence | None:
        session = self._session
        if session is None:
            return None
        absence = session.service.snapshot.absences.get(absence_id)
        return absence if absence is not None and not absence.deleted else None

    def absence_summary(
        self, resource_id: str, reason: str, first: date, last: date, part: DayPart = "day"
    ) -> str:
        """« Vacances LBI lun 26.10 → ven 30.10 »."""
        label = ABSENCE_LABELS.get(reason, reason)
        return f"{label} {self._who(resource_id)} {absence_dates_text(first, last, part)}"

    def edit_absence(self, absence_id: str) -> None:
        """Open « Absence » on ``absence_id``; a recurring day off opens its person instead."""
        self._hide_hover()
        person = recurring_resource_id(absence_id)
        if person is not None:
            self._toast(RECURRING_MESSAGE)
            self._edit_in_team(person)
            return
        absence = self._absence(absence_id)
        if absence is None:
            return
        if self.read_only:
            self._toast(self._read_only_message())
            return
        self._open_absence_dialog(absence=absence)

    def new_absence(self, resource_id: str) -> None:
        """Open « Nouvelle absence » for ``resource_id``, from today or the period shown."""
        if self._session is None or self.read_only:
            self._toast(self._read_only_message())
            return
        first, last = self._tab.period_range()
        today = self._today()
        self._open_absence_dialog(
            resource_id=resource_id, first_day=today if first <= today <= last else first
        )

    def remove_absence(self, absence_id: str) -> None:
        """Delete an absence for everyone, with « Annuler » on the toast."""
        absence = self._absence(absence_id)
        if absence is None:
            return
        who = self._who(absence.resource_id)
        changes = self._execute(
            f"Supprimer l'absence de {who}", step(commands.delete_absence, absence_id)
        )
        if changes:
            summary = self.absence_summary(
                absence.resource_id,
                absence.reason,
                absence.first_day,
                absence.last_day,
                absence.part,
            )
            self._toast_undo(f"Absence supprimée : {summary}")

    def _open_absence_dialog(
        self,
        *,
        absence: Absence | None = None,
        resource_id: str = "",
        first_day: date | None = None,
    ) -> None:
        session = self._session
        if session is None:
            return
        current = self._absence_dialog
        if current is not None:
            current.raise_()
            current.activateWindow()
            return
        dialog = AbsenceDialog(
            session.service.snapshot,
            self._apply_builder,
            absence=absence,
            resource_id=resource_id,
            first_day=first_day,
            parent=self._tab.window(),
        )
        dialog.finished.connect(lambda _result, shown=dialog: self._absence_closed(shown))
        self._absence_dialog = dialog
        dialog.open()

    def _absence_closed(self, dialog: AbsenceDialog) -> None:
        if dialog is self._absence_dialog:
            self._absence_dialog = None
        if dialog.outcome:
            summary = self.absence_summary(
                dialog.resource_id(),
                dialog.reason(),
                dialog.first_day(),
                dialog.last_day(),
                dialog.part(),
            )
            verb = {"created": "enregistrée", "saved": "modifiée", "deleted": "supprimée"}
            self._toast_undo(f"Absence {verb[dialog.outcome]} : {summary}")
        dialog.deleteLater()

    def _on_absence_menu(self, absence_id: str, position: QPoint) -> None:
        self._hide_hover()
        menu = self.absence_menu(absence_id)
        if menu is not None:
            self._popup(menu, position)

    def absence_menu(self, absence_id: str) -> QMenu | None:
        """Build the menu of an absence block: change or delete it (its person if recurring)."""
        menu = QMenu(self._tab)
        person = recurring_resource_id(absence_id)
        if person is not None:
            title = menu.addAction(f"Jour récurrent de {self._who(person)}")
            title.setEnabled(False)
            menu.addAction("Modifier dans Équipe…").triggered.connect(
                lambda: self._edit_in_team(person)
            )
            return menu
        absence = self._absence(absence_id)
        if absence is None:
            return None
        editable = not self.read_only
        edit = menu.addAction("Modifier l'absence…")
        edit.setEnabled(editable)
        edit.triggered.connect(lambda: self.edit_absence(absence_id))
        delete = menu.addAction("Supprimer l'absence")
        delete.setEnabled(editable)
        delete.triggered.connect(lambda: self.remove_absence(absence_id))
        menu.addSeparator()
        menu.addAction(f"Absences de {self._who(absence.resource_id)}…").triggered.connect(
            lambda: self._absences_in_team(absence.resource_id)
        )
        return menu

    def absence_hover_info(self, absence_id: str) -> HoverInfo | None:
        """Build what the hover card shows for an absence block (None when it is gone)."""
        session = self._session
        if session is None:
            return None
        snapshot = session.service.snapshot
        person = recurring_resource_id(absence_id)
        if person is not None:
            return self._recurring_hover_info(absence_id, person, snapshot)
        absence = self._absence(absence_id)
        if absence is None:
            return None
        resource = snapshot.resources.get(absence.resource_id)
        return HoverInfo(
            number=self._who(absence.resource_id),
            client=resource.name if resource is not None else "",
            title=absence.label,
            type_label="Absence",
            when=absence_dates_text(absence.first_day, absence.last_day, absence.part),
            notes=absence.note,
            author=author_text(absence, datetime.now(UTC)),
        )

    def _recurring_hover_info(
        self, absence_id: str, person: str, snapshot: PlanningSnapshot
    ) -> HoverInfo:
        resource = snapshot.resources.get(person)
        model = self._tab.grid.model
        block = model.absence_block(absence_id) if model is not None else None
        weekday, part = _recurring_parts(absence_id)
        when = f"Chaque {WEEKDAY_NAMES[weekday]}" if 0 <= weekday < len(WEEKDAY_NAMES) else ""
        if part in {"morning", "afternoon"}:
            when = f"{when} · {'matin' if part == 'morning' else 'après-midi'}"
        return HoverInfo(
            number=self._who(person),
            client=resource.name if resource is not None else "",
            title=block.label if block is not None else "Jour non disponible",
            type_label="Jour récurrent",
            when=when,
            notes=RECURRING_MESSAGE,
        )

    def _copy_summary(self, task_id: str) -> None:
        text = self.summary_text(task_id)
        clipboard = QGuiApplication.clipboard()
        if text and clipboard is not None:
            clipboard.setText(text)
            self._toast("Résumé copié.")

    def summary_text(self, task_id: str) -> str:
        """« 2026-118 Müller SA · Pose garde-corps · lun 26.10 06:30 → … · KDU, LBI »."""
        session = self._session
        task = self._task(task_id)
        if session is None or task is None:
            return ""
        snapshot = session.service.snapshot
        head = " ".join(part for part in (task.project_number, task.project_cache.client) if part)
        parts = [head, task.display_title]
        if task.start is not None and task.end is not None:
            calendar = calendar_resolver(snapshot)(next(iter(task.assignments), None))
            parts.append(span_label(calendar, task.start, task.end))
        people = [self._who(rid) for rid in task.assignments]
        parts.append(", ".join(people) if people else "Non affecté")
        return " · ".join(part for part in parts if part)

    # -- hover ----------------------------------------------------------------

    def _on_hover(self, task_id: str, position: QPoint) -> None:
        self._hover_task = task_id
        if task_id:
            self._hover_absence = ""
        self._restart_hover(position)

    def _on_absence_hover(self, absence_id: str, position: QPoint) -> None:
        self._hover_absence = absence_id
        if absence_id:
            self._hover_task = ""
        self._restart_hover(position)

    def _restart_hover(self, position: QPoint) -> None:
        self._hover_pos = QPoint(position)
        self._tab.hover.hide()
        if not (self._hover_task or self._hover_absence) or self._tab.grid.gestures.active:
            self._hover_timer.stop()
            return
        self._hover_timer.start()

    def _hide_hover(self) -> None:
        self._hover_timer.stop()
        self._hover_task = ""
        self._hover_absence = ""
        self._tab.hover.hide()

    def _show_hover(self) -> None:
        tab = self._tab
        if (
            not (self._hover_task or self._hover_absence)
            or not tab.isVisible()
            or not tab.isActiveWindow()
            or tab.grid.gestures.active
            or tab.editor.is_open()
        ):
            return
        if self._hover_task:
            info = self.hover_info(self._hover_task)
        else:
            info = self.absence_hover_info(self._hover_absence)
        if info is not None:
            self._tab.hover.show_for(info, self._hover_pos)

    def _hover_info(self, task: PlanningTask, snapshot: PlanningSnapshot) -> HoverInfo:
        meta = snapshot.meta
        calendar_for = calendar_resolver(snapshot)
        calendar = calendar_for(next(iter(task.assignments), None))
        dark = current_theme().is_dark
        kind = meta.task_type(task.task_type)
        project = self._project_info(task)
        cache = task.project_cache
        when = "À planifier"
        duration = ""
        current: tuple[WallTime, WallTime] | None = None
        rest = ""
        if task.off_hours and task.start is not None and task.end is not None:
            when = f"{MOON} nuit {moment_label(task.start)} → {moment_label(task.end)}"
            duration = elapsed_text(task.end - task.start)
            rest = rest_text(task.start, task.end)
            current = (task.start, task.end)
        elif task.start is not None and task.end is not None:
            when = f"{moment_label(task.start)} → {moment_label(task.end)}"
            days = task_days(calendar, task)
            minutes = calendar.work_between(task.start, task.end)
            duration = format_days(days) if days else format_duration(minutes)
            current = (task.start, task.end)
        elif task.estimate_days:
            duration = f"~{format_days(task.estimate_days)}"
        elif task.estimate_minutes:
            duration = f"~{format_duration(task.estimate_minutes)}"
        segments: list[tuple[WallTime, WallTime, str]] = []
        minutes_total = 0
        if task.project_number:
            for other in snapshot.live_tasks():
                if (
                    other.project_number != task.project_number
                    or other.start is None
                    or other.end is None
                ):
                    continue
                other_kind = meta.task_type(other.task_type)
                segments.append(
                    (other.start, other.end, other_kind.color_dark if dark else other_kind.color)
                )
                other_calendar = calendar_for(next(iter(other.assignments), None))
                minutes_total += (
                    other.end - other.start
                    if other.off_hours
                    else other_calendar.work_between(other.start, other.end)
                )
        model = self._tab.grid.model
        findings: list[str] = []
        if model is not None:
            for bar in model.bars_by_task.get(task.id, ()):
                findings.extend(item for item in bar.findings if item not in findings)
        return HoverInfo(
            number=task.project_number,
            designation=(project.designation if project is not None else "") or cache.designation,
            client=(project.client if project is not None else "") or cache.client,
            contact=(project.contact if project is not None else "") or cache.contact,
            atelier_release=_short_date(dossier_release(snapshot, task)),
            managed_by=cache.managed_by or (project.managed_by if project is not None else ""),
            location=cache.location,
            title=task.display_title,
            type_label=kind.label,
            type_color=kind.color_dark if dark else kind.color,
            when=when,
            duration=duration,
            rest=rest,
            people=tuple(self._who(rid) for rid in task.assignments),
            status=STATUS_LABELS.get(task.status, task.status),
            notes=task.notes,
            findings=tuple(findings),
            author=author_text(task, datetime.now(UTC)),
            dossier_tasks=tuple(sorted(segments)),
            current=current,
            dossier_minutes=minutes_total,
        )

    # -- dossiers -------------------------------------------------------------

    def _find_project(self, number: str) -> ProjectInfo | None:
        clean = number.strip()
        found = self._projects.get(clean)
        if found is None and clean:
            self._want_project(clean)
        return found

    def _want_project(self, number: str) -> None:
        """Look ``number`` up (its fiche) when no répertoire read holds it."""
        year = number[:4]
        if (
            self._hooks.find_project is None
            or number in self._projects
            or number in self._looked_up
            or not (year.isdigit() and len(number) > len(year) and number[len(year)] == "-")
            or int(year) in self._project_years
        ):
            return
        self._lookup_queue.add(number)
        if not self._lookup_running and not self._closed:
            self._lookup_running = True
            self._hooks.schedule(self._lookup_projects())

    def _want_task_projects(self, snapshot: PlanningSnapshot) -> None:
        for task in snapshot.live_tasks():
            if task.project_number:
                self._want_project(task.project_number.strip())

    async def _lookup_projects(self) -> None:
        """Read the fiche of the older dossiers asked for, a few at a time."""
        finder = self._hooks.find_project
        found_any = False
        try:
            while self._lookup_queue and finder is not None and not self._closed:
                batch = sorted(self._lookup_queue)[:LOOKUPS_AT_ONCE]
                for number in batch:
                    self._lookup_queue.discard(number)
                    self._looked_up.add(number)
                    try:
                        info = await finder(number)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - an unread fiche changes nothing
                        get_logger(__name__).info(
                            "planning.project.lookup_failed", error=type(exc).__name__
                        )
                        continue
                    if info is not None and not self._closed:
                        self._older_projects[number] = info
                        self._projects.setdefault(number, info)
                        found_any = True
        finally:
            self._lookup_running = False
        if found_any and not self._closed:
            ordered = sorted(
                self._projects.values(), key=lambda info: number_key(info.number), reverse=True
            )
            tab = self._tab
            tab.quick.set_projects(ordered)
            tab.editor.set_projects(ordered)
            tab.detail.set_project_choices(ordered)
            self._schedule_rebuild()

    def _project_exists(self, number: str) -> bool:
        """Unknown only when the répertoire of that year was read and lacks the dossier."""
        clean = number.strip()
        if not self._project_years or clean in self._projects:
            return True
        year = clean[:4]
        return not (year.isdigit() and int(year) in self._project_years)

    def _request_projects(self, *, force: bool = True) -> None:
        if self._projects_running or self._closed:
            return
        if (
            not force
            and self._projects_at is not None
            and time.monotonic() - self._projects_at < PROJECTS_STALE_SECONDS
        ):
            return
        self._projects_running = True
        self._hooks.schedule(self._refresh_projects())

    async def _refresh_projects(self) -> None:
        """Read the répertoire of the last ``PRELOADED_YEARS`` years (A:E only).

        An older dossier is looked up on its own, from its fiche (``_lookup_projects``).
        """
        try:
            year = self._today().year
            found: dict[str, ProjectInfo] = {}
            years: list[int] = []
            for wanted in range(year, year - PRELOADED_YEARS, -1):
                snapshot = await self._read_year(wanted, past=wanted < year)
                if snapshot is None:
                    continue
                years.append(wanted)
                for row in snapshot.rows:
                    info = project_from_row(tuple(row.values))
                    if info is not None:
                        found.setdefault(info.number, info)
            if years and not self._closed:
                self._set_projects(found, years)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            get_logger(__name__).exception("planning.repertoire.failed")
        finally:
            self._projects_running = False
            self._projects_at = time.monotonic()

    async def _read_year(self, year: int, *, past: bool) -> RepertoireSnapshot | None:
        """Read the répertoire of ``year``; None when unread (a past year without a sheet: once)."""
        if year in self._missing_years:
            return None
        try:
            return await self._hooks.read_repertoire(year)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the numbers typed stay accepted
            get_logger(__name__).info(
                "planning.repertoire.unread", year=year, error=type(exc).__name__
            )
            if past and isinstance(exc, ProjectCreationError):
                # No sheet for a past year: it will not appear during this session.
                self._missing_years.add(year)
            return None

    def _set_projects(self, found: Mapping[str, ProjectInfo], years: Sequence[int]) -> None:
        older = {
            number: info for number, info in self._older_projects.items() if number not in found
        }
        self._projects = {**older, **found}
        self._project_years = frozenset(years)
        found = self._projects
        ordered = sorted(found.values(), key=lambda info: number_key(info.number), reverse=True)
        tab = self._tab
        tab.quick.set_projects(ordered)
        tab.editor.set_projects(ordered)
        tab.detail.set_project_choices(ordered)
        self._schedule_rebuild()

    # -- sortie cards ---------------------------------------------------------

    async def _ensure_card(
        self,
        number: str,
        *,
        designation: str,
        client: str,
        contact: str,
        managed_by: str,
        released: date | None,
    ) -> bool:
        clean = number.strip()
        if self._closed or not clean:
            return False
        session = await self._settled_session()
        if self.is_closed():
            return False
        if session is None or not session.loaded.is_set() or session.failure is not None:
            reason = (
                "planning en cours de chargement"
                if session is None or session.failure is None
                # Its outbox would wait in the cache of a store not reached yet (SharePoint
                # ids still unknown): the card could be lost once the list is found.
                else f"planning indisponible : {session.failure.message}"
            )
            self._log(f"Planning : carte à planifier non créée pour {clean} ({reason}).")
            return False
        service = session.service
        snapshot = service.snapshot
        if not snapshot.meta.auto_card_on_sortie:
            return False
        cards = [
            task
            for task in snapshot.live_tasks()
            if task.origin == "sortie_dossier" and task.project_number == clean
        ]
        open_card = next((task for task in cards if task.status != "done"), None)
        if open_card is not None:
            if open_card.scheduled:
                self._flash_after.add(open_card.id)
            else:
                self._new_after.add(open_card.id)
            self._schedule_rebuild()
            self._log(f"Planning : {clean} a déjà sa carte « {clip(open_card.display_title)} ».")
            return False
        title = f"Fabrication – {designation.strip()}" if designation.strip() else "Fabrication"  # noqa: RUF001
        if cards:
            when = f" {released:%d.%m}" if released is not None else ""
            title = f"{title} ({len(cards) + 1}e sortie{when})"
        cache = ProjectCache(
            client=client,
            contact=contact,
            designation=designation,
            managed_by=managed_by,
            atelier_release=released.isoformat() if released is not None else "",
            read_at=self._clock(),
        )
        changes = commands.create_task(
            self._context(snapshot),
            title=title,
            task_type=_workshop_type(snapshot),
            project_number=clean,
            project_cache=cache,
            origin="sortie_dossier",
        )
        service.apply(changes, label=f"Carte à planifier {clean}")
        await service.save()
        self._card_after = changes[0].doc_id
        self._schedule_rebuild()
        return True

    async def _settled_session(self) -> _Session | None:
        """Wait for an opening in progress to end and for its cache, then give the session.

        None (or a session not loaded yet) after ``CARD_WAIT_SECONDS``.
        """
        session: _Session | None = None
        try:
            async with asyncio.timeout(CARD_WAIT_SECONDS):
                async with self._lifecycle:
                    session = self._session
                if session is not None:
                    await session.loaded.wait()
        except TimeoutError:
            get_logger(__name__).info("planning.sortie_card.not_ready")
        return session

    # -- team dialog ----------------------------------------------------------

    async def _open_team_dialog_later(self) -> None:
        try:
            members: list[PlannerMember] = []
            try:
                async with asyncio.timeout(MEMBERS_TIMEOUT_SECONDS):
                    found = await self._hooks.planner_members()
                members = [item for member in found if (item := _member_tuple(member))]
                self._members = members
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the roster opens without them
                get_logger(__name__).info("planning.members.unread", error=type(exc).__name__)
            if not self._closed and self._session is not None:
                self._show_team_dialog(members)
        finally:
            self._team_opening = False

    def _show_team_dialog(self, members: Sequence[PlannerMember] | None = None) -> None:
        if self._team_dialog is not None or self._session is None:
            return
        parent = self._team_parent()
        dialog = TeamDialog(
            self._team_snapshot,
            self._apply_builder,
            planner_members=list(members if members is not None else self._members or ()),
            parent=parent,
            read_only_reason=self._read_only_reason,
            today=self._today,
        )
        dialog.finished.connect(self._on_team_closed)
        # Deleted with a modal window that owned it without going home first.
        dialog.destroyed.connect(lambda *_args, gone=dialog: self._forget_team_dialog(gone))
        self._team_dialog = dialog
        self._watch_modal(parent, dialog)
        snapshot = self._team_snapshot()
        self._team_state = _team_state(snapshot)
        dialog.show()
        if self._team_focus:
            dialog.select_resource(self._team_focus)
            self._team_focus = ""
        if self._team_types:
            dialog.show_page("types")
            self._team_types = False

    def _team_parent(self) -> QWidget:
        """Give the modal window in front (Settings), else the main window.

        A window that does not belong to the modal one would be blocked until it closes.
        """
        modal: QWidget | None = QApplication.activeModalWidget()
        dialog = self._team_dialog
        if (
            modal is not None
            and modal is not dialog
            and (dialog is None or not dialog.isAncestorOf(modal))
        ):
            return modal
        return self._tab.window()

    def _adopt_team_dialog(self, dialog: TeamDialog) -> None:
        """Hand an open roster to the modal window in front, so that it stays usable."""
        parent = self._team_parent()
        if dialog.parentWidget() is not parent:
            _move_window(dialog, parent)
            self._watch_modal(parent, dialog)

    def _watch_modal(self, parent: QWidget, dialog: TeamDialog) -> None:
        if isinstance(parent, QDialog) and parent is not self._tab.window():
            parent.finished.connect(lambda _result, kept=dialog: self._team_back_home(kept))

    def _team_back_home(self, dialog: TeamDialog) -> None:
        """Keep the roster open on the main window once the modal window closed."""
        if dialog is not self._team_dialog or self._closed:
            return
        home = self._tab.window()
        if dialog.parentWidget() is not home:
            # Shown again: a window may hide the windows it owns as it closes.
            _move_window(dialog, home, show=True)

    def _forget_team_dialog(self, dialog: TeamDialog) -> None:
        if dialog is self._team_dialog:
            self._team_dialog = None
            self._team_state = None

    def _team_snapshot(self) -> PlanningSnapshot:
        session = self._session
        return session.service.snapshot if session is not None else self._last_snapshot

    def _on_team_closed(self, _result: int) -> None:
        dialog, self._team_dialog = self._team_dialog, None
        self._team_state = None
        if dialog is not None:
            dialog.deleteLater()

    # -- feedback -------------------------------------------------------------

    def _toast(
        self,
        message: str,
        *,
        action_text: str = "",
        action: Callable[[], object] | None = None,
    ) -> None:
        if self._closed:
            return
        self._tab.show_toast(message, action_text=action_text, action=action)

    def _toast_undo(self, message: str) -> None:
        self._tab.show_toast(
            message, action_text="Annuler", action=self.undo, timeout_ms=UNDO_TOAST_MS
        )

    def _log(self, message: str) -> None:
        try:
            self._hooks.log(message)
        except Exception:  # noqa: BLE001 - the log of the application must not break this
            get_logger(__name__).exception("planning.log.failed")


# ----------------------------------------------------------------- tools ----


def next_free_half_day(
    snapshot: PlanningSnapshot, row_id: str, after: WallTime, calendar: WorkCalendar
) -> tuple[WallTime, WallTime] | None:
    """First half day (a worked period) starting at or after ``after`` where the row is free."""
    unassigned = row_kind(row_id) != "resource"
    busy = [
        (task.start, task.end)
        for task in snapshot.live_tasks()
        if task.start is not None
        and task.end is not None
        and ((not task.assignments) if unassigned else row_id in task.assignments)
    ]
    day = wall_date(after)
    for _ in range(MAX_ADVANCE_DAYS):
        for low, high in calendar.day_periods(day):
            if low < after:
                continue
            if not any(start < high and end > low for start, end in busy):
                return low, high
        day = calendar.next_workday(day)
    return None


def half_day_start(calendar: WorkCalendar, value: WallTime) -> WallTime:
    """Start of the worked period (half day) holding ``value``, else of the next one."""
    start = calendar.working_start(value)
    for low, high in calendar.day_periods(wall_date(start)):
        if low <= start < high:
            return low
    return start


def _recurring_parts(block_id: str) -> tuple[int, str]:
    """Weekday and part of a ``recurring_block_id`` (-1 and "" when unreadable)."""
    parts = block_id.split(":", 3)
    try:
        return int(parts[1]), parts[2]
    except (IndexError, ValueError):
        return -1, ""


def capitalized(text: str) -> str:
    """First letter in capital, the rest as typed (« pose PL » gives « Pose PL »)."""
    return text[:1].upper() + text[1:]


def dossier_release(snapshot: PlanningSnapshot, task: PlanningTask) -> str:
    """« Sorti atelier » of the task's dossier: its own, else the latest of the dossier.

    The release date comes with the « Fabrication » card of a Sortie dossier; the tasks
    planned afterwards for the dossier show it too.
    """
    own = task.project_cache.atelier_release
    if own.strip() or not task.project_number:
        return own
    latest: tuple[date, str] | None = None
    for other in snapshot.live_tasks():
        if other.project_number != task.project_number:
            continue
        text = other.project_cache.atelier_release
        day = release_day(text)
        if day is not None and (latest is None or day > latest[0]):
            latest = (day, text)
    return latest[1] if latest is not None else own


_THIS_TASK_RE: Final = re.compile(r"\b[Cc]ette tâche\b")


def conflict_text(event: PlanningEvent, snapshot: PlanningSnapshot) -> tuple[str, str]:
    """Tag and text of a conflict toast: the author once, and the task by its name.

    « DEMO · POSTE-DEMO » with « a modifié « 2026-5003 Pose garde-corps » entre-temps ».
    """
    author = event.author.label if event.author is not None else ""
    message = event.message.strip()
    if author and message.startswith(author):
        message = message[len(author) :].lstrip(" :")
    task_ids = [doc_id for kind, doc_id in event.changed if kind == "task"]
    if len(task_ids) == 1:
        lost = (
            item
            for item in event.restorable
            if isinstance(item, PlanningTask) and item.id == task_ids[0]
        )
        task = next(lost, None) or snapshot.tasks.get(task_ids[0])
        if task is not None:
            named = f"« {task_name(task)} »"
            message, count = _THIS_TASK_RE.subn(named, message, count=1)
            if not count:
                message = f"{named} : {message}"
    return author, message


def _move_window(window: QWidget, parent: QWidget, *, show: bool = False) -> None:
    """Give a top-level window another parent, at the same place on the screen."""
    visible = window.isVisible()
    geometry = window.geometry()
    window.setParent(parent, window.windowFlags())
    window.setGeometry(geometry)
    if visible or show:
        window.show()


async def _cancel(task: asyncio.Task[Any] | None) -> None:
    """Cancel ``task`` and give it ``CANCEL_GRACE_SECONDS`` to end (never raises)."""
    if task is None or task.done() or task is asyncio.current_task():
        return
    task.cancel()
    await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)


def _resize_end(ctx: CommandContext, task_id: str, minutes: int) -> Changes:
    """End moved by worked minutes on the calendar of the task's first assignee."""
    task = ctx.snapshot.tasks.get(task_id)
    if task is None or task.end is None:
        return ()
    if task.off_hours:
        return commands.resize_task(ctx, task_id, end=task.end + minutes)
    calendar = ctx.calendar_for(next(iter(task.assignments), None))
    return commands.resize_task(ctx, task_id, end=calendar.add_work(task.end, minutes))


def _bar_keys(bars: object) -> list[BarKey]:
    if not isinstance(bars, list | tuple):
        return []
    return [
        (str(bar[0]), str(bar[1]) if bar[1] is not None else UNASSIGNED_ROW_ID)
        for bar in bars
        if isinstance(bar, list | tuple) and len(bar) == len(("task", "row"))
    ]


def _span_entry(task: PlanningTask) -> ParsedEntry | None:
    """Build a stand-in entry with the task's dates, for the availability of the people."""
    if task.start is None or task.end is None:
        return None
    return ParsedEntry(
        project_number="",
        project=None,
        title="",
        task_type=task.task_type,
        resource_ids=(),
        start=task.start,
        end=task.end,
        estimate_minutes=0,
        required_people=1,
        provisional=False,
        urgent=False,
        deadline=None,
        notes="",
        absence=None,
        destination="grid",
        tokens=(),
        warnings=(),
        interpretation="",
    )


def _short_date(text: str) -> str:
    try:
        day = date.fromisoformat(text.strip()[:10])
    except ValueError:
        return text.strip()
    return f"{day:%d.%m}"


def _team_state(snapshot: PlanningSnapshot) -> tuple[object, ...]:
    """Return what the team dialog shows: roster, settings, absences, tasks per type."""
    counts = tuple(sorted(task_type_counts(snapshot).items()))
    return (snapshot.resources, snapshot.meta, snapshot.absences, counts)


def _workshop_type(snapshot: PlanningSnapshot) -> str:
    """Type of a workshop card: the default one, else the first type of the workshop team."""
    kinds = snapshot.meta.task_types
    if any(kind.key == DEFAULT_TASK_TYPE for kind in kinds):
        return DEFAULT_TASK_TYPE
    workshop = next((kind for kind in kinds if kind.team == "atelier"), None)
    return workshop.key if workshop is not None else snapshot.meta.default_task_type


async def _close_quietly(store: PlanningStore) -> None:
    try:
        await store.aclose()
    except Exception:  # noqa: BLE001
        get_logger(__name__).exception("planning.store.close_failed")


__all__ = [
    "PlanningController",
    "PlanningHooks",
    "PlanningStoreFactory",
    "UnavailableStore",
    "capitalized",
    "chain",
    "conflict_text",
    "dossier_release",
    "failure_status",
    "half_day_start",
    "merge_changes",
    "next_free_half_day",
    "project_from_row",
    "remote_verb",
    "step",
    "task_name",
]
