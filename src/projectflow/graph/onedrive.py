from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from projectflow.exceptions import ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.platform.sync_paths import (
    is_excel_recovery_copy,
    is_synchronized_path,
    onedrive_roots,
)

HTTP_NOT_FOUND = 404
MAX_SEARCH_PAGES = 20
LINK_GUIDANCE = (
    "Dans OneDrive ou SharePoint, copiez le lien de partage du classeur d'origine "
    "et collez-le dans Parametres > Repertoire chantier."
)


@dataclass(frozen=True, slots=True)
class DriveItemTarget:
    drive_id: str
    item_id: str
    web_url: str = ""


class OneDriveItemResolver:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def resolve_workbook(self, local_path: Path | str) -> DriveItemTarget:
        raw_path = str(local_path).strip()
        if "://" in raw_path:
            return await self._resolve_by_link(raw_path)
        local_path = Path(raw_path)
        _assert_not_conflict_copy(local_path)
        relative_path = _relative_onedrive_path(local_path)
        if relative_path:
            target = await self._resolve_by_path(relative_path)
            if target is not None:
                return target
            target = await self._resolve_by_root_shortcut(relative_path)
            if target is not None:
                return target
            raise ConfigError(
                "Le chemin du repertoire n'existe pas dans le compte Microsoft connecte. "
                + LINK_GUIDANCE,
            )
        if is_synchronized_path(local_path):
            raise ConfigError(
                "Le repertoire synchronise ne peut pas etre relie avec certitude au cloud. "
                + LINK_GUIDANCE,
            )
        return await self._resolve_by_search(local_path)

    async def _resolve_by_link(self, sharing_url: str) -> DriveItemTarget:
        parsed = urlsplit(sharing_url)
        hostname = (parsed.hostname or "").casefold()
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (
                hostname in {"1drv.ms", "onedrive.live.com"} or hostname.endswith(".sharepoint.com")
            )
        ):
            raise ConfigError(
                "Le lien doit etre un lien HTTPS OneDrive ou SharePoint. " + LINK_GUIDANCE,
            )
        _assert_not_conflict_copy(unquote(parsed.path).rsplit("/", 1)[-1])
        sharing_token = "u!" + base64.urlsafe_b64encode(sharing_url.encode()).decode().rstrip("=")
        payload = await self._graph.get(
            f"/shares/{sharing_token}/driveItem"
            "?$select=id,name,webUrl,parentReference,remoteItem,folder",
            headers={"Prefer": "redeemSharingLink"},
        )
        return _target_from_payload(payload)

    async def _resolve_by_path(self, relative_path: str) -> DriveItemTarget | None:
        encoded_path = quote(relative_path, safe="/")
        try:
            payload = await self._graph.get(
                f"/me/drive/root:/{encoded_path}:"
                "?$select=id,name,webUrl,parentReference,remoteItem,folder",
            )
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                return None
            raise
        return _target_from_payload(payload)

    async def _resolve_by_root_shortcut(self, relative_path: str) -> DriveItemTarget | None:
        first_segment, remaining_path = _split_first_segment(relative_path)
        if not first_segment or not remaining_path:
            return None

        shortcut_payload = await self._resolve_optional_drive_item(
            f"/me/drive/root:/{quote(first_segment, safe='')}:"
            "?$select=id,name,webUrl,parentReference,remoteItem,size",
        )
        if shortcut_payload is None:
            return None
        shortcut_target = _remote_item_target(shortcut_payload)
        if shortcut_target is None:
            return None

        encoded_remaining_path = quote(remaining_path, safe="/")
        payload = await self._resolve_optional_drive_item(
            f"/drives/{quote(shortcut_target.drive_id, safe='')}"
            f"/items/{quote(shortcut_target.item_id, safe='')}:/{encoded_remaining_path}:"
            "?$select=id,name,webUrl,parentReference,remoteItem,folder",
        )
        if payload is None:
            return None
        return _target_from_payload(payload)

    async def _resolve_optional_drive_item(self, path: str) -> dict[str, Any] | None:
        try:
            return await self._graph.get(path)
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                return None
            raise

    async def _resolve_by_search(self, local_path: Path) -> DriveItemTarget:
        search_term = quote(_odata_string(local_path.name), safe="")
        next_path = (
            f"/me/drive/root/search(q='{search_term}')"
            "?$select=id,name,webUrl,parentReference,remoteItem,folder"
        )
        visited: set[str] = set()
        candidates: dict[tuple[str, str], DriveItemTarget] = {}
        while next_path:
            if next_path in visited or len(visited) >= MAX_SEARCH_PAGES:
                raise ConfigError(
                    "La recherche du repertoire cloud est incomplete. " + LINK_GUIDANCE,
                )
            visited.add(next_path)
            payload = await self._graph.get(next_path)
            for item in _payload_items(payload):
                if str(item.get("name", "")).casefold() != local_path.name.casefold():
                    continue
                if isinstance(item.get("folder"), dict):
                    continue
                target = _target_from_payload(item)
                candidates[target.drive_id, target.item_id] = target
            if len(candidates) > 1:
                break
            next_path = _search_next_path(payload)
        if len(candidates) != 1:
            count = len(candidates)
            raise ConfigError(
                "Impossible d'identifier de maniere unique le repertoire chantier dans OneDrive "
                f"({count} candidat(s) trouve(s)). " + LINK_GUIDANCE,
            )
        return next(iter(candidates.values()))


