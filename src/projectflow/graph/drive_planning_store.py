"""Shared planning stored as JSON files in a folder of the signed-in account's OneDrive.

Only the delegated permission Files.ReadWrite.All of the répertoire is used: no
SharePoint permission, no administrator consent. Each planning document is one file
``<kind>__<doc_id>.json`` of the folder (« ProjectFlow Planning » at the root of the
drive by default); any other file of the folder (a user's file, a conflict copy, a
non-JSON file) is ignored.

Changes are read with the delta API of the whole drive (``/me/drive/root/delta``): the
cursor is its delta link and only the items of the planning folder are kept. A first
read lists the folder after taking a ``token=latest`` link, so a change made during the
listing comes back with the next delta. Writes are conditional: a creation fails when the
file exists (conflictBehavior=fail → 409), an update or a deletion when the eTag moved
(If-Match → 412); both reach the service as ``StoreConflictError``.

Delta reports a deleted item by its id, sometimes without its name. The store learns
which item holds which document from every read and write; after a restart with a saved
cursor, a deletion it cannot place costs one full read of the folder.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeVar
from urllib.parse import quote

from projectflow.core.planning.store import (
    StoreChanges,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.exceptions import AuthError, ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.logging import get_logger

DEFAULT_ONEDRIVE_FOLDER: Final = "ProjectFlow Planning"
DOCUMENT_KINDS: Final = ("task", "resource", "absence", "meta")
FILE_SUFFIX: Final = ".json"
KIND_SEPARATOR: Final = "__"
DOWNLOAD_CONCURRENCY: Final = 4
CHILDREN_PAGE_SIZE: Final = 200
DOWNLOAD_URL_KEY: Final = "@microsoft.graph.downloadUrl"
DRIVE: Final = "/me/drive"
LATEST_DELTA: Final = f"{DRIVE}/root/delta?token=latest"
RESYNC_UPLOAD_DIFFERENCES: Final = "resyncChangesUploadDifferences"
# Bound of the pages followed in one listing or one delta read.
MAX_PAGES: Final = 10_000
SETTINGS_PLACE: Final = f"Paramètres {chr(0x203A)} Planning"

HTTP_BAD_REQUEST: Final = 400
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
HTTP_NOT_FOUND: Final = 404
HTTP_CONFLICT: Final = 409
HTTP_GONE: Final = 410
HTTP_PRECONDITION_FAILED: Final = 412
HTTP_LOCKED: Final = 423
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR: Final = 500
# A delta link refused with these statuses is dropped for a full enumeration.
STALE_CURSOR_STATUSES: Final = frozenset({HTTP_BAD_REQUEST, HTTP_NOT_FOUND})
# The pre-authenticated address expired or vanished: the content request gives a new one.
DOWNLOAD_RETRY_STATUSES: Final = frozenset({HTTP_UNAUTHORIZED, HTTP_FORBIDDEN, HTTP_NOT_FOUND})
OFFLINE_STATUSES: Final = frozenset({HTTP_LOCKED, HTTP_TOO_MANY_REQUESTS})

SIGN_IN_MESSAGE: Final = (
    "Reconnexion Microsoft requise pour le planning OneDrive. Cliquez sur « Se connecter "
    "à Microsoft » en haut de la fenêtre ; aucune fenêtre de connexion n'est ouverte "
    "automatiquement. Consultation seule depuis la copie de ce poste."
)
FORBIDDEN_MESSAGE: Final = (
    "Planning OneDrive : accès refusé (403) au dossier du planning. Vérifiez que ce poste "
    "est connecté au compte Microsoft partagé (celui du répertoire chantier) et que le "
    "dossier n'est pas en lecture seule. Consultation seule depuis la copie de ce poste."
)
OFFLINE_MESSAGE: Final = (
    "Planning OneDrive injoignable pour le moment (réseau ou Microsoft). Consultation "
    "seule depuis la copie de ce poste ; cliquez sur « Réessayer » quand le réseau est "
    "revenu."
)
CONFLICT_MESSAGE: Final = (
    "Cet élément du planning a été modifié sur un autre poste entre-temps (412). "
    "Actualisez le planning puis recommencez la modification."
)
EXISTS_MESSAGE: Final = "Cet élément du planning existe déjà : il a été créé sur un autre poste."
MISSING_MESSAGE: Final = "Cet élément du planning a été supprimé sur un autre poste entre-temps."

_FILE_NAME: Final = re.compile(
    rf"({'|'.join(DOCUMENT_KINDS)}){KIND_SEPARATOR}([A-Za-z0-9_-]{{1,128}})\.json",
)
_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")

_Key = tuple[str, str]
_Result = TypeVar("_Result")


@dataclass(frozen=True, slots=True)
class DriveFolder:
    drive_id: str
    folder_id: str


@dataclass(frozen=True, slots=True)
class _Read:
    documents: tuple[StoredDocument, ...]
    complete: bool
    """False when a file of the folder could not be read: nothing may be dropped."""


def document_file_name(kind: str, doc_id: str) -> str:
    """Name of the file of one document: ``<kind>__<doc_id>.json``."""
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"Type de document de planning invalide: {kind!r}")
    if not _SAFE_NAME.fullmatch(doc_id):
        raise ValueError(f"Identifiant de document de planning invalide: {doc_id!r}")
    return f"{kind}{KIND_SEPARATOR}{doc_id}{FILE_SUFFIX}"


def parse_file_name(name: object) -> _Key | None:
    """``(kind, doc_id)`` of a planning file, None for any other file."""
    if not isinstance(name, str):
        return None
    match = _FILE_NAME.fullmatch(name)
    return (match.group(1), match.group(2)) if match else None


def folder_segments(folder: str) -> tuple[str, ...]:
    """Folders of a path such as « ProjectFlow Planning » or « Atelier/Planning »."""
    parts = tuple(part.strip() for part in folder.replace("\\", "/").split("/"))
    segments = tuple(part for part in parts if part)
    return segments or (DEFAULT_ONEDRIVE_FOLDER,)


class DrivePlanningStore:
    """``PlanningStore`` over a folder of ``/me/drive`` (see the module documentation)."""

    def __init__(
        self,
        *,
        graph: GraphClient,
        folder: str = DEFAULT_ONEDRIVE_FOLDER,
        page_size: int = CHILDREN_PAGE_SIZE,
        download_concurrency: int = DOWNLOAD_CONCURRENCY,
    ) -> None:
        self._graph = graph
        self._segments = folder_segments(folder)
        self._page_size = page_size
        self._downloads = asyncio.Semaphore(max(1, download_concurrency))
        self._folder: DriveFolder | None = None
        self._folder_lock = asyncio.Lock()
        self._items: dict[_Key, str] = {}
        self._keys: dict[str, _Key] = {}
        self._complete = False
        """True once a full read was handed over: an unknown deletion is then foreign."""
        self._needs_full_read = False

    @property
    def folder_path(self) -> str:
        return "/".join(self._segments)

    @property
    def folder(self) -> DriveFolder | None:
        """The planning folder once found (``open``), else None."""
        return self._folder

    @property
    def drive_id(self) -> str:
        return self._folder.drive_id if self._folder is not None else ""

    async def open(self) -> DriveFolder:
        """Find the planning folder, creating it when missing; errors are mapped."""
        return await self._mapped(self._ensure_folder())

    async def changes(self, cursor: str | None) -> StoreChanges:
        return await self._mapped(self._changes(cursor))

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:
        return await self._mapped(self._get(kind, doc_id))

    async def create(
        self,
        kind: str,
        doc_id: str,
        data: Mapping[str, Any],
        *,
        project: str = "",  # noqa: ARG002 - the project number is inside the data
    ) -> StoredDocument:
        name = document_file_name(kind, doc_id)
        content = _encode(data)
        return await self._mapped(self._create(kind, doc_id, name, content))

    async def update(
        self,
        kind: str,
        doc_id: str,
        etag: str,
        data: Mapping[str, Any],
        *,
        project: str | None = None,  # noqa: ARG002 - the project number is inside the data
    ) -> str:
        document_file_name(kind, doc_id)
        return await self._mapped(self._update((kind, doc_id), etag, _encode(data)))

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:
        document_file_name(kind, doc_id)
        await self._mapped(self._delete((kind, doc_id), etag))

    async def aclose(self) -> None:
        await self._graph.aclose()

    # -------------------------------------------------------------- folder ----

    async def _ensure_folder(self) -> DriveFolder:
        async with self._folder_lock:
            if self._folder is not None:
                return self._folder
            path = "/".join(quote(segment, safe="") for segment in self._segments)
            try:
                raw = await self._graph.get(f"{DRIVE}/root:/{path}")
            except GraphError as exc:
                if exc.status_code != HTTP_NOT_FOUND:
                    raise
                raw = await self._create_folders()
                # A new folder: whatever the cursor saw before is not in it.
                self._needs_full_read = True
            self._folder = _drive_folder(raw, self.folder_path)
            get_logger(__name__).info("planning.onedrive.folder")
            return self._folder

    async def _create_folders(self) -> dict[str, Any]:
        parent = f"{DRIVE}/root"
        raw: dict[str, Any] = {}
        for segment in self._segments:
            raw = await self._child_folder(parent, segment)
            parent = f"{DRIVE}/items/{quote(str(raw.get('id') or ''), safe='')}"
        return raw

    async def _child_folder(self, parent: str, name: str) -> dict[str, Any]:
        try:
            return await self._graph.get(f"{parent}:/{quote(name, safe='')}")
        except GraphError as exc:
            if exc.status_code != HTTP_NOT_FOUND:
                raise
        try:
            return await self._graph.post(
                f"{parent}/children",
                json={
                    "name": name,
                    "folder": {},
                    "@microsoft.graph.conflictBehavior": "fail",
                },
            )
        except GraphError as exc:
            if exc.status_code != HTTP_CONFLICT:
                raise
        # Another poste created it at the same moment.
        return await self._graph.get(f"{parent}:/{quote(name, safe='')}")

    def _lose_folder(self) -> None:
        self._folder = None
        self._items.clear()
        self._keys.clear()
        self._complete = False

    # ------------------------------------------------------------- reading ----

    async def _changes(self, cursor: str | None) -> StoreChanges:
        folder = await self._ensure_folder()
        if not cursor or self._needs_full_read:
            return await self._full_read()
        try:
            raw_items, link = await self._collect(cursor)
        except GraphError as exc:
            if exc.status_code == HTTP_GONE:
                get_logger(__name__).info("planning.onedrive.resync", code=exc.error_code or "")
                upload = (exc.error_code or "").casefold() == RESYNC_UPLOAD_DIFFERENCES.casefold()
                return await self._full_read(upload_local=upload)
            if exc.status_code in STALE_CURSOR_STATUSES:
                get_logger(__name__).warning("planning.onedrive.cursor_refused")
                return await self._full_read()
            raise
        if any(_is_folder_gone(raw, folder) for raw in raw_items):
            get_logger(__name__).warning("planning.onedrive.folder_gone")
            self._lose_folder()
            await self._ensure_folder()
            return await self._full_read()
        documents, wanted, ambiguous = self._sort_delta(raw_items, folder)
        if ambiguous and not self._complete:
            # Which document a deleted item held is unknown after a restart: read all.
            return await self._full_read()
        read = await self._download_all(wanted)
        merged = {(document.kind, document.doc_id): document for document in documents}
        merged.update({(document.kind, document.doc_id): document for document in read.documents})
        return StoreChanges(documents=tuple(merged.values()), cursor=link)

    async def _full_read(self, *, upload_local: bool = False) -> StoreChanges:
        """Every document of the folder, with a delta link taken before the listing."""
        self._needs_full_read = False
        _latest, link = await self._collect(LATEST_DELTA)
        folder = await self._ensure_folder()
        children = await self._list_children(folder)
        self._items.clear()
        self._keys.clear()
        wanted: list[tuple[_Key, dict[str, Any]]] = []
        for raw in children:
            key = parse_file_name(raw.get("name"))
            item_id = raw.get("id")
            if key is None or "file" not in raw or not isinstance(item_id, str):
                continue
            self._remember(item_id, key)
            wanted.append((key, raw))
        read = await self._download_all(wanted)
        if read.complete:
            self._complete = True
        return StoreChanges(
            documents=read.documents,
            cursor=link,
            full=read.complete,
            upload_local=read.complete and upload_local,
        )

    async def _collect(self, link: str) -> tuple[list[dict[str, Any]], str]:
        """Follow the pages of one delta read; the latest state of each item, in order."""
        items: dict[str, dict[str, Any]] = {}
        url = link
        for _page in range(MAX_PAGES):
            payload = await self._graph.get(url)
            for raw in _values(payload):
                item_id = raw.get("id")
                if isinstance(item_id, str):
                    items.pop(item_id, None)
                    items[item_id] = raw
            next_link = payload.get("@odata.nextLink")
            if isinstance(next_link, str) and next_link:
                url = next_link
                continue
            delta_link = payload.get("@odata.deltaLink")
            if isinstance(delta_link, str) and delta_link:
                return list(items.values()), delta_link
            break
        raise GraphError("Microsoft Graph n'a pas retourne de lien de suivi des modifications.")

    async def _list_children(self, folder: DriveFolder) -> list[dict[str, Any]]:
        url = f"{DRIVE}/items/{quote(folder.folder_id, safe='')}/children?$top={self._page_size}"
        children: list[dict[str, Any]] = []
        for _page in range(MAX_PAGES):
            payload = await self._graph.get(url)
            children.extend(_values(payload))
            next_link = payload.get("@odata.nextLink")
            if not isinstance(next_link, str) or not next_link:
                return children
            url = next_link
        return children

    def _sort_delta(
        self,
        raw_items: Sequence[dict[str, Any]],
        folder: DriveFolder,
    ) -> tuple[list[StoredDocument], list[tuple[_Key, dict[str, Any]]], bool]:
        """Return the deletions, the files to download and whether a deletion is unplaced."""
        deletions: dict[_Key, StoredDocument] = {}
        wanted: dict[_Key, tuple[_Key, dict[str, Any]]] = {}
        ambiguous = False
        live = [raw for raw in raw_items if "deleted" not in raw]
        gone = [raw for raw in raw_items if "deleted" in raw]
        for raw in gone:
            item_id = str(raw.get("id"))
            known = self._keys.get(item_id)
            parent = _parent_id(raw)
            named = parse_file_name(raw.get("name"))
            if known is not None:
                self._forget(item_id)
                deletions[known] = _deletion(known, raw)
            elif named is not None and parent in {None, folder.folder_id}:
                if self._items.get(named) in {None, item_id}:
                    self._items.pop(named, None)
                    deletions[named] = _deletion(named, raw)
            elif parent in {None, folder.folder_id}:
                ambiguous = True
        for raw in live:
            item_id = str(raw.get("id"))
            known = self._keys.get(item_id)
            key = parse_file_name(raw.get("name"))
            inside = _parent_id(raw) == folder.folder_id and "file" in raw
            if known is not None and (not inside or key != known):
                # Moved out of the folder or renamed: the document it held is gone.
                self._forget(item_id)
                if self._items.get(known) is None:
                    deletions[known] = _deletion(known, raw)
            if key is None or not inside:
                continue
            self._remember(item_id, key)
            deletions.pop(key, None)
            wanted[key] = (key, raw)
        return list(deletions.values()), list(wanted.values()), ambiguous

    async def _download_all(self, wanted: Sequence[tuple[_Key, dict[str, Any]]]) -> _Read:
        results = await asyncio.gather(
            *(self._download_one(key, raw) for key, raw in wanted),
            return_exceptions=True,
        )
        documents: list[StoredDocument] = []
        complete = True
        for result in results:
            if isinstance(result, BaseException):
                raise result
            document, readable = result
            complete = complete and readable
            if document is not None:
                documents.append(document)
        return _Read(documents=tuple(documents), complete=complete)

    async def _download_one(
        self,
        key: _Key,
        raw: Mapping[str, Any],
    ) -> tuple[StoredDocument | None, bool]:
        """Return the document of one file, and False when the file could not be read."""
        item_id = str(raw.get("id"))
        async with self._downloads:
            try:
                content = await self._content(item_id, raw.get(DOWNLOAD_URL_KEY))
            except GraphError as exc:
                if exc.status_code != HTTP_NOT_FOUND:
                    raise
                # Deleted since it was listed: the next delta reports it.
                return None, True
        return _document(key, raw, content)

    async def _content(self, item_id: str, download_url: object) -> bytes:
        if isinstance(download_url, str) and download_url:
            try:
                return await self._graph.download(download_url)
            except GraphError as exc:
                if exc.status_code not in DOWNLOAD_RETRY_STATUSES:
                    raise
        return await self._graph.get_bytes(f"{DRIVE}/items/{quote(item_id, safe='')}/content")

    async def _get(self, kind: str, doc_id: str) -> StoredDocument | None:
        key = (kind, doc_id)
        name = document_file_name(kind, doc_id)
        raw = await self._metadata(key, name)
        if raw is None:
            return None
        document, _readable = await self._download_one(key, raw)
        return document

    async def _metadata(self, key: _Key, name: str) -> dict[str, Any] | None:
        """Return the item of a document, by its known id, else by its path; None if gone."""
        folder = await self._ensure_folder()
        item_id = self._items.get(key)
        if item_id is not None:
            try:
                raw = await self._graph.get(f"{DRIVE}/items/{quote(item_id, safe='')}")
            except GraphError as exc:
                if exc.status_code != HTTP_NOT_FOUND:
                    raise
                self._forget(item_id)
            else:
                if (
                    "deleted" not in raw
                    and _parent_id(raw) == folder.folder_id
                    and parse_file_name(raw.get("name")) == key
                ):
                    return raw
                self._forget(item_id)
        try:
            raw = await self._graph.get(self._path(folder, name))
        except GraphError as exc:
            if exc.status_code != HTTP_NOT_FOUND:
                raise
            return None
        found = raw.get("id")
        if not isinstance(found, str) or "file" not in raw:
            return None
        self._remember(found, key)
        return raw

    # ------------------------------------------------------------- writing ----

    async def _create(
        self,
        kind: str,
        doc_id: str,
        name: str,
        content: bytes,
    ) -> StoredDocument:
        for attempt in range(2):
            folder = await self._ensure_folder()
            try:
                raw = await self._graph.put_bytes(
                    f"{self._path(folder, name)}:/content?@microsoft.graph.conflictBehavior=fail",
                    content,
                )
            except GraphError as exc:
                if exc.status_code == HTTP_CONFLICT:
                    raise StoreConflictError(EXISTS_MESSAGE) from exc
                if exc.status_code == HTTP_NOT_FOUND and attempt == 0:
                    # The folder went away: found (or created) again, then one retry.
                    self._lose_folder()
                    continue
                raise
            item_id = raw.get("id")
            if isinstance(item_id, str):
                self._remember(item_id, (kind, doc_id))
            return StoredDocument(
                kind=kind,
                doc_id=doc_id,
                etag=str(raw.get("eTag") or ""),
                data=json.loads(content),
            )
        raise StoreConflictError(MISSING_MESSAGE)  # pragma: no cover - loop always returns

    async def _update(self, key: _Key, etag: str, content: bytes) -> str:
        item_id = await self._item_id(key)
        if item_id is None:
            raise StoreConflictError(MISSING_MESSAGE)
        try:
            raw = await self._graph.put_bytes(
                f"{DRIVE}/items/{quote(item_id, safe='')}/content",
                content,
                headers={"If-Match": etag},
            )
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                self._forget(item_id)
                raise StoreConflictError(MISSING_MESSAGE) from exc
            raise
        return str(raw.get("eTag") or "")

    async def _delete(self, key: _Key, etag: str) -> None:
        item_id = await self._item_id(key)
        if item_id is None:
            return
        try:
            await self._graph.delete(
                f"{DRIVE}/items/{quote(item_id, safe='')}",
                headers={"If-Match": etag},
            )
        except GraphError as exc:
            if exc.status_code != HTTP_NOT_FOUND:
                raise
        self._forget(item_id)

    async def _item_id(self, key: _Key) -> str | None:
        item_id = self._items.get(key)
        if item_id is not None:
            return item_id
        raw = await self._metadata(key, document_file_name(*key))
        found = raw.get("id") if raw is not None else None
        return found if isinstance(found, str) else None

    def _path(self, folder: DriveFolder, name: str) -> str:
        return f"{DRIVE}/items/{quote(folder.folder_id, safe='')}:/{quote(name, safe='')}"

    def _remember(self, item_id: str, key: _Key) -> None:
        previous = self._items.get(key)
        if previous is not None and previous != item_id:
            self._keys.pop(previous, None)
        self._items[key] = item_id
        self._keys[item_id] = key

    def _forget(self, item_id: str) -> None:
        key = self._keys.pop(item_id, None)
        if key is not None and self._items.get(key) == item_id:
            del self._items[key]

    # -------------------------------------------------------------- errors ----

    async def _mapped(self, operation: Awaitable[_Result]) -> _Result:
        """Run ``operation``, with Microsoft's failures read as the planning's errors."""
        failure: StoreConflictError | StoreUnavailableError
        try:
            return await operation
        except (StoreConflictError, StoreUnavailableError, ConfigError):
            raise
        except AuthError as exc:
            failure = _auth_failure(exc)
        except GraphError as exc:
            mapped = _graph_failure(exc)
            if mapped is None:
                raise
            failure = mapped
        except OSError:
            # MSAL reads the authority outside its own error handling: offline.
            failure = StoreUnavailableError(OFFLINE_MESSAGE)
        # Raised outside the ``except``: an offline failure must not hang on the
        # AuthError, which the sync state would read as « reconnexion requise ».
        raise failure


def _auth_failure(error: AuthError) -> StoreUnavailableError:
    if _network_failure(error):
        return StoreUnavailableError(OFFLINE_MESSAGE)
    failure = StoreUnavailableError(SIGN_IN_MESSAGE)
    failure.__cause__ = error
    return failure


def _graph_failure(error: GraphError) -> StoreConflictError | StoreUnavailableError | None:
    status = error.status_code
    if status == HTTP_PRECONDITION_FAILED:
        failure: StoreConflictError | StoreUnavailableError = StoreConflictError(CONFLICT_MESSAGE)
    elif status == HTTP_UNAUTHORIZED:
        failure = StoreUnavailableError(SIGN_IN_MESSAGE)
    elif status == HTTP_FORBIDDEN:
        failure = StoreUnavailableError(FORBIDDEN_MESSAGE)
    elif status is None or status in OFFLINE_STATUSES or status >= HTTP_SERVER_ERROR:
        failure = StoreUnavailableError(OFFLINE_MESSAGE)
    else:
        return None
    failure.__cause__ = error
    return failure


def _network_failure(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error.__cause__
    while current is not None and id(current) not in seen:
        if isinstance(current, OSError):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


def _drive_folder(raw: Mapping[str, Any], path: str) -> DriveFolder:
    folder_id = raw.get("id")
    parent = raw.get("parentReference")
    drive_id = parent.get("driveId") if isinstance(parent, Mapping) else None
    if "folder" not in raw or not isinstance(folder_id, str) or not folder_id:
        raise ConfigError(
            f"Planning OneDrive : « {path} » existe dans OneDrive mais n'est pas un dossier. "
            "Renommez ce fichier ou choisissez un autre nom de dossier dans "
            f"{SETTINGS_PLACE}.",
        )
    return DriveFolder(drive_id=drive_id if isinstance(drive_id, str) else "", folder_id=folder_id)


def _values(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("value")
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, dict)]


def _parent_id(raw: Mapping[str, Any]) -> str | None:
    parent = raw.get("parentReference")
    if not isinstance(parent, Mapping):
        return None
    parent_id = parent.get("id")
    return parent_id if isinstance(parent_id, str) and parent_id else None


def _is_folder_gone(raw: Mapping[str, Any], folder: DriveFolder) -> bool:
    return raw.get("id") == folder.folder_id and "deleted" in raw


def _deletion(key: _Key, raw: Mapping[str, Any]) -> StoredDocument:
    return StoredDocument(
        kind=key[0],
        doc_id=key[1],
        etag=str(raw.get("eTag") or ""),
        data=None,
        deleted=True,
    )


def _document(
    key: _Key,
    raw: Mapping[str, Any],
    content: bytes,
) -> tuple[StoredDocument | None, bool]:
    try:
        data = json.loads(content.decode("utf-8-sig"))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        get_logger(__name__).warning(
            "planning.onedrive.document_unreadable",
            kind=key[0],
            name=str(raw.get("name") or ""),
        )
        return None, False
    own_id = data.get("id")
    if isinstance(own_id, str) and own_id and own_id != key[1]:
        # A copy under another name (made by hand or by a sync client): not this document.
        get_logger(__name__).warning(
            "planning.onedrive.document_foreign",
            kind=key[0],
            name=str(raw.get("name") or ""),
        )
        return None, True
    return (
        StoredDocument(kind=key[0], doc_id=key[1], etag=str(raw.get("eTag") or ""), data=data),
        True,
    )


def _encode(data: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
