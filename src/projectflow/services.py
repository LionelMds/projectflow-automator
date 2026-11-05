from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import (
    GRAPH_SCOPES,
    OUTLOOK_GRAPH_SCOPES,
    PLANNER_GRAPH_SCOPES,
    PLANNING_GRAPH_SCOPES,
    AccessTokenProvider,
    MsalAccessTokenProvider,
)
from projectflow.config import AppConfig, RepertoireChantierConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.models import ProjectInput
from projectflow.core.planning.graph_backend import GraphPlanningBackend
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import PlanningStore, StoreUnavailableError
from projectflow.core.project_service import ProjectService
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import AuthError, ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.drive_planning_store import DrivePlanningStore
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.graph.outlook_calendar import OutlookCalendarGateway
from projectflow.graph.planner import GraphPlannerClient, PlannerTaskResult
from projectflow.graph.planning_store import (
    PlanningListRef,
    find_list,
    group_site_id,
    planning_column_problems,
    site_id_from_url,
)
from projectflow.graph.workbook_opening import resolve_workbook_open_url
from projectflow.logging import get_logger
from projectflow.outlook.local import create_local_outlook_client
from projectflow.platform.filemanager import move_path_to_trash, pin_to_filemanager_favorites
from projectflow.platform.sync_paths import is_synchronized_path

GraphFactory = Callable[[AccessTokenProvider], GraphClient]
"""Builds the Graph client of a token provider (a mocked transport in the tests)."""

PLANNING_REQUEST_TIMEOUT_SECONDS: Final = 30.0
PLANNING_SETTINGS_PLACE: Final = "Paramètres \u203a Planning"
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
HTTP_NOT_FOUND: Final = 404
HTTP_LOCKED: Final = 423
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR: Final = 500