def _relative_onedrive_path(path: Path) -> str:
    resolved_path = path.expanduser().resolve()
    roots = {root.expanduser().resolve() for root in onedrive_roots()}
    if len(roots) > 1:
        raise ConfigError(
            "Plusieurs comptes OneDrive sont synchronises sur ce poste. "
            "Le chemin local ne permet pas d'identifier le compte du repertoire. " + LINK_GUIDANCE,
        )
    for root in roots:
        try:
            return resolved_path.relative_to(root).as_posix()
        except ValueError:
            continue
    return ""


def _assert_not_conflict_copy(name: str | Path) -> None:
    if is_excel_recovery_copy(Path(name)):
        raise ConfigError(
            "La copie non fusionnee ne peut pas servir de repertoire. " + LINK_GUIDANCE,
        )


def _search_next_path(payload: dict[str, Any]) -> str:
    next_link = payload.get("@odata.nextLink")
    if next_link is None:
        return ""
    if not isinstance(next_link, str) or not next_link:
        raise ConfigError("La recherche du repertoire cloud est incomplete. " + LINK_GUIDANCE)
    parsed = urlsplit(next_link)
    # GraphClient attaches a bearer token: never follow pagination to another origin.
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "graph.microsoft.com"
        or not parsed.path.startswith("/v1.0/")
        or parsed.fragment
    ):
        raise ConfigError("Lien de pagination Microsoft Graph invalide. " + LINK_GUIDANCE)
    return next_link


def _odata_string(value: str) -> str:
    return value.replace("'", "''")


def _split_first_segment(path: str) -> tuple[str, str]:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def _payload_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("value")
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _target_from_payload(payload: dict[str, Any]) -> DriveItemTarget:
    _assert_not_conflict_copy(str(payload.get("name", "")))
    if isinstance(payload.get("folder"), dict):
        raise ConfigError("Selectionnez le classeur Excel, pas son dossier. " + LINK_GUIDANCE)
    remote_item = payload.get("remoteItem")
    if isinstance(remote_item, dict):
        return _target_from_payload(remote_item)
    item_id = payload.get("id")
    parent_reference = payload.get("parentReference")
    drive_id = parent_reference.get("driveId") if isinstance(parent_reference, dict) else None
    if not isinstance(item_id, str) or not item_id or not isinstance(drive_id, str) or not drive_id:
        raise ConfigError("Reponse OneDrive incomplete pour le repertoire chantier.")
    web_url = payload.get("webUrl")
    return DriveItemTarget(
        drive_id=drive_id,
        item_id=item_id,
        web_url=web_url if isinstance(web_url, str) else "",
    )


def _remote_item_target(payload: dict[str, Any]) -> DriveItemTarget | None:
    remote_item = payload.get("remoteItem")
    if not isinstance(remote_item, dict):
        return None
    item_id = remote_item.get("id")
    parent_reference = remote_item.get("parentReference")
    drive_id = parent_reference.get("driveId") if isinstance(parent_reference, dict) else None
    if not isinstance(item_id, str) or not item_id or not isinstance(drive_id, str) or not drive_id:
        return None
    web_url = remote_item.get("webUrl")
    return DriveItemTarget(
        drive_id=drive_id,
        item_id=item_id,
        web_url=web_url if isinstance(web_url, str) else "",
    )
