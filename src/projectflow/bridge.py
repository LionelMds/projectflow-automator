"""Headless requests sent by MailFlow Archivist.

MailFlow sorts Outlook mails into project folders. When a mail quotes a project whose
Outlook folder does not exist yet, MailFlow asks ProjectFlow to prepare it. ProjectFlow
stays the only place that names and creates project folders: the project must already
exist in the repertoire chantier, and the folder follows the Outlook arborescence of the
ProjectFlow settings. Nothing is created for an unknown number.

Request and result are JSON files because the packaged application has no console. The
command never opens a window nor a Microsoft sign-in page: an expired session is
reported so that the user reconnects from ProjectFlow.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from projectflow import __version__
from projectflow.config import AppConfig
from projectflow.core.duplication import project_info_columns_empty
from projectflow.core.models import ProjectInput
from projectflow.core.numero import is_main_project_number, parse_project_number
from projectflow.core.project_service import outlook_folder_paths
from projectflow.exceptions import ProjectCreationError, ProjectFlowError
from projectflow.logging import configure_logging, get_logger

PROTOCOL_VERSION = 1
REQUEST_ARGUMENT = "--mailflow-request"
RESULT_ARGUMENT = "--mailflow-result"
ENSURE_OUTLOOK_FOLDERS = "ensure_outlook_folders"
MAX_NUMBERS = 50
MAX_REQUEST_BYTES = 64 * 1024


class BridgeRequestError(ProjectFlowError):
    """Raised when a MailFlow request cannot be served; the message is shown to the user."""


@dataclass(frozen=True, slots=True)
class RepertoireProject:
    number: str
    societe: str
    contact: str
    designation: str


class RepertoireReader(Protocol):
    async def read_snapshot(self, *, year: int) -> Any:  # noqa: ANN401 - RepertoireSnapshot
        """Return the rows of one year of the repertoire chantier."""


class OutlookFolders(Protocol):
    def validate_target_sync(self) -> None:
        """Check that the configured Outlook store is available."""

    def ensure_folder_path_sync(self, names: list[str]) -> object:
        """Create the nested folder path when it is missing."""


@dataclass(frozen=True, slots=True)
class BridgeDependencies:
    load_config: Callable[[], AppConfig]
    repertoire: Callable[[AppConfig], tuple[RepertoireReader, Callable[[], Any]]]
    outlook: Callable[[AppConfig], OutlookFolders]


def is_bridge_request(argv: Sequence[str]) -> bool:
    return REQUEST_ARGUMENT in argv


def run_bridge(argv: Sequence[str], dependencies: BridgeDependencies | None = None) -> int:
    configure_logging()
    logger = get_logger("projectflow.bridge")
    try:
        request_path, result_path = _argument_paths(argv)
    except BridgeRequestError:
        logger.warning("bridge.arguments.invalid")
        return 2
    try:
        result = handle_request(
            _read_request(request_path),
            dependencies or _default_dependencies(),
        )
    except ProjectFlowError as exc:
        logger.warning("bridge.request.failed", error=type(exc).__name__)
        result = _failure(str(exc))
    except Exception as exc:  # MailFlow must always receive an answer
        logger.exception("bridge.request.crashed", error=type(exc).__name__)
        result = _failure("Erreur inattendue dans ProjectFlow: consultez son journal.")
    _write_result(result_path, result)
    logger.info(
        "bridge.request.done",
        ok=result["ok"],
        statuses=[project["status"] for project in result.get("projects", [])],
    )
    return 0 if result["ok"] else 1


def handle_request(request: dict[str, Any], dependencies: BridgeDependencies) -> dict[str, Any]:
    numbers = requested_numbers(request)
    config = dependencies.load_config()
    if not config.outlook.enabled:
        raise BridgeRequestError(
            "La creation Outlook est desactivee dans les parametres de ProjectFlow.",
        )
    outlook = dependencies.outlook(config)
    outlook.validate_target_sync()
    repertoire, close = dependencies.repertoire(config)
    projects, unknown = asyncio.run(_read_projects(repertoire, numbers, close))
    results = [
        _ensure_project_folder(number, projects, unknown, config, outlook) for number in numbers
    ]
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": True,
        "error": None,
        "projectflow_version": __version__,
        "outlook": {
            "mailbox": config.outlook.target_mailbox,
            "base_folder": config.outlook.target_base_folder,
        },
        "projects": results,
    }


def requested_numbers(request: dict[str, Any]) -> list[str]:
    if request.get("protocol") != PROTOCOL_VERSION:
        raise BridgeRequestError(
            "Demande MailFlow dans un format inconnu: mettez a jour MailFlow et ProjectFlow.",
        )
    if request.get("action") != ENSURE_OUTLOOK_FOLDERS:
        raise BridgeRequestError("Action MailFlow inconnue pour ProjectFlow.")
    raw_numbers = request.get("numbers")
    if not isinstance(raw_numbers, list) or not raw_numbers:
        raise BridgeRequestError("Aucun numero de projet dans la demande MailFlow.")
    if len(raw_numbers) > MAX_NUMBERS:
        raise BridgeRequestError(
            f"Trop de projets dans une seule demande (maximum {MAX_NUMBERS}).",
        )
    numbers: dict[str, None] = {}
    for value in raw_numbers:
        if not isinstance(value, str) or not is_main_project_number(value):
            raise BridgeRequestError("Numero de projet invalide dans la demande MailFlow.")
        numbers.setdefault(str(parse_project_number(value)), None)
    return list(numbers)


async def _read_projects(
    repertoire: RepertoireReader,
    numbers: Sequence[str],
    close: Callable[[], Any],
) -> tuple[dict[str, RepertoireProject], dict[str, str]]:
    projects: dict[str, RepertoireProject] = {}
    unknown: dict[str, str] = {}
    try:
        for year in sorted({parse_project_number(number).year for number in numbers}):
            wanted = {number for number in numbers if parse_project_number(number).year == year}
            try:
                snapshot = await repertoire.read_snapshot(year=year)
            except ProjectCreationError:
                for number in wanted:
                    unknown[number] = f"Onglet {year} absent du repertoire chantier."
                continue
            for row in snapshot.rows:
                values = list(row.values)
                if row.number in wanted and not project_info_columns_empty(values):
                    projects[row.number] = RepertoireProject(
                        number=row.number,
                        societe=_text(values, 2),
                        contact=_text(values, 3),
                        designation=_text(values, 4),
                    )
    finally:
        closing = close()
        if asyncio.iscoroutine(closing):
            await closing
    return projects, unknown


def _ensure_project_folder(
    number: str,
    projects: dict[str, RepertoireProject],
    unknown: dict[str, str],
    config: AppConfig,
    outlook: OutlookFolders,
) -> dict[str, Any]:
    project = projects.get(number)
    if project is None:
        return {
            "number": number,
            "status": "unknown",
            "message": unknown.get(
                number,
                "Projet absent du repertoire chantier: creez-le d'abord dans ProjectFlow.",
            ),
        }
    project_input = ProjectInput(
        number=parse_project_number(number),
        designation=project.designation,
        societe=project.societe,
        contact=project.contact,
    )
    folder_paths = outlook_folder_paths(project_input, config.outlook.arborescence)
    try:
        for folder_path in folder_paths:
            outlook.ensure_folder_path_sync(folder_path)
    except ProjectFlowError as exc:
        return {"number": number, "status": "error", "message": str(exc)}
    return {
        "number": number,
        "status": "ok",
        "designation": project.designation,
        "societe": project.societe,
        "folder_paths": folder_paths,
        "message": "",
    }


def _argument_paths(argv: Sequence[str]) -> tuple[Path, Path]:
    values: dict[str, str] = {}
    for name in (REQUEST_ARGUMENT, RESULT_ARGUMENT):
        if name not in argv or argv.index(name) + 1 >= len(argv):
            raise BridgeRequestError(f"Argument manquant: {name}")
        values[name] = argv[argv.index(name) + 1]
    return Path(values[REQUEST_ARGUMENT]), Path(values[RESULT_ARGUMENT])


def _read_request(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_REQUEST_BYTES:
            raise BridgeRequestError("Demande MailFlow trop volumineuse.")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeRequestError("Demande MailFlow illisible.") from exc
    if not isinstance(data, dict):
        raise BridgeRequestError("Demande MailFlow illisible.")
    return data


def _write_result(path: Path, result: dict[str, Any]) -> None:
    content = json.dumps(result, ensure_ascii=False, indent=2)
    # MailFlow reads the file once the process ends: never leave half a document.
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temp_path = Path(stream.name)
        stream.write(content)
    try:
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def _failure(message: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "ok": False,
        "error": message,
        "projectflow_version": __version__,
        "projects": [],
    }


def _text(values: list[Any], column: int) -> str:
    if len(values) <= column or values[column] is None:
        return ""
    return str(values[column]).strip()


def _default_dependencies() -> BridgeDependencies:
    return BridgeDependencies(
        load_config=AppConfig.load,
        repertoire=_configured_repertoire,
        outlook=_configured_outlook,
    )


def _configured_repertoire(
    config: AppConfig,
) -> tuple[RepertoireReader, Callable[[], Any]]:
    from projectflow.services import ServiceContainer  # noqa: PLC0415 - heavy imports

    # A request from MailFlow must never open a browser: reuse the saved session only.
    services = ServiceContainer(config, interactive_sign_in=False)
    return services.repertoire(), services.close


def _configured_outlook(config: AppConfig) -> OutlookFolders:
    if not sys.platform.startswith("win"):
        raise BridgeRequestError("Les demandes MailFlow sont disponibles sous Windows.")
    from projectflow.outlook.windows import WindowsLocalOutlookClient  # noqa: PLC0415

    if not config.outlook.target_store_id and not config.outlook.target_mailbox:
        raise BridgeRequestError("Selectionnez un compte Outlook dans les parametres.")
    return WindowsLocalOutlookClient(
        target_store_id=config.outlook.target_store_id,
        target_mailbox=config.outlook.target_mailbox,
        base_folder=config.outlook.target_base_folder,
    )
