"""Links that open a project folder on the poste where they are clicked.

A fiche synchronised by OneDrive is opened by Excel under its web address, so a path
written in a cell, even a relative one, leads to the browser. ``projectflow://dossier/
2024-4493`` names the dossier only: ProjectFlow receives the link and opens the folder
under the projects root configured on that poste, whatever its drive or user name.

Anything can send such a link (a web page, another workbook). It carries a project
number and nothing else: the folder is built from that number, never from a path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

from projectflow.core.numero import ProjectNumber, parse_project_number, project_folder_name
from projectflow.exceptions import ProjectFlowError

LINK_SCHEME: Final[str] = "projectflow"
_DOSSIER: Final[str] = "dossier"


class DossierLinkError(ProjectFlowError):
    """Raised when a link cannot be followed; the message is shown to the user."""


def dossier_link(number: ProjectNumber) -> str:
    """Return the link that opens the folder of ``number`` on any poste."""
    return f"{LINK_SCHEME}://{_DOSSIER}/{number}"


def is_dossier_link(text: str) -> bool:
    """Tell a ProjectFlow link from the other arguments the application receives."""
    return text.strip().lower().startswith(f"{LINK_SCHEME}:")


def find_dossier_link(arguments: list[str]) -> str | None:
    """Return the first ProjectFlow link among command-line arguments."""
    return next((argument for argument in arguments if is_dossier_link(argument)), None)


def parse_dossier_link(link: str) -> ProjectNumber:
    """Return the dossier a link names.

    Both ``projectflow://dossier/2024-4493`` and ``projectflow:dossier/2024-4493`` are
    read, with or without a trailing slash: programs rewrite links slightly.
    """
    parts = urlsplit(link.strip())
    if parts.scheme.lower() != LINK_SCHEME:
        raise DossierLinkError("Ce lien n'est pas un lien ProjectFlow.")
    segments = [unquote(segment) for segment in f"{parts.netloc}/{parts.path}".split("/")]
    segments = [segment for segment in segments if segment]
    unknown = parts.query or parts.fragment or len(segments) != 2  # noqa: PLR2004 - kind, number
    if unknown or segments[0].lower() != _DOSSIER:
        raise DossierLinkError("Lien ProjectFlow inconnu : mettez ProjectFlow a jour.")
    try:
        return parse_project_number(segments[1])
    except ProjectFlowError as exc:
        raise DossierLinkError("Le lien ProjectFlow ne designe pas un numero de dossier.") from exc


def dossier_dir(root: Path | None, number: ProjectNumber) -> Path:
    """Return the existing folder of ``number`` under the projects root of this poste.

    A sub-project that has its own folder inside the project opens there.
    """
    if root is None:
        raise DossierLinkError(
            "La racine projets n'est pas configuree sur ce poste : ouvrez ProjectFlow "
            "et renseignez-la dans les parametres.",
        )
    project_dir = root / str(number.year) / project_folder_name(number)
    if not project_dir.is_dir():
        raise DossierLinkError(f"Dossier {project_folder_name(number)} introuvable : {project_dir}")
    nested_dir = project_dir / str(number)
    if number.is_subproject and nested_dir.is_dir():
        return nested_dir
    return project_dir
