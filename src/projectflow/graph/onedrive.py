from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from projectflow.graph.client import JSON, GraphClient


@dataclass(frozen=True, slots=True)
class DriveItem:
    id: str
    name: str
    web_url: str = ""


class OneDriveClient:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def locate_me_item_by_path(self, path: str) -> DriveItem:
        encoded_path = quote(path.strip("/"), safe="/")
        payload = await self._graph.get(f"/me/drive/root:/{encoded_path}")
        return _drive_item(payload)

    async def get_item(self, *, drive_id: str, item_id: str) -> DriveItem:
        payload = await self._graph.get(f"/drives/{drive_id}/items/{item_id}")
        return _drive_item(payload)


def _drive_item(payload: JSON) -> DriveItem:
    item_id = payload.get("id")
    name = payload.get("name")
    web_url = payload.get("webUrl", "")
    if not isinstance(item_id, str) or not isinstance(name, str):
        raise ValueError("Reponse OneDrive invalide.")
    return DriveItem(id=item_id, name=name, web_url=web_url if isinstance(web_url, str) else "")
