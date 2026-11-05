"""DrivePlanningStore against an in-memory OneDrive answering like Microsoft Graph."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from projectflow import services
from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import GRAPH_SCOPES, AccessTokenProvider
from projectflow.config import AppConfig
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.core.planning.store import (
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.exceptions import AuthError, ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.drive_planning_store import (
    DEFAULT_ONEDRIVE_FOLDER,
    DrivePlanningStore,
    document_file_name,
    folder_segments,
    parse_file_name,
)
from projectflow.ui.planning_controller import failure_status

BASE = "https://graph.microsoft.com/v1.0"
DOWNLOAD_HOST = "https://download.example"
DRIVE_ID = "b!drive-1"
CLIENT_ID = "11111111-2222-3333-4444-555555555555"


@dataclass
class Item:
    id: str
    name: str
    parent: str
    folder: bool = False
    content: bytes = b""
    etag: str = ""
    seq: int = 0
    deleted: bool = False


@dataclass
class FakeDrive:
    """One OneDrive: folders, files, eTags and a delta journal; records every request."""

    items: dict[str, Item] = field(default_factory=dict)
    seq: int = 0
    counter: int = 0
    requests: list[httpx.Request] = field(default_factory=list)
    download_urls: bool = True
    deleted_names: bool = True
    page_size: int = 2
    gone_tokens: dict[str, str] = field(default_factory=dict)
    """Delta tokens refused with 410 and this error code."""
    failures: list[tuple[re.Pattern[str], Callable[[httpx.Request], httpx.Response]]] = field(
        default_factory=list,
    )

    def __post_init__(self) -> None:
        self.items["root"] = Item("root", "root", "", folder=True)

    # ----------------------------------------------------------- set up ----

    def new_id(self) -> str:
        self.counter += 1
        return f"item-{self.counter:03d}"

    def touch(self, item: Item) -> None:
        self.seq += 1
        item.seq = self.seq
        item.etag = f'"{{{item.id}}},{self.seq}"'

    def add_folder(self, name: str, parent: str = "root") -> Item:
        item = Item(self.new_id(), name, parent, folder=True)
        self.items[item.id] = item
        self.touch(item)
        return item

    def add_file(self, name: str, content: bytes | dict[str, Any], parent: str) -> Item:
        body = content if isinstance(content, bytes) else json.dumps(content).encode()
        item = Item(self.new_id(), name, parent, content=body)
        self.items[item.id] = item
        self.touch(item)
        return item

    def write(self, item: Item, content: dict[str, Any]) -> None:
        item.content = json.dumps(content).encode()
        self.touch(item)

    def remove(self, item: Item) -> None:
        item.deleted = True
        self.touch(item)

    def fail(self, pattern: str, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        self.failures.append((re.compile(pattern), answer))

    def child(self, parent: str, name: str) -> Item | None:
        return next(
            (
                item
                for item in self.items.values()
                if item.parent == parent
                and not item.deleted
                and item.name.casefold() == name.casefold()
            ),
            None,
        )

    def client(self, token_provider: AccessTokenProvider | None = None) -> GraphClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.answer))
        return GraphClient(token_provider=token_provider or Token(), http_client=http)

    def store(self, **options: Any) -> DrivePlanningStore:
        return DrivePlanningStore(graph=self.client(), **options)

    # ------------------------------------------------------------ server ----

    def json_item(self, item: Item) -> dict[str, Any]:
        if item.deleted:
            raw: dict[str, Any] = {
                "id": item.id,
                "deleted": {"state": "deleted"},
                "parentReference": {"driveId": DRIVE_ID, "id": item.parent},
            }
            if self.deleted_names:
                raw["name"] = item.name
            return raw
        raw = {
            "id": item.id,
            "name": item.name,
            "eTag": item.etag,
            "parentReference": {"driveId": DRIVE_ID, "id": item.parent},
        }
        if item.id == "root":
            raw.pop("parentReference")
            raw["root"] = {}
        if item.folder:
            raw["folder"] = {"childCount": 0}
        else:
            raw["file"] = {"mimeType": "application/json"}
            if self.download_urls:
                raw["@microsoft.graph.downloadUrl"] = f"{DOWNLOAD_HOST}/{item.id}?tempauth=x"
        return raw

    def answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for pattern, answer in self.failures:
            if pattern.search(unquote(url)):
                return answer(request)
        if url.startswith(DOWNLOAD_HOST):
            assert "Authorization" not in request.headers
            item = self.items.get(request.url.path.strip("/"))
            if item is None or item.deleted:
                return httpx.Response(404)
            return httpx.Response(200, content=item.content)
        assert request.headers.get("Authorization") == "Bearer token"
        path = unquote(request.url.raw_path.decode().split("?", 1)[0]).removeprefix("/v1.0")
        return self.route(request, path)

    def route(self, request: httpx.Request, path: str) -> httpx.Response:  # noqa: C901, PLR0911, PLR0912
        method = request.method
        params = request.url.params
        if path == "/me/drive/root/delta":
            return self.delta(params.get("token"), int(params.get("skip", "0")))
        if match := re.fullmatch(r"/me/drive/root:/(.+)", path):
            item = self.resolve("root", match.group(1))
            return self.item_answer(item)
        if match := re.fullmatch(r"/me/drive/(?:root|items/([^/:]+))/children", path):
            parent = match.group(1) or "root"
            if method == "POST":
                body = json.loads(request.content)
                if self.child(parent, body["name"]) is not None:
                    return error(409, "nameAlreadyExists")
                return httpx.Response(
                    201, json=self.json_item(self.add_folder(body["name"], parent))
                )
            return self.children(
                parent, int(params.get("$top", "200")), int(params.get("skip", "0"))
            )
        if match := re.fullmatch(r"/me/drive/items/([^/:]+):/([^:]+):/content", path):
            parent, name = match.groups()
            if method != "PUT":
                return error(405, "invalidRequest")
            if parent not in self.items or self.items[parent].deleted:
                return error(404, "itemNotFound")
            if self.child(parent, name) is not None:
                assert params.get("@microsoft.graph.conflictBehavior") == "fail"
                return error(409, "nameAlreadyExists")
            item = self.add_file(name, request.content, parent)
            return httpx.Response(201, json=self.json_item(item))
        if match := re.fullmatch(r"/me/drive/items/([^/:]+):/([^:]+)", path):
            item = self.resolve(match.group(1), match.group(2))
            return self.item_answer(item)
        if match := re.fullmatch(r"/me/drive/items/([^/:]+)/content", path):
            item = self.items.get(match.group(1))
            if item is None or item.deleted:
                return error(404, "itemNotFound")
            if method == "GET":
                return httpx.Response(302, headers={"Location": f"{DOWNLOAD_HOST}/{item.id}"})
            if request.headers.get("If-Match") not in {None, item.etag}:
                return error(412, "resourceModified")
            item.content = request.content
            self.touch(item)
            return httpx.Response(200, json=self.json_item(item))
        if match := re.fullmatch(r"/me/drive/items/([^/:]+)", path):
            item = self.items.get(match.group(1))
            if item is None or item.deleted:
                return error(404, "itemNotFound")
            if method == "DELETE":
                if request.headers.get("If-Match") not in {None, item.etag}:
                    return error(412, "resourceModified")
                self.remove(item)
                return httpx.Response(204)
            return httpx.Response(200, json=self.json_item(item))
        return error(400, "invalidRequest")

    def resolve(self, parent: str, path: str) -> Item | None:
        item: Item | None = self.items.get(parent)
        for segment in path.split("/"):
            if item is None or item.deleted:
                return None
            item = self.child(item.id, segment)
        return item

    def item_answer(self, item: Item | None) -> httpx.Response:
        if item is None or item.deleted:
            return error(404, "itemNotFound")
        return httpx.Response(200, json=self.json_item(item))

    def children(self, parent: str, top: int, skip: int) -> httpx.Response:
        found = [item for item in self.items.values() if item.parent == parent and not item.deleted]
        page = found[skip : skip + top]
        payload: dict[str, Any] = {"value": [self.json_item(item) for item in page]}
        if skip + top < len(found):
            payload["@odata.nextLink"] = (
                f"{BASE}/me/drive/items/{parent}/children?$top={top}&skip={skip + top}"
            )
        return httpx.Response(200, json=payload)

    def delta(self, token: str | None, skip: int) -> httpx.Response:
        if token in self.gone_tokens:
            return error(410, self.gone_tokens[token])
        if token == "latest":
            return httpx.Response(
                200,
                json={
                    "value": [],
                    "@odata.deltaLink": f"{BASE}/me/drive/root/delta?token={self.seq}",
                },
            )
        since = int(token or "0")
        changed = sorted(
            (item for item in self.items.values() if item.seq > since and item.id != "root"),
            key=lambda item: item.seq,
        )
        page = changed[skip : skip + self.page_size]
        payload: dict[str, Any] = {"value": [self.json_item(item) for item in page]}
        if skip + self.page_size < len(changed):
            payload["@odata.nextLink"] = (
                f"{BASE}/me/drive/root/delta?token={since}&skip={skip + self.page_size}"
            )
        else:
            payload["@odata.deltaLink"] = f"{BASE}/me/drive/root/delta?token={self.seq}"
        return httpx.Response(200, json=payload)

    def routes(self) -> list[str]:
        return [f"{request.method} {unquote(str(request.url))}" for request in self.requests]


class Token:
    async def access_token(self) -> str:
        return "token"


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": code}})


def task(doc_id: str, title: str = "Pose") -> dict[str, Any]:
    return {"id": doc_id, "title": title, "project_number": "2026-5245"}


@pytest.fixture
def drive() -> FakeDrive:
    return FakeDrive()


def planning_folder(drive: FakeDrive) -> Item:
    return drive.add_folder(DEFAULT_ONEDRIVE_FOLDER)


def by_key(documents: tuple[StoredDocument, ...]) -> dict[tuple[str, str], StoredDocument]:
    return {(document.kind, document.doc_id): document for document in documents}


# ------------------------------------------------------------------- names ----


def test_file_names_hold_the_kind_and_the_id() -> None:
    assert document_file_name("task", "abc123") == "task__abc123.json"
    assert parse_file_name("meta__meta.json") == ("meta", "meta")
    assert parse_file_name("absence__a_b-c.json") == ("absence", "a_b-c")
    for other in ("notes.txt", "task__abc.json.tmp", "probe__x.json", "task-abc.json", None):
        assert parse_file_name(other) is None
    with pytest.raises(ValueError, match="invalide"):
        document_file_name("probe", "abc")
    with pytest.raises(ValueError, match="invalide"):
        document_file_name("task", "../x")


def test_folder_paths_are_normalised() -> None:
    assert folder_segments("") == (DEFAULT_ONEDRIVE_FOLDER,)
    assert folder_segments(" /Atelier\\ Planning / ") == ("Atelier", "Planning")


# ------------------------------------------------------------------ folder ----


@pytest.mark.asyncio
async def test_missing_folder_is_created_at_the_root(drive: FakeDrive) -> None:
    store = drive.store()

    folder = await store.open()

    created = drive.child("root", DEFAULT_ONEDRIVE_FOLDER)
    assert created is not None
    assert created.folder
    assert folder.folder_id == created.id
    assert folder.drive_id == DRIVE_ID
    post = next(request for request in drive.requests if request.method == "POST")
    assert json.loads(post.content)["@microsoft.graph.conflictBehavior"] == "fail"


@pytest.mark.asyncio
async def test_existing_folder_is_found_without_creating_it(drive: FakeDrive) -> None:
    existing = planning_folder(drive)
    store = drive.store()

    folder = await store.open()

    assert folder.folder_id == existing.id
    assert [request.method for request in drive.requests] == ["GET"]
    assert drive.requests[0].url.raw_path.decode().endswith("/root:/ProjectFlow%20Planning")


@pytest.mark.asyncio
async def test_nested_folder_path_creates_each_level(drive: FakeDrive) -> None:
    atelier = drive.add_folder("Atelier")
    store = drive.store(folder="Atelier/Planning")

    folder = await store.open()

    planning = drive.child(atelier.id, "Planning")
    assert planning is not None
    assert folder.folder_id == planning.id


@pytest.mark.asyncio
async def test_folder_created_by_another_poste_meanwhile_is_used(drive: FakeDrive) -> None:
    store = drive.store()
    other: list[Item] = []

    def race(request: httpx.Request) -> httpx.Response:
        drive.failures.clear()
        other.append(drive.add_folder(DEFAULT_ONEDRIVE_FOLDER))
        return error(409, "nameAlreadyExists")

    drive.fail(r"/root/children$", race)

    folder = await store.open()

    assert folder.folder_id == other[0].id


@pytest.mark.asyncio
async def test_a_file_with_the_folder_name_is_a_configuration_error(drive: FakeDrive) -> None:
    drive.add_file(DEFAULT_ONEDRIVE_FOLDER, b"x", "root")

    with pytest.raises(ConfigError, match="n'est pas un dossier"):
        await drive.store().open()


# -------------------------------------------------------------------- read ----


@pytest.mark.asyncio
async def test_first_read_lists_the_folder_and_takes_the_latest_link_before(
    drive: FakeDrive,
) -> None:
    folder = planning_folder(drive)
    for index in range(5):
        drive.add_file(f"task__t{index}.json", task(f"t{index}"), folder.id)
    drive.add_file("meta__meta.json", {"id": "meta"}, folder.id)
    drive.add_file("notes.txt", b"hello", folder.id)
    drive.add_file("task__t9.json", b"{}", "root")  # same name, elsewhere in the drive
    drive.add_folder("task__sub.json", folder.id)
    store = drive.store(page_size=2)

    changes = await store.changes(None)

    assert changes.full
    assert not changes.upload_local
    documents = by_key(changes.documents)
    assert set(documents) == {("task", f"t{index}") for index in range(5)} | {("meta", "meta")}
    assert documents["task", "t0"].data == task("t0")
    assert documents["task", "t0"].etag == drive.child(folder.id, "task__t0.json").etag  # type: ignore[union-attr]
    assert changes.cursor == f"{BASE}/me/drive/root/delta?token={drive.seq}"
    routes = drive.routes()
    latest = routes.index(f"GET {BASE}/me/drive/root/delta?token=latest")
    listing = next(index for index, route in enumerate(routes) if "/children" in route)
    assert latest < listing
    # Four pages of children ($top=2) for the 8 items of the folder.
    assert sum("/children" in route for route in routes) == 4


@pytest.mark.asyncio
async def test_delta_keeps_only_the_planning_folder(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    first = drive.add_file("task__a.json", task("a"), folder.id)
    store = drive.store()
    start = await store.changes(None)

    drive.write(first, task("a", "Soudure"))
    drive.add_file("task__b.json", task("b"), folder.id)
    drive.add_file("task__c.json", task("c"), "root")
    drive.add_file("Rapport.xlsx", b"PK", folder.id)
    elsewhere = drive.add_folder("Photos")
    drive.add_file("meta__meta.json", {"id": "meta"}, elsewhere.id)
    changes = await store.changes(start.cursor)

    assert not changes.full
    documents = by_key(changes.documents)
    assert set(documents) == {("task", "a"), ("task", "b")}
    assert documents["task", "a"].data == task("a", "Soudure")
    assert documents["task", "a"].etag == first.etag
    assert changes.cursor == f"{BASE}/me/drive/root/delta?token={drive.seq}"
    again = await store.changes(changes.cursor)
    assert again.documents == ()


@pytest.mark.asyncio
async def test_deleted_files_come_back_as_deletions(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    item = drive.add_file("task__a.json", task("a"), folder.id)
    drive.add_file("task__b.json", task("b"), folder.id)
    drive.deleted_names = False
    store = drive.store()
    start = await store.changes(None)

    drive.remove(item)
    changes = await store.changes(start.cursor)

    assert changes.documents == (
        StoredDocument(kind="task", doc_id="a", etag="", data=None, deleted=True),
    )


@pytest.mark.asyncio
async def test_unplaced_deletion_after_a_restart_costs_one_full_read(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    item = drive.add_file("task__a.json", task("a"), folder.id)
    drive.add_file("task__b.json", task("b"), folder.id)
    cursor = (await drive.store().changes(None)).cursor
    drive.deleted_names = False
    drive.remove(item)

    restarted = drive.store()
    changes = await restarted.changes(cursor)

    assert changes.full
    assert set(by_key(changes.documents)) == {("task", "b")}
    # Known from then on: a deletion elsewhere in the drive changes nothing.
    other = drive.add_file("x.txt", b"x", folder.id)
    drive.remove(other)
    assert not (await restarted.changes(changes.cursor)).full


@pytest.mark.asyncio
async def test_named_deletion_after_a_restart_needs_no_full_read(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    item = drive.add_file("task__a.json", task("a"), folder.id)
    cursor = (await drive.store().changes(None)).cursor
    drive.remove(item)

    changes = await drive.store().changes(cursor)

    assert not changes.full
    assert [(doc.doc_id, doc.deleted) for doc in changes.documents] == [("a", True)]


@pytest.mark.asyncio
async def test_file_moved_out_or_renamed_is_a_deletion(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    moved = drive.add_file("task__a.json", task("a"), folder.id)
    renamed = drive.add_file("task__b.json", task("b"), folder.id)
    store = drive.store()
    start = await store.changes(None)

    moved.parent = "root"
    drive.touch(moved)
    renamed.name = "task__b (copie).json"
    drive.touch(renamed)
    changes = await store.changes(start.cursor)

    assert {(doc.doc_id, doc.deleted) for doc in changes.documents} == {("a", True), ("b", True)}


@pytest.mark.asyncio
async def test_unreadable_file_is_skipped_and_the_read_is_not_full(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    drive.add_file("task__bad.json", b"{not json", folder.id)
    drive.add_file("task__list.json", b"[1, 2]", folder.id)

    changes = await drive.store().changes(None)

    assert not changes.full
    assert set(by_key(changes.documents)) == {("task", "a")}


@pytest.mark.asyncio
async def test_copy_under_another_name_is_ignored(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    drive.add_file("task__a-BUREAU-LM.json", task("a"), folder.id)

    changes = await drive.store().changes(None)

    assert changes.full
    assert set(by_key(changes.documents)) == {("task", "a")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "upload"),
    [("resyncChangesApplyDifferences", False), ("resyncChangesUploadDifferences", True)],
)
async def test_410_restarts_with_a_full_read(drive: FakeDrive, code: str, *, upload: bool) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    store = drive.store()
    drive.gone_tokens["old"] = code

    changes = await store.changes(f"{BASE}/me/drive/root/delta?token=old")

    assert changes.full
    assert changes.upload_local is upload
    assert set(by_key(changes.documents)) == {("task", "a")}


@pytest.mark.asyncio
async def test_refused_cursor_restarts_with_a_full_read(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    drive.fail(r"token=bogus", lambda _request: error(400, "invalidRequest"))

    changes = await drive.store().changes(f"{BASE}/me/drive/root/delta?token=bogus")

    assert changes.full


@pytest.mark.asyncio
async def test_downloads_follow_the_content_redirect_without_download_url(
    drive: FakeDrive,
) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    drive.download_urls = False

    changes = await drive.store().changes(None)

    assert by_key(changes.documents)["task", "a"].data == task("a")
    routes = drive.routes()
    assert any(route.endswith("/content") and route.startswith("GET") for route in routes)
    assert any(route.startswith(f"GET {DOWNLOAD_HOST}/") for route in routes)


@pytest.mark.asyncio
async def test_downloads_use_the_download_url_when_given(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)

    await drive.store().changes(None)

    routes = drive.routes()
    assert not any(route.endswith("/content") for route in routes)
    assert any("tempauth=x" in route for route in routes)


@pytest.mark.asyncio
async def test_expired_download_url_falls_back_to_the_content_request(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    drive.fail(r"tempauth=x", lambda _request: httpx.Response(401))

    changes = await drive.store().changes(None)

    assert by_key(changes.documents)["task", "a"].data == task("a")


@pytest.mark.asyncio
async def test_downloads_run_four_at_a_time(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    for index in range(10):
        drive.add_file(f"task__t{index}.json", task(f"t{index}"), folder.id)
    store = drive.store()
    graph = store._graph  # noqa: SLF001 - the test wraps its downloads
    running = 0
    highest = 0
    original = graph.download

    async def counted(url: str) -> bytes:
        nonlocal running, highest
        running += 1
        highest = max(highest, running)
        try:
            import asyncio  # noqa: PLC0415

            await asyncio.sleep(0.001)
            return await original(url)
        finally:
            running -= 1

    graph.download = counted  # type: ignore[method-assign]

    changes = await store.changes(None)

    assert len(changes.documents) == 10
    assert highest == 4


@pytest.mark.asyncio
async def test_deleted_planning_folder_is_found_again_with_a_full_read(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    drive.add_file("task__a.json", task("a"), folder.id)
    store = drive.store()
    start = await store.changes(None)

    drive.remove(folder)
    changes = await store.changes(start.cursor)

    assert changes.full
    assert changes.documents == ()
    assert store.folder is not None
    assert store.folder.folder_id != folder.id


# ------------------------------------------------------------------- write ----


@pytest.mark.asyncio
async def test_create_writes_the_file_and_refuses_an_existing_one(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    store = drive.store()

    created = await store.create("task", "a", task("a"))

    item = drive.child(folder.id, "task__a.json")
    assert item is not None
    assert json.loads(item.content) == task("a")
    assert created == StoredDocument(kind="task", doc_id="a", etag=item.etag, data=task("a"))
    put = next(request for request in drive.requests if request.method == "PUT")
    assert put.url.params["@microsoft.graph.conflictBehavior"] == "fail"
    assert put.headers["Content-Type"] == "application/json"
    with pytest.raises(StoreConflictError, match="existe déjà"):
        await drive.store().create("task", "a", task("a", "Autre"))
    assert json.loads(item.content) == task("a")


@pytest.mark.asyncio
async def test_update_needs_the_current_etag(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    store = drive.store()
    created = await store.create("task", "a", task("a"))

    etag = await store.update("task", "a", created.etag, task("a", "Soudure"))

    item = drive.child(folder.id, "task__a.json")
    assert item is not None
    assert etag == item.etag != created.etag
    assert json.loads(item.content) == task("a", "Soudure")
    with pytest.raises(StoreConflictError, match="412"):
        await store.update("task", "a", created.etag, task("a", "Perdu"))
    assert json.loads(item.content) == task("a", "Soudure")


@pytest.mark.asyncio
async def test_update_after_a_restart_finds_the_file_by_its_path(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    item = drive.add_file("task__a.json", task("a"), folder.id)

    etag = await drive.store().update("task", "a", item.etag, task("a", "Soudure"))

    assert etag == item.etag
    with pytest.raises(StoreConflictError, match="supprimé"):
        await drive.store().update("task", "zz", item.etag, task("zz"))


@pytest.mark.asyncio
async def test_delete_needs_the_current_etag_and_tolerates_a_gone_file(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    store = drive.store()
    created = await store.create("task", "a", task("a"))
    etag = await store.update("task", "a", created.etag, task("a", "Soudure"))

    with pytest.raises(StoreConflictError):
        await store.delete("task", "a", created.etag)
    assert drive.child(folder.id, "task__a.json") is not None

    await store.delete("task", "a", etag)
    assert drive.child(folder.id, "task__a.json") is None
    await store.delete("task", "a", etag)  # already gone: nothing to do

    other = await store.create("task", "b", task("b"))
    drive.remove(drive.child(folder.id, "task__b.json"))  # type: ignore[arg-type]
    await store.delete("task", "b", other.etag)  # 404 from the item id


@pytest.mark.asyncio
async def test_get_reads_one_document(drive: FakeDrive) -> None:
    folder = planning_folder(drive)
    item = drive.add_file("task__a.json", task("a"), folder.id)
    store = drive.store()

    document = await store.get("task", "a")

    assert document == StoredDocument(kind="task", doc_id="a", etag=item.etag, data=task("a"))
    assert await store.get("task", "missing") is None
    drive.remove(item)
    assert await store.get("task", "a") is None


@pytest.mark.asyncio
async def test_own_writes_come_back_with_the_same_etag(drive: FakeDrive) -> None:
    planning_folder(drive)
    store = drive.store()
    start = await store.changes(None)

    created = await store.create("task", "a", task("a"))
    changes = await store.changes(start.cursor)

    assert by_key(changes.documents)["task", "a"].etag == created.etag


# ------------------------------------------------------------------ errors ----


@pytest.mark.asyncio
async def test_401_asks_to_sign_in_again(drive: FakeDrive) -> None:
    drive.fail(r"/root:/", lambda _request: error(401, "InvalidAuthenticationToken"))

    with pytest.raises(StoreUnavailableError, match="Reconnexion Microsoft requise") as caught:
        await drive.store().open()

    assert failure_status(caught.value).state == "auth_required"


@pytest.mark.asyncio
async def test_signed_out_poste_asks_to_sign_in_again(drive: FakeDrive) -> None:
    class SignedOut:
        async def access_token(self) -> str:
            raise AuthError("Connexion Microsoft requise.")

    store = DrivePlanningStore(graph=drive.client(SignedOut()))

    with pytest.raises(StoreUnavailableError, match="Reconnexion") as caught:
        await store.changes(None)

    assert failure_status(caught.value).state == "auth_required"


@pytest.mark.asyncio
async def test_403_reports_a_refused_access(drive: FakeDrive) -> None:
    drive.fail(r"/root:/", lambda _request: error(403, "accessDenied"))

    with pytest.raises(StoreUnavailableError, match="accès refusé") as caught:
        await drive.store().open()

    assert failure_status(caught.value).state == "forbidden"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", "timeout", "503", "msal-offline"])
async def test_unreachable_microsoft_reads_offline(drive: FakeDrive, failure: str) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("getaddrinfo failed", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        return error(503, "serviceNotAvailable")

    class Offline:
        async def access_token(self) -> str:
            raise AuthError("Microsoft ne repond pas.") from OSError("getaddrinfo failed")

    if failure == "msal-offline":
        store = DrivePlanningStore(graph=drive.client(Offline()))
    else:
        drive.fail(r".", broken)
        store = drive.store()

    with pytest.raises(StoreUnavailableError, match="injoignable") as caught:
        await store.changes(None)

    assert failure_status(caught.value).state == "offline"


@pytest.mark.asyncio
async def test_other_graph_errors_are_raised_unchanged(drive: FakeDrive) -> None:
    drive.fail(r"/root:/", lambda _request: error(400, "invalidRequest"))

    with pytest.raises(GraphError) as caught:
        await drive.store().open()

    assert caught.value.status_code == 400


# ------------------------------------------------------------- settings ----


def test_cache_key_follows_the_drive_and_the_folder() -> None:
    poste = PlanningPosteConfig(backend="onedrive", onedrive_drive_id="b!A")
    key = poste.cache_key()

    assert key.startswith("onedrive-")
    assert (
        PlanningPosteConfig(
            backend="onedrive",
            onedrive_drive_id="b!a",
            onedrive_folder="projectflow planning/",
        ).cache_key()
        == key
    )
    assert PlanningPosteConfig(backend="onedrive", onedrive_drive_id="b!B").cache_key() != key
    assert (
        PlanningPosteConfig(
            backend="onedrive",
            onedrive_drive_id="b!A",
            onedrive_folder="Autre",
        ).cache_key()
        != key
    )
    assert PlanningPosteConfig(backend="local").cache_key().startswith("local-")


def test_onedrive_folder_defaults_and_is_normalised() -> None:
    assert PlanningPosteConfig().onedrive_folder == DEFAULT_ONEDRIVE_FOLDER
    assert PlanningPosteConfig(onedrive_folder="  ").onedrive_folder == DEFAULT_ONEDRIVE_FOLDER
    assert PlanningPosteConfig(onedrive_folder="/A / B/").onedrive_folder == "A/B"


# ---------------------------------------------------------------- services ----


@dataclass
class Providers:
    made: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **options: Any) -> Token:
        self.made.append(options)
        return Token()


@pytest.mark.asyncio
async def test_services_open_onedrive_with_the_repertoire_connection(
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = Providers()
    monkeypatch.setattr(services, "MsalAccessTokenProvider", providers)
    registered: list[GraphClient] = []
    poste = PlanningPosteConfig(backend="onedrive", onedrive_folder="Planning atelier")

    store = await services.create_planning_store(
        AppConfig(),
        poste,
        application_settings=ApplicationSettings(microsoft_client_id=CLIENT_ID),
        graph_factory=drive.client,
        register_client=registered.append,
    )

    assert isinstance(store, DrivePlanningStore)
    assert providers.made == [
        {"client_id": CLIENT_ID, "scopes": GRAPH_SCOPES, "allow_interactive": False},
    ]
    assert len(registered) == 1
    assert drive.child("root", "Planning atelier") is not None
    assert poste.onedrive_drive_id == DRIVE_ID
    await store.aclose()


@pytest.mark.asyncio
async def test_services_onedrive_without_connector_is_a_configuration_error() -> None:
    with pytest.raises(ConfigError, match="connecteur Microsoft"):
        await services.create_planning_store(
            AppConfig(),
            PlanningPosteConfig(backend="onedrive"),
            application_settings=ApplicationSettings(microsoft_client_id=""),
        )


@pytest.mark.asyncio
async def test_services_onedrive_signed_out_is_unavailable(
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SignedOut:
        def __init__(self, **_options: Any) -> None:
            pass

        async def access_token(self) -> str:
            raise AuthError("Connexion Microsoft requise.")

    monkeypatch.setattr(services, "MsalAccessTokenProvider", SignedOut)

    with pytest.raises(StoreUnavailableError) as caught:
        await services.create_planning_store(
            AppConfig(),
            PlanningPosteConfig(backend="onedrive"),
            application_settings=ApplicationSettings(microsoft_client_id=CLIENT_ID),
            graph_factory=drive.client,
        )

    assert failure_status(caught.value).state == "auth_required"
    assert drive.requests == []


# ------------------------------------------------------------ two postes ----


@pytest.mark.asyncio
async def test_two_postes_share_the_planning_through_the_onedrive_folder(
    drive: FakeDrive,
) -> None:
    from test_planning_service import ME, OTHER, edited, make_clock, new_task  # noqa: PLC0415

    from projectflow.core.planning.changes import DocumentChange  # noqa: PLC0415
    from projectflow.core.planning.service import PlanningService  # noqa: PLC0415
    from projectflow.core.planning.sync import SyncEngine  # noqa: PLC0415

    mine = PlanningService(drive.store(), None, ME, clock=make_clock())
    theirs = PlanningService(drive.store(), None, OTHER, clock=make_clock())
    assert await mine.load()
    assert await theirs.load()
    my_engine = SyncEngine(mine, mine._store)  # noqa: SLF001
    their_engine = SyncEngine(theirs, theirs._store)  # noqa: SLF001

    task_ = new_task("Garde-corps")
    mine.apply((DocumentChange.creation(task_),), label="Nouvelle tâche")
    await my_engine.sync_once()
    await their_engine.sync_once()

    assert theirs.snapshot.tasks[task_.id].title == "Garde-corps"
    folder = drive.child("root", DEFAULT_ONEDRIVE_FOLDER)
    assert folder is not None
    assert drive.child(folder.id, f"task__{task_.id}.json") is not None

    # Both edit the same task: the second write meets a 412 and is merged.
    mine.apply((edited(mine.snapshot.tasks[task_.id], title="Escalier"),), label="Titre")
    theirs.apply((edited(theirs.snapshot.tasks[task_.id], notes="Galva"),), label="Notes")
    await my_engine.sync_once()
    await their_engine.sync_once()
    await my_engine.sync_once()

    for service in (mine, theirs):
        merged = service.snapshot.tasks[task_.id]
        assert (merged.title, merged.notes) == ("Escalier", "Galva")
        assert service.pending == 0
