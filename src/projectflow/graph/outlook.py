from __future__ import annotations

from dataclasses import dataclass

from projectflow.graph.client import JSON, GraphClient


@dataclass(frozen=True, slots=True)
class MailFolder:
    id: str
    display_name: str


class OutlookClient:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def ensure_folder(self, display_name: str, *, parent_id: str | None = None) -> MailFolder:
        existing = await self._find_folder(display_name, parent_id=parent_id)
        if existing is not None:
            return existing

        path = (
            "/me/mailFolders"
            if parent_id is None
            else f"/me/mailFolders/{parent_id}/childFolders"
        )
        payload = await self._graph.post(path, json={"displayName": display_name})
        return _mail_folder(payload)

    async def ensure_folder_path(self, names: list[str]) -> MailFolder:
        if not names:
            raise ValueError("La liste de dossiers Outlook ne peut pas etre vide.")

        parent_id: str | None = None
        folder: MailFolder | None = None
        for name in names:
            folder = await self.ensure_folder(name, parent_id=parent_id)
            parent_id = folder.id
        if folder is None:
            raise ValueError("La liste de dossiers Outlook ne peut pas etre vide.")
        return folder

    async def _find_folder(self, display_name: str, *, parent_id: str | None) -> MailFolder | None:
        path = (
            "/me/mailFolders"
            if parent_id is None
            else f"/me/mailFolders/{parent_id}/childFolders"
        )
        folders = await self._graph.get_all_pages(path, params={"$select": "id,displayName"})
        for folder in folders:
            if folder.get("displayName") == display_name:
                return _mail_folder(folder)
        return None


def _mail_folder(payload: JSON) -> MailFolder:
    folder_id = payload.get("id")
    display_name = payload.get("displayName")
    if not isinstance(folder_id, str) or not isinstance(display_name, str):
        raise ValueError("Reponse Outlook invalide.")
    return MailFolder(id=folder_id, display_name=display_name)