PLANNING_NO_CONNECTOR_MESSAGE: Final = (
    "Planning SharePoint indisponible : cette version de ProjectFlow n'embarque pas le "
    f"connecteur Microsoft. Choisissez le stockage local dans {PLANNING_SETTINGS_PLACE}."
)
OUTLOOK_NO_CONNECTOR_MESSAGE: Final = (
    "Calendrier Outlook indisponible : connexion Microsoft non configurée dans cette version."
)
PLANNING_ONEDRIVE_NO_CONNECTOR_MESSAGE: Final = (
    "Planning OneDrive indisponible : cette version de ProjectFlow n'embarque pas le "
    f"connecteur Microsoft. Choisissez le stockage local dans {PLANNING_SETTINGS_PLACE}."
)
PLANNING_SITE_MISSING_MESSAGE: Final = (
    "Planning SharePoint : indiquez l'adresse du site SharePoint du planning dans "
    f"{PLANNING_SETTINGS_PLACE}, ou choisissez le stockage local."
)
PLANNING_AUTHORIZATION_MESSAGE: Final = (
    "Planning SharePoint : autorisation Microsoft manquante. Un administrateur Microsoft 365 "
    "doit accorder l'autorisation Sites.ReadWrite.All à ProjectFlow Automator "
    "(consentement administrateur en attente). Consultation seule depuis la copie de ce poste."
)
PLANNING_FORBIDDEN_MESSAGE: Final = (
    "Planning SharePoint : accès refusé (403), autorisation manquante. Un administrateur "
    "Microsoft 365 doit accorder Sites.ReadWrite.All à ProjectFlow Automator et l'accès au "
    "site du planning à ce compte. Consultation seule depuis la copie de ce poste."
)
PLANNING_SIGN_IN_MESSAGE: Final = (
    "Planning SharePoint : connexion Microsoft à renouveler. Cliquez sur « Se connecter à "
    "Microsoft » en haut de la fenêtre ; aucune fenêtre de connexion n'est ouverte "
    "automatiquement. Consultation seule depuis la copie de ce poste."
)
PLANNING_OFFLINE_MESSAGE: Final = (
    "Planning SharePoint injoignable pour le moment (réseau ou Microsoft). "
    "Consultation seule depuis la copie de ce poste ; cliquez sur « Réessayer » quand le "
    "réseau est revenu."
)
PLANNING_SITE_NOT_FOUND_MESSAGE: Final = (
    "Planning SharePoint : site ou plan introuvable. Vérifiez l'adresse du site dans "
    f"{PLANNING_SETTINGS_PLACE}."
)


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    application_settings: ApplicationSettings | None = None
    fiche_service: FicheService | None = None
    repertoire_service: RepertoireService | None = None
    planner_service: ConfiguredPlannerGateway | None = None
    # False for headless requests: an expired session is reported, never prompted.
    interactive_sign_in: bool = True
    _graph_clients: list[GraphClient] = field(default_factory=list, init=False, repr=False)

    def fiche(self) -> FicheService:
        if self.fiche_service is None:
            self.fiche_service = FicheService()
        return self.fiche_service

    def repertoire(self) -> RepertoireService:
        if self.repertoire_service is not None:
            return self.repertoire_service
        repertoire = self.config.paths.repertoire_chantier
        display_path = repertoire.display_path.strip()
        workbook_path = Path(display_path).expanduser()
        if repertoire_uses_cloud(repertoire):
            settings = self.application_settings or ApplicationSettings.load()
            if not settings.microsoft_client_id.strip():
                raise ConfigError(
                    "Le repertoire chantier doit etre ecrit directement dans OneDrive, "
                    "mais cette version de ProjectFlow n'embarque pas encore le connecteur "
                    "Microsoft. Ajoutez PROJECTFLOW_MICROSOFT_CLIENT_ID au build de release.",
                )
            token_provider = MsalAccessTokenProvider(
                client_id=settings.microsoft_client_id,
                allow_interactive=self.interactive_sign_in,
            )
            graph = GraphClient(token_provider=token_provider, request_timeout=60.0)
            self._graph_clients.append(graph)
            self.repertoire_service = RepertoireService(
                GraphExcelWorkbookGateway(graph=graph, config=repertoire),
            )
            get_logger(__name__).info("repertoire.backend", backend="cloud")
            return self.repertoire_service

        if not display_path:
            raise ConfigError("Repertoire chantier local non configure.")
        if not workbook_path.exists():
            raise ConfigError("Repertoire chantier local non configure.")
        self.repertoire_service = RepertoireService(LocalWorkbookGateway(workbook_path))
        get_logger(__name__).info("repertoire.backend", backend="local", path=display_path)
        return self.repertoire_service

    def project(self) -> ProjectService:
        return ProjectService(
            config=self.config,
            fiche_service=self.fiche(),
            repertoire_service=self.repertoire(),
            outlook=create_local_outlook_client(self.config.outlook),
            planner=self.planner(),
            pin_path=pin_to_filemanager_favorites,
            trash_path=move_path_to_trash,
        )

    def planner(self) -> ConfiguredPlannerGateway | None:
        if not self.config.planner.enabled:
            return None
        if self.planner_service is not None:
            return self.planner_service
        settings = self.application_settings or ApplicationSettings.load()
        if not settings.microsoft_client_id.strip():
            raise ConfigError(
                "Planner actif mais cette version de ProjectFlow n'embarque pas encore "
                "le connecteur Microsoft.",
            )
        token_provider = MsalAccessTokenProvider(
            client_id=settings.microsoft_client_id,
            scopes=PLANNER_GRAPH_SCOPES,
            allow_interactive=self.interactive_sign_in,
        )
        graph = GraphClient(token_provider=token_provider, request_timeout=60.0)
        self._graph_clients.append(graph)
        self.planner_service = ConfiguredPlannerGateway(
            client=GraphPlannerClient(graph=graph),
            config=self.config,
        )
        return self.planner_service

    def reset_repertoire(self) -> None:
        # Existing operations may still use the retired service. Its client
        # remains owned until shutdown, after those operations have settled.
        self.repertoire_service = None

    def reset_planner(self) -> None:
        self.planner_service = None

    async def create_planning_store(self, poste_config: PlanningPosteConfig) -> PlanningStore:
        """Store of the shared planning; its Graph client is also closed with the others."""
        return await create_planning_store(
            self.config,
            poste_config,
            application_settings=self.application_settings,
            register_client=self._graph_clients.append,
        )

    async def open_outlook_calendar(self, *, interactive: bool = False) -> OutlookCalendarGateway:
        """Calendars of the account; its Graph client is closed with the others."""
        return await open_outlook_calendar(
            interactive=interactive,
            application_settings=self.application_settings,
            register_client=self._graph_clients.append,
        )

    async def close(self) -> None:
        """Close owned clients after the controller has settled its running tasks."""
        clients, self._graph_clients = self._graph_clients, []
        results = await asyncio.gather(
            *(client.aclose() for client in clients), return_exceptions=True
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise BaseExceptionGroup("Impossible de fermer les connexions Microsoft.", errors)


@dataclass(slots=True)
class ConfiguredPlannerGateway:
    client: GraphPlannerClient
    config: AppConfig

    async def ensure_project_task(self, project: ProjectInput) -> PlannerTaskResult:
        return await self.client.ensure_project_task(project, self.config.planner)

    async def delete_project_tasks(self, project: ProjectInput) -> int:
        return await self.client.delete_project_tasks(project, self.config.planner)


def repertoire_uses_cloud(repertoire: RepertoireChantierConfig) -> bool:
    display_path = repertoire.display_path.strip()
    return (
        repertoire.is_configured
        or repertoire.cloud_only
        or display_path.casefold().startswith("https://")
        # An empty path would test the current folder, which may well be in OneDrive.
        or (bool(display_path) and is_synchronized_path(Path(display_path).expanduser()))
    )


def uses_microsoft(config: AppConfig) -> bool:
    """Tell whether the repertoire or Planner needs the Microsoft connection."""
    return config.planner.enabled or repertoire_uses_cloud(config.paths.repertoire_chantier)


async def create_planning_store(
    config: AppConfig,
    poste_config: PlanningPosteConfig,
    *,
    application_settings: ApplicationSettings | None = None,
    graph_factory: GraphFactory | None = None,
    register_client: Callable[[GraphClient], None] | None = None,
) -> PlanningStore:
    """Open the planning store this poste is set to use.

    OneDrive: the files of the
    planning folder in the OneDrive of the signed-in account, with the same connection
    (Files.ReadWrite.All) as the répertoire; the drive id found is written into
    ``poste_config``. SharePoint: the list found from
    the poste's ids, else its site address and list name, else the site of the Planner
    plan's group. The ids found are written into ``poste_config`` (the caller saves it).
    Microsoft is never asked interactively: a missing permission or connection raises
    ``StoreUnavailableError`` (caused by the 401/403 or the sign-in error, so that the
    sync state tells them apart; with no cause when Microsoft is out of reach, which
    reads « offline ») and a wrong setting ``ConfigError``, with French messages.
    """
    if poste_config.backend != "sharepoint":
        return await _open_onedrive_store(
            poste_config,
            application_settings=application_settings,
            graph_factory=graph_factory,
            register_client=register_client,
        )
    settings = application_settings or ApplicationSettings.load()
    client_id = settings.microsoft_client_id.strip()
    if not client_id:
        raise ConfigError(PLANNING_NO_CONNECTOR_MESSAGE)
    plan_id = config.planner.target_plan_id
    if not (poste_config.sharepoint_site_id or poste_config.sharepoint_site_url or plan_id):
        raise ConfigError(PLANNING_SITE_MISSING_MESSAGE)
    make_graph = graph_factory or _planning_graph_client
    provider = MsalAccessTokenProvider(
        client_id=client_id,
        scopes=PLANNING_GRAPH_SCOPES,
        allow_interactive=False,
    )
    await _check_planning_token(provider, client_id)
    graph = make_graph(provider)
    if register_client is not None:
        register_client(graph)
    try:
        list_ref = await _resolve_planning_list(
            graph,
            poste_config,
            plan_id=plan_id,
            client_id=client_id,
            graph_factory=make_graph,
        )
    except BaseException:
        await graph.aclose()
        raise
    if (poste_config.sharepoint_site_id, poste_config.sharepoint_list_id) != (
        list_ref.site_id,
        list_ref.list_id,
    ):
        poste_config.sharepoint_site_id = list_ref.site_id
        poste_config.sharepoint_list_id = list_ref.list_id
        get_logger(__name__).info("planning.sharepoint.resolved")
    return GraphPlanningBackend.for_list(graph=graph, list_ref=list_ref)


async def _open_onedrive_store(
    poste_config: PlanningPosteConfig,
    *,
    application_settings: ApplicationSettings | None,
    graph_factory: GraphFactory | None,
    register_client: Callable[[GraphClient], None] | None,
) -> DrivePlanningStore:
    """Open the OneDrive planning folder (found or created) with the répertoire consent."""
    settings = application_settings or ApplicationSettings.load()
    client_id = settings.microsoft_client_id.strip()
    if not client_id:
        raise ConfigError(PLANNING_ONEDRIVE_NO_CONNECTOR_MESSAGE)
    provider = MsalAccessTokenProvider(
        client_id=client_id,
        scopes=GRAPH_SCOPES,
        allow_interactive=False,
    )
    graph = (graph_factory or _planning_graph_client)(provider)
    if register_client is not None:
        register_client(graph)
    store = DrivePlanningStore(graph=graph, folder=poste_config.onedrive_folder)
    try:
        folder = await store.open()
    except BaseException:
        await graph.aclose()
        raise
    if folder.drive_id and folder.drive_id != poste_config.onedrive_drive_id:
        poste_config.onedrive_drive_id = folder.drive_id
        get_logger(__name__).info("planning.onedrive.resolved")
    return store


async def open_outlook_calendar(
    *,
    interactive: bool = False,
    application_settings: ApplicationSettings | None = None,
    graph_factory: GraphFactory | None = None,
    register_client: Callable[[GraphClient], None] | None = None,
) -> OutlookCalendarGateway:
    """Open the Outlook calendars of the signed-in account (Calendars.ReadWrite).

    Without ``interactive`` the browser never opens: a permission not granted yet
    raises ``AuthError`` at once. With it, Microsoft asks for the consent once.
    """
    settings = application_settings or ApplicationSettings.load()
    client_id = settings.microsoft_client_id.strip()
    if not client_id:
        raise ConfigError(OUTLOOK_NO_CONNECTOR_MESSAGE)
    provider = MsalAccessTokenProvider(
        client_id=client_id,
        scopes=OUTLOOK_GRAPH_SCOPES,
        allow_interactive=interactive,
    )
    await provider.access_token()
    graph = (graph_factory or _planning_graph_client)(provider)
    if register_client is not None:
        register_client(graph)
    return OutlookCalendarGateway(graph)


def planning_authorization_error(
    message: str = PLANNING_AUTHORIZATION_MESSAGE,
) -> StoreUnavailableError:
    """« Autorisation manquante », caused by a 403 so that the sync state reads « forbidden »."""
    denied = GraphError(message, status_code=HTTP_FORBIDDEN)
    error = StoreUnavailableError(message)
    error.__cause__ = denied
    return error


def _planning_graph_client(token_provider: AccessTokenProvider) -> GraphClient:
    return GraphClient(
        token_provider=token_provider,
        request_timeout=PLANNING_REQUEST_TIMEOUT_SECONDS,
    )


async def _check_planning_token(provider: MsalAccessTokenProvider, client_id: str) -> None:
    """Fail at once, without any request, when the SharePoint permission is not granted."""
    try:
        await provider.access_token()
    except AuthError as exc:
        failure = await _planning_token_failure(exc, client_id)
    except OSError:
        # MSAL reads the authority before its first token, outside its own error
        # handling: offline, that request fails with the bare network error.
        failure = StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    else:
        return
    # Raised outside the ``except``: an offline failure must not hang on the AuthError,
    # which the sync state would read as « reconnexion requise ».
    raise failure


async def _planning_token_failure(error: AuthError, client_id: str) -> StoreUnavailableError:
    """Why the Sites token was refused: offline, signed out, or the consent pending."""
    if _network_failure(error):
        return StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    base = MsalAccessTokenProvider(client_id=client_id, allow_interactive=False)
    try:
        await base.access_token()
    except OSError:
        return StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    except AuthError as base_error:
        if _network_failure(base_error):
            return StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
        # Not signed in at all: the connection, not the permission, is missing.
        return _planning_auth_failure(error)
    # Signed in, yet no token for Sites.ReadWrite.All: the consent is pending.
    return planning_authorization_error()


def _planning_auth_failure(error: AuthError) -> StoreUnavailableError:
    """« Hors ligne » when Microsoft did not answer, else « reconnexion requise »."""
    if _network_failure(error):
        return StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    failure = StoreUnavailableError(PLANNING_SIGN_IN_MESSAGE)
    failure.__cause__ = error
    return failure


def _network_failure(error: BaseException) -> bool:
    """Tell whether the sign-in failed because Microsoft could not be reached (no network)."""
    seen: set[int] = set()
    current: BaseException | None = error.__cause__
    while current is not None and id(current) not in seen:
        if isinstance(current, OSError):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


async def _resolve_planning_list(
    graph: GraphClient,
    poste_config: PlanningPosteConfig,
    *,
    plan_id: str,
    client_id: str,
    graph_factory: GraphFactory,
) -> PlanningListRef:
    try:
        site_id = poste_config.sharepoint_site_id
        if not site_id and poste_config.sharepoint_site_url:
            site_id = await site_id_from_url(graph, poste_config.sharepoint_site_url)
        elif not site_id:
            group_id = await _plan_group_id(plan_id, client_id=client_id, factory=graph_factory)
            site_id = await group_site_id(graph, group_id)
        list_id = poste_config.sharepoint_list_id
        if not list_id:
            list_name = poste_config.sharepoint_list_name
            list_id = await find_list(graph, site_id, list_name) or ""
            if not list_id:
                raise ConfigError(
                    f"Planning SharePoint : la liste « {list_name} » est introuvable sur le "
                    "site du planning. Un propriétaire du site doit la créer, ou corrigez son "
                    f"nom dans {PLANNING_SETTINGS_PLACE}.",
                )
            problems = await planning_column_problems(graph, site_id, list_id)
            if problems:
                raise ConfigError(f"Planning SharePoint : {' '.join(problems)}")
    except AuthError as exc:
        failure = _planning_auth_failure(exc)
    except GraphError as exc:
        raise _planning_graph_failure(exc) from exc
    except OSError:
        # The Planner connection's first token, as in ``_check_planning_token``.
        failure = StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    else:
        return PlanningListRef(site_id=site_id, list_id=list_id)
    raise failure  # Outside the ``except``, as in ``_check_planning_token``.


async def _plan_group_id(plan_id: str, *, client_id: str, factory: GraphFactory) -> str:
    """Microsoft 365 group of the Planner plan, read with the Planner permission."""
    graph = factory(
        MsalAccessTokenProvider(
            client_id=client_id,
            scopes=PLANNER_GRAPH_SCOPES,
            allow_interactive=False,
        ),
    )
    try:
        payload = await graph.get(
            f"/planner/plans/{quote(plan_id, safe='')}?$select=id,container,owner",
        )
    finally:
        await graph.aclose()
    group_id = _plan_container_group(payload)
    if not group_id:
        raise ConfigError(
            "Planning SharePoint : impossible de trouver le groupe Microsoft 365 du plan "
            f"Planner. Indiquez l'adresse du site SharePoint dans {PLANNING_SETTINGS_PLACE}.",
        )
    return group_id


def _plan_container_group(payload: dict[str, Any]) -> str:
    container = payload.get("container")
    if isinstance(container, dict):
        kind = str(container.get("type") or "").casefold()
        container_id = container.get("containerId")
        if isinstance(container_id, str) and container_id and kind in {"", "group"}:
            return container_id
    owner = payload.get("owner")
    return owner if isinstance(owner, str) else ""


def _planning_graph_failure(exc: GraphError) -> StoreUnavailableError | ConfigError:
    status = exc.status_code
    if status == HTTP_FORBIDDEN:
        return StoreUnavailableError(PLANNING_FORBIDDEN_MESSAGE)
    if status == HTTP_UNAUTHORIZED:
        return StoreUnavailableError(PLANNING_SIGN_IN_MESSAGE)
    if status == HTTP_NOT_FOUND:
        return ConfigError(PLANNING_SITE_NOT_FOUND_MESSAGE)
    if (
        status is None
        or status in {HTTP_LOCKED, HTTP_TOO_MANY_REQUESTS}
        or status >= HTTP_SERVER_ERROR
    ):
        return StoreUnavailableError(PLANNING_OFFLINE_MESSAGE)
    return ConfigError(f"Planning SharePoint inaccessible : {exc}")


async def resolve_repertoire_open_url(config: RepertoireChantierConfig) -> str:
    settings = ApplicationSettings.load()
    graph = GraphClient(
        token_provider=MsalAccessTokenProvider(
            client_id=settings.microsoft_client_id,
            allow_interactive=False,
        ),
        request_timeout=60.0,
    )
    try:
        return await resolve_workbook_open_url(graph, config)
    finally:
        await graph.aclose()
