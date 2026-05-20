from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from projectflow.exceptions import ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.platform.paths import detect_onedrive_balz_root

HTTP_NOT_FOUND = 404


@dataclass(frozen=True, slots=True)
class DriveItemTarget:
    drive_id: str
    item_id: str
    web_url: str = ""


class OneDriveItemResolver:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def resolve_workbook(self, local_path: Path) -> DriveItemTarget:
        relative_path = _relative_onedrive_path(local_path)
        if relative_path:
            target = await self._resolve_by_path(relative_path)
            if target is not None:
                return target
            target = await self._resolve_by_root_shortcut(relative_path)
            if target is not None:
                return target
        return await self._resolve_by_search(local_path)

    async def _resolve_by_path(self, relative_path: str) -> DriveItemTarget | None:
        encoded_path = quote(relative_path, safe="/")
        try:
            payload = await self._graph.get(
                f"/me/drive/root:/{encoded_path}:"
                "?$select=id,name,webUrl,parentReference,size",
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
            "?$select=id,name,webUrl,parentReference,size",
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
        payload = await self._graph.get(
            f"/me/drive/root/search(q='{_odata_string(local_path.name)}')"
            "?$select=id,name,webUrl,parentReference,size",
        )
        candidates = [
            item
            for item in _payload_items(payload)
            if str(item.get("name", "")).casefold() == local_path.name.casefold()
        ]
        local_size = await asyncio.to_thread(_local_file_size, local_path)
        if local_size is not None:
            sized_candidates = [
                item for item in candidates if _item_size(item) in {None, local_size}
            ]
            if sized_candidates:
                candidates = sized_candidates
        if len(candidates) != 1:
            count = len(candidates)
            raise ConfigError(
                "Impossible d'identifier de maniere unique le repertoire chantier dans OneDrive "
                f"({count} candidat(s) trouve(s)). Placez le fichier dans un dossier OneDrive "
                "Balz synchronise, ouvrez le fichier depuis le bouton 'Parcourir', ou renommez "
                "les doublons avant de reconnecter le repertoire.",
            )
        return _target_from_payload(candidates[0])


def _relative_onedrive_path(path: Path) -> str:
    root = detect_onedrive_balz_root()
    if root is None:
        return ""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


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


def _item_size(item: dict[str, Any]) -> int | None:
    size = item.get("size")
    return size if isinstance(size, int) else None


def _local_file_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _target_from_payload(payload: dict[str, Any]) -> DriveItemTarget:
    item_id = payload.get("id")
    parent_reference = payload.get("parentReference")
    drive_id = parent_reference.get("driveId") if isinstance(parent_reference, dict) else None
    if not isinstance(item_id, str) or not isinstance(drive_id, str):
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
    if not isinstance(item_id, str) or not isinstance(drive_id, str):
        return None
    web_url = remote_item.get("webUrl")
    return DriveItemTarget(
        drive_id=drive_id,
        item_id=item_id,
        web_url=web_url if isinstance(web_url, str) else "",
    )
