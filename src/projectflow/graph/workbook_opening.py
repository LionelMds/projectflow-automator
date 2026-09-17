from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from projectflow.config import RepertoireChantierConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.onedrive import OneDriveItemResolver
from projectflow.platform.sync_paths import is_excel_recovery_copy

OPENING_GUIDANCE = (
    "Selectionnez le fichier OneDrive synchronise dans Parametres > "
    "Fichier synchronise pour ouverture Excel."
)


def excel_document_uri(url: str) -> str:
    """Address the original cloud document through the registered Excel application."""
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("Lien direct du classeur invalide. " + OPENING_GUIDANCE) from exc
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or any(character in url for character in "|\r\n")
        or not (
            hostname.endswith(".sharepoint.com")
            or hostname in {"onedrive.live.com", "d.docs.live.net"}
        )
        or PurePosixPath(unquote(parsed.path)).suffix.casefold() != ".xlsx"
    ):
        raise ConfigError("Lien direct du classeur indisponible. " + OPENING_GUIDANCE)
    return "ms-excel:ofe|u|" + url


async def resolve_workbook_open_url(
    graph: GraphClient,
    config: RepertoireChantierConfig,
) -> str:
    """Read file metadata only: never start an Excel session or write a workbook."""
    drive_id, item_id = config.drive_id, config.item_id
    if not config.is_configured:
        target = await OneDriveItemResolver(graph).resolve_workbook(config.display_path)
        drive_id, item_id = target.drive_id, target.item_id
    item_path = f"/drives/{quote(drive_id, safe='')}/items/{quote(item_id, safe='')}"
    item = await graph.get(item_path + "?$select=name,webDavUrl,webUrl,parentReference,folder")
    name = item.get("name")
    if not isinstance(name, str) or not name or isinstance(item.get("folder"), dict):
        raise ConfigError("Le repertoire doit designer un classeur Excel. " + OPENING_GUIDANCE)
    if is_excel_recovery_copy(Path(name)):
        raise ConfigError("La copie non fusionnee ne peut pas servir de repertoire.")
    for key in ("webDavUrl", "webUrl"):
        candidate = item.get(key)
        if isinstance(candidate, str) and _is_document_url(candidate):
            return candidate

    # Browser links such as Doc.aspx cannot be passed to desktop Excel. Obtain
    # the actual containing folder instead of guessing from an opaque share link.
    parent = item.get("parentReference")
    parent_id = parent.get("id") if isinstance(parent, dict) else None
    if isinstance(parent_id, str) and parent_id:
        folder = await graph.get(
            f"/drives/{quote(drive_id, safe='')}/items/{quote(parent_id, safe='')}?$select=webUrl",
        )
        folder_url = folder.get("webUrl")
        if isinstance(folder_url, str):
            parsed = urlsplit(folder_url)
            if (
                not parsed.query
                and not parsed.fragment
                and not unquote(parsed.path).casefold().rstrip("/").endswith(".aspx")
            ):
                candidate = urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path.rstrip("/") + "/" + quote(name, safe=""),
                        "",
                        "",
                    ),
                )
                if _is_document_url(candidate):
                    return candidate
    raise ConfigError("Lien direct du classeur indisponible. " + OPENING_GUIDANCE)


def _is_document_url(url: str) -> bool:
    try:
        excel_document_uri(url)
    except (ConfigError, ValueError):
        return False
    return True
