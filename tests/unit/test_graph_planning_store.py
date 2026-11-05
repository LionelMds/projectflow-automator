from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from projectflow.auth.msal_client import PLANNING_GRAPH_SCOPES
from projectflow.exceptions import ConfigError, GraphError
from projectflow.graph.client import GraphClient
from projectflow.graph.planning_store import (
    BULK_FIELDS_THRESHOLD,
    FIELDS_EXPAND,
    PLANNING_COLUMNS,
    RESYNC_APPLY_DIFFERENCES,
    RESYNC_UPLOAD_DIFFERENCES,
    GraphListPlanningStore,
    PlanningConflictError,
    PlanningListRef,
    PlanningRecord,
    create_planning_list,
    find_list,
    group_site_id,
    manual_list_instructions,
    planning_column_problems,
    site_id_from_url,
)

GRAPH = "https://graph.microsoft.com/v1.0"
ITEMS = "/v1.0/sites/site-1/lists/list-1/items"
DELTA = f"{ITEMS}/delta"
ETAG_1 = '"{11111111-2222-3333-4444-555555555555},1"'
ETAG_2 = '"{11111111-2222-3333-4444-555555555555},2"'
Handler = Callable[[httpx.Request], httpx.Response]


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


def _graph(handler: Handler) -> GraphClient:
    return GraphClient(
        token_provider=FakeTokenProvider(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _store(handler: Handler, *, page_size: int | None = None) -> GraphListPlanningStore:
    return GraphListPlanningStore(
        graph=_graph(handler),
        list_ref=PlanningListRef(site_id="site-1", list_id="list-1"),
        page_size=page_size,
    )


def _json(request: httpx.Request) -> dict[str, Any]:
    payload = json.loads(request.content.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


def _item(
    item_id: str,
    data: dict[str, Any] | None = None,
    *,
    etag: str = ETAG_1,
    kind: str = "task",
    with_fields: bool = True,
) -> dict[str, Any]:
    item: dict[str, Any] = {"id": item_id, "eTag": etag}
    if with_fields:
        item["fields"] = {
            "@odata.etag": etag,
            "PfKind": kind,
            "PfDocId": f"doc-{item_id}",
            "PfProject": "2026-5245",
            "PfData": json.dumps(data if data is not None else {"n": item_id}),
        }
    return item


def test_planning_scopes_only_need_sites_read_write() -> None:
    assert PLANNING_GRAPH_SCOPES == ("Sites.ReadWrite.All",)


@pytest.mark.asyncio
async def test_changes_full_enumeration_follows_pages_with_fields_expanded() -> None:
    requests: list[httpx.Request] = []
    next_link = f"{GRAPH}/sites/site-1/lists/list-1/items/delta?$skiptoken=page2"
    delta_link = f"{GRAPH}/sites/site-1/lists/list-1/items/delta?token=abc"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "$skiptoken" in str(request.url):
            return httpx.Response(
                200,
                json={"value": [_item("2", {"name": "Équipe é"})], "@odata.deltaLink": delta_link},
            )
        return httpx.Response(
            200,
            json={"value": [_item("1", {"seq": 1})], "@odata.nextLink": next_link},
        )

    store = _store(handler, page_size=50)
    changes = await store.changes(None)
    await store.aclose()

    first = requests[0].url
    assert first.path == DELTA
    assert first.params["$expand"] == FIELDS_EXPAND
    assert first.params["$top"] == "50"
    assert "token" not in first.params
    assert requests[1].url == httpx.URL(next_link)
    assert [record.item_id for record in changes.records] == ["1", "2"]
    assert changes.records[0] == PlanningRecord(
        item_id="1",
        etag=ETAG_1,
        kind="task",
        doc_id="doc-1",
        data={"seq": 1},
        project="2026-5245",
    )
    assert changes.records[1].data == {"name": "Équipe é"}
    assert changes.delta_link == delta_link
    assert changes.full is True
    assert changes.drops_missing_local is True
    assert changes.resync_reason == ""
    assert changes.resynced is False
    assert changes.requests == 2
    assert changes.fields_fetched == 0
    assert FIELDS_EXPAND == "fields($select=PfKind,PfDocId,PfProject,PfData)"


@pytest.mark.asyncio
async def test_changes_from_delta_link_keeps_last_occurrence_and_maps_deletions() -> None:
    requests: list[httpx.Request] = []
    delta_link = f"{GRAPH}/sites/site-1/lists/list-1/items/delta?token=old"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [
                    _item("1", {"v": 1}, etag=ETAG_1),
                    {"id": "2", "deleted": {"state": "deleted"}},
                    _item("1", {"v": 2}, etag=ETAG_2),
                    {"id": "", "fields": {}},
                    "not-an-item",
                ],
                "@odata.deltaLink": f"{GRAPH}/sites/site-1/lists/list-1/items/delta?token=new",
            },
        )

    store = _store(handler)
    changes = await store.changes(delta_link)

    assert len(requests) == 1
    assert str(requests[0].url) == delta_link
    assert [record.item_id for record in changes.records] == ["2", "1"]
    deleted, updated = changes.records
    assert deleted.deleted is True
    assert deleted.data is None
    assert deleted.etag == ""
    assert updated.data == {"v": 2}
    assert updated.etag == ETAG_2
    assert changes.full is False
    assert changes.drops_missing_local is False
    assert changes.delta_link.endswith("token=new")


@pytest.mark.asyncio
async def test_changes_reads_column_values_missing_from_delta_with_one_get_each() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == DELTA:
            return httpx.Response(
                200,
                json={
                    "value": [
                        _item("1", with_fields=False),
                        _item("2", with_fields=False),
                        {"id": "3", "fields": {"PfKind": "task", "PfData": "{}"}},
                    ],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=t",
                },
            )
        if path == f"{ITEMS}/1":
            assert request.url.params["$expand"] == FIELDS_EXPAND
            return httpx.Response(200, json=_item("1", {"seq": 7}, etag=ETAG_2))
        if path == f"{ITEMS}/3":
            return httpx.Response(200, json=_item("3", {"seq": 3}))
        return httpx.Response(404, json={"error": {"code": "itemNotFound"}})

    store = _store(handler)
    changes = await store.changes(None)

    by_id = {record.item_id: record for record in changes.records}
    assert by_id["1"].data == {"seq": 7}
    assert by_id["1"].etag == ETAG_2
    assert by_id["2"].deleted is True
    assert by_id["3"].data == {"seq": 3}
    assert changes.fields_fetched == 3
    assert changes.requests == 4


@pytest.mark.asyncio
async def test_changes_cancel_the_other_reads_when_one_fails() -> None:
    cancelled: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == DELTA:
            return httpx.Response(
                200,
                json={
                    "value": [_item(item_id, with_fields=False) for item_id in ("1", "2", "3")],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=t",
                },
            )
        if path == f"{ITEMS}/1":
            await asyncio.sleep(0)
            return httpx.Response(403, json={"error": {"code": "accessDenied"}})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(path)
            raise
        raise AssertionError(path)

    store = GraphListPlanningStore(
        graph=GraphClient(
            token_provider=FakeTokenProvider(),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
        list_ref=PlanningListRef(site_id="site-1", list_id="list-1"),
    )
    with pytest.raises(GraphError) as caught:
        await asyncio.wait_for(store.changes(None), timeout=5)

    assert caught.value.status_code == 403
    assert sorted(cancelled) == [f"{ITEMS}/2", f"{ITEMS}/3"]


@pytest.mark.asyncio
async def test_changes_lists_items_once_when_many_lack_column_values() -> None:
    requests: list[httpx.Request] = []
    count = BULK_FIELDS_THRESHOLD + 5
    ids = [str(index) for index in range(1, count + 1)]
    second_page = f"{GRAPH}/sites/site-1/lists/list-1/items?$skiptoken=2"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == DELTA:
            return httpx.Response(
                200,
                json={
                    "value": [_item(item_id, with_fields=False) for item_id in ids],
                    "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=t",
                },
            )
        if request.url.path == ITEMS and "$skiptoken" in str(request.url):
            return httpx.Response(200, json={"value": [_item(item_id) for item_id in ids[10:-1]]})
        if request.url.path == ITEMS:
            assert request.url.params["$expand"] == FIELDS_EXPAND
            return httpx.Response(
                200,
                json={
                    "value": [_item(item_id) for item_id in ids[:10]] + [_item("999")],
                    "@odata.nextLink": second_page,
                },
            )
        raise AssertionError(request.url)

    store = _store(handler)
    changes = await store.changes(None)

    assert len(requests) == 3
    assert len(changes.records) == count
    assert all(record.data == {"n": record.item_id} for record in changes.records[:-1])
    # Absent from the listing: deleted since the delta was computed.
    assert changes.records[-1].deleted is True
    assert changes.fields_fetched == count


@pytest.mark.asyncio
async def test_changes_restart_a_full_enumeration_after_410() -> None:
    requests: list[httpx.Request] = []
    stale_link = f"{GRAPH}/sites/site-1/lists/list-1/items/delta?token=expired"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "token=expired" in str(request.url):
            return httpx.Response(
                410,
                json={"error": {"code": "resyncChangesApplyDifferences", "message": "resync"}},
                headers={"Location": f"{GRAPH}/sites/site-1/lists/list-1/items/delta?token=x"},
            )
        return httpx.Response(
            200,
            json={"value": [_item("1")], "@odata.deltaLink": "https://graph/delta?token=fresh"},
        )

    store = _store(handler)
    changes = await store.changes(stale_link)

    assert len(requests) == 2
    assert "token" not in requests[1].url.params
    assert requests[1].url.params["$expand"] == FIELDS_EXPAND
    assert changes.resynced is True
    assert changes.full is True
    assert changes.resync_reason == RESYNC_APPLY_DIFFERENCES
    assert changes.uploads_local_differences is False
    assert changes.drops_missing_local is True
    assert changes.delta_link.endswith("token=fresh")


@pytest.mark.asyncio
async def test_changes_after_upload_differences_resync_keep_local_documents() -> None:
    codes = iter([RESYNC_UPLOAD_DIFFERENCES, RESYNC_APPLY_DIFFERENCES])

    def handler(_request: httpx.Request) -> httpx.Response:
        code = next(codes, None)
        if code is not None:
            return httpx.Response(410, json={"error": {"code": code, "message": "resync"}})
        return httpx.Response(
            200,
            json={"value": [_item("1")], "@odata.deltaLink": "https://graph/delta?token=fresh"},
        )

    store = _store(handler)
    changes = await store.changes("https://graph.microsoft.com/v1.0/delta?token=expired")

    assert changes.resynced is True
    assert changes.full is True
    # A later ApplyDifferences never overrides an UploadDifferences request.
    assert changes.resync_reason == RESYNC_UPLOAD_DIFFERENCES
    assert changes.uploads_local_differences is True
    assert changes.drops_missing_local is False


@pytest.mark.asyncio
async def test_changes_give_up_after_repeated_410() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"error": {"code": "resyncChangesUploadDifferences"}})

    store = _store(handler)
    with pytest.raises(GraphError) as caught:
        await store.changes("https://graph.microsoft.com/v1.0/delta?token=old")

    assert caught.value.status_code == 410


@pytest.mark.asyncio
async def test_changes_propagate_other_errors() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "accessDenied"}})

    store = _store(handler)
    with pytest.raises(GraphError) as caught:
        await store.changes(None)

    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_changes_drop_expand_when_delta_refuses_it() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == DELTA and "$expand" in request.url.params:
            return httpx.Response(400, json={"error": {"code": "invalidRequest"}})
        if request.url.path == DELTA:
            return httpx.Response(
                200,
                json={
                    "value": [_item("1", with_fields=False)],
                    "@odata.deltaLink": "https://graph/delta?token=t",
                },
            )
        return httpx.Response(200, json=_item("1", {"ok": True}))

    store = _store(handler)
    changes = await store.changes(None)

    assert store.expands_fields is False
    assert changes.records[0].data == {"ok": True}
    assert [request.url.path for request in requests] == [DELTA, DELTA, f"{ITEMS}/1"]
    assert "$expand" not in store.delta_url()


@pytest.mark.asyncio
async def test_changes_keep_expand_when_the_retry_without_it_also_fails() -> None:
    requests: list[httpx.Request] = []
    statuses = iter([400, 400])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status = next(statuses, 200)
        if status != 200:
            return httpx.Response(status, json={"error": {"code": "invalidRequest"}})
        return httpx.Response(
            200,
            json={"value": [_item("1", {"ok": 1})], "@odata.deltaLink": "https://g/d?token=t"},
        )

    store = _store(handler)
    with pytest.raises(GraphError) as caught:
        await store.changes(None)
    assert caught.value.status_code == 400
    assert store.expands_fields is True

    changes = await store.changes(None)

    assert "$expand" not in requests[1].url.params
    assert requests[2].url.params["$expand"] == FIELDS_EXPAND
    assert changes.records[0].data == {"ok": 1}
    assert store.expands_fields is True


@pytest.mark.asyncio
async def test_changes_without_any_link_is_an_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    store = _store(handler)
    with pytest.raises(GraphError, match="lien de suivi"):
        await store.changes(None)


@pytest.mark.asyncio
async def test_latest_delta_link_requests_token_latest() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": "https://g/d?token=now"})

    store = _store(handler)

    assert await store.latest_delta_link() == "https://g/d?token=now"
    assert requests[0].url.params["token"] == "latest"
    assert requests[0].url.params["$expand"] == FIELDS_EXPAND


@pytest.mark.asyncio
async def test_create_posts_the_four_columns_as_compact_json() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "42", "eTag": ETAG_1})

    store = _store(handler)
    record = await store.create("resource", " doc-42 ", {"name": "Équipe é", "n": [1, 2]}, " P1 ")

    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == ITEMS
    assert _json(request) == {
        "fields": {
            "Title": "resource:doc-42",
            "PfKind": "resource",
            "PfDocId": "doc-42",
            "PfProject": "P1",
            "PfData": '{"name":"Équipe é","n":[1,2]}',
        },
    }
    assert record == PlanningRecord(
        item_id="42",
        etag=ETAG_1,
        kind="resource",
        doc_id="doc-42",
        data={"name": "Équipe é", "n": [1, 2]},
        project="P1",
    )


@pytest.mark.asyncio
async def test_create_reads_the_etag_when_the_response_has_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "42"})
        assert request.url.path == f"{ITEMS}/42"
        assert request.url.params["$select"] == "id,eTag"
        return httpx.Response(200, json={"id": "42", "eTag": ETAG_2})

    store = _store(handler)
    record = await store.create("probe", "p", {})

    assert record.etag == ETAG_2


@pytest.mark.asyncio
async def test_create_rejects_invalid_documents() -> None:
    store = _store(lambda _request: httpx.Response(500))

    with pytest.raises(ValueError, match="inconnu"):
        await store.create("vehicle", "doc", {})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="manquant"):
        await store.create("task", "  ", {})


@pytest.mark.asyncio
async def test_create_requires_an_item_id() -> None:
    store = _store(lambda _request: httpx.Response(201, json={"eTag": ETAG_1}))

    with pytest.raises(GraphError, match="identifiant"):
        await store.create("task", "doc", {})


@pytest.mark.asyncio
async def test_update_sends_if_match_and_returns_the_etag_of_the_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"@odata.etag": ETAG_2, "PfData": "{}"})

    store = _store(handler)
    record = PlanningRecord(item_id="7", etag=ETAG_1, kind="task", doc_id="d", data={})
    new_etag = await store.update(record, ETAG_1, {"seq": 2}, project="2026-1")

    request = requests[0]
    assert request.method == "PATCH"
    assert request.url.path == f"{ITEMS}/7/fields"
    assert request.headers["If-Match"] == ETAG_1
    assert _json(request) == {"PfData": '{"seq":2}', "PfProject": "2026-1"}
    assert new_etag == ETAG_2


@pytest.mark.asyncio
async def test_update_reads_the_new_etag_when_the_response_has_none() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json={"PfData": "{}"})
        return httpx.Response(200, json={"id": "7", "eTag": ETAG_2})

    store = _store(handler)
    new_etag = await store.update("7", ETAG_1, {"seq": 3})

    assert _json(requests[0]) == {"PfData": '{"seq":3}'}
    assert requests[1].method == "GET"
    assert requests[1].url.path == f"{ITEMS}/7"
    assert requests[1].url.params["$select"] == "id,eTag"
    assert new_etag == ETAG_2


@pytest.mark.asyncio
async def test_update_fails_when_no_etag_can_be_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"id": "7"})

    store = _store(handler)
    with pytest.raises(GraphError, match="version"):
        await store.update("7", ETAG_1, {})


@pytest.mark.asyncio
async def test_update_with_a_stale_etag_raises_planning_conflict_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(412, json={"error": {"code": "preconditionFailed"}})

    store = _store(handler)
    with pytest.raises(PlanningConflictError) as caught:
        await store.update("7", ETAG_1, {"seq": 1})

    assert isinstance(caught.value, GraphError)
    assert caught.value.status_code == 412
    assert caught.value.error_code == "preconditionFailed"
    assert "autre poste" in str(caught.value)


@pytest.mark.asyncio
async def test_update_propagates_other_errors_and_needs_an_etag() -> None:
    store = _store(lambda _request: httpx.Response(400, json={"error": {"code": "bad"}}))

    with pytest.raises(GraphError) as caught:
        await store.update("7", ETAG_1, {})
    assert not isinstance(caught.value, PlanningConflictError)
    with pytest.raises(ValueError, match="etag"):
        await store.update("7", " ", {})
    with pytest.raises(ValueError, match="element"):
        await store.update(" ", ETAG_1, {})


@pytest.mark.asyncio
async def test_delete_sends_if_match_and_maps_conflicts() -> None:
    requests: list[httpx.Request] = []
    statuses = iter([204, 412, 404, 500])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(next(statuses))

    store = _store(handler)
    await store.delete("7", ETAG_1)
    with pytest.raises(PlanningConflictError):
        await store.delete("7", ETAG_1)
    await store.delete("7", ETAG_2)
    with pytest.raises(GraphError) as caught:
        await store.delete("7", ETAG_2)

    assert requests[0].method == "DELETE"
    assert requests[0].url.path == f"{ITEMS}/7"
    assert requests[0].headers["If-Match"] == ETAG_1
    assert caught.value.status_code == 500


@pytest.mark.asyncio
async def test_get_returns_the_record_or_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json=_item("1", {"x": 1}))
        if request.url.path.endswith("/2"):
            return httpx.Response(
                200, json={"id": "2", "eTag": ETAG_1, "fields": {"PfKind": "task", "PfData": "[1]"}}
            )
        if request.url.path.endswith("/3"):
            return httpx.Response(403)
        return httpx.Response(404)

    store = _store(handler)

    first = await store.get("1")
    assert first is not None
    assert first.data == {"x": 1}
    second = await store.get("2")
    assert second is not None
    assert second.data is None
    assert await store.get("9") is None
    with pytest.raises(GraphError):
        await store.get("3")


@pytest.mark.asyncio
async def test_find_list_follows_pages_and_ignores_case() -> None:
    requests: list[httpx.Request] = []
    next_link = f"{GRAPH}/sites/site-1/lists?$skiptoken=2"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "$skiptoken" in str(request.url):
            return httpx.Response(
                200,
                json={"value": [{"id": "list-9", "displayName": "ProjectFlow Planning PoC"}]},
            )
        return httpx.Response(
            200,
            json={
                "value": [{"id": "docs", "displayName": "Documents"}, {"displayName": "x"}],
                "@odata.nextLink": next_link,
            },
        )

    graph = _graph(handler)

    assert await find_list(graph, "site-1", " projectflow planning poc ") == "list-9"
    assert requests[0].url.path == "/v1.0/sites/site-1/lists"
    assert requests[0].url.params["$select"] == "id,displayName"
    assert await find_list(graph, "site-1", "Absente") is None


@pytest.mark.asyncio
async def test_create_planning_list_posts_the_four_columns() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "list-new", "displayName": "Planning"})

    list_id = await create_planning_list(_graph(handler), "site-1", " Planning ")

    assert list_id == "list-new"
    assert requests[0].url.path == "/v1.0/sites/site-1/lists"
    body = _json(requests[0])
    assert body["displayName"] == "Planning"
    assert body["list"] == {"template": "genericList"}
    assert [column["name"] for column in body["columns"]] == list(PLANNING_COLUMNS)
    assert body["columns"][3]["text"] == {"allowMultipleLines": True, "textType": "plain"}
    assert all(column["text"] == {} for column in body["columns"][:3])


@pytest.mark.asyncio
async def test_create_planning_list_refused_explains_the_manual_creation() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "accessDenied"}})

    with pytest.raises(ConfigError) as caught:
        await create_planning_list(_graph(handler), "site-1", "ProjectFlow Planning PoC")

    message = str(caught.value)
    assert message == manual_list_instructions("ProjectFlow Planning PoC")
    assert "Sites.Manage.All" in message
    assert "« ProjectFlow Planning PoC »" in message
    assert all(column in message for column in PLANNING_COLUMNS)
    assert "Une seule ligne de texte" in message
    assert "Plusieurs lignes de texte" in message
    assert "texte brut" in message
    assert isinstance(caught.value.__cause__, GraphError)


@pytest.mark.asyncio
async def test_create_planning_list_conflict_reuses_the_existing_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
        return httpx.Response(200, json={"value": [{"id": "list-1", "displayName": "Planning"}]})

    assert await create_planning_list(_graph(handler), "site-1", "Planning") == "list-1"


@pytest.mark.asyncio
async def test_create_planning_list_reports_other_failures() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
        return httpx.Response(200, json={"value": []})

    with pytest.raises(GraphError) as caught:
        await create_planning_list(_graph(failing), "site-1", "Planning")
    assert caught.value.status_code == 409
    with pytest.raises(GraphError, match="identifiant"):
        await create_planning_list(_graph(lambda _r: httpx.Response(201, json={})), "s", "P")
    with pytest.raises(ConfigError):
        await create_planning_list(_graph(failing), "site-1", " ")


@pytest.mark.asyncio
async def test_planning_column_problems_flags_missing_and_rich_text_columns() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["$select"] == "name,text"
        if "list-ok" in request.url.path:
            columns = [{"name": name, "text": {}} for name in PLANNING_COLUMNS[:3]] + [
                {"name": "PfData", "text": {"allowMultipleLines": True, "textType": "plain"}},
            ]
        else:
            columns = [
                {"name": "Title", "text": {}},
                {"name": "PfKind", "text": {}},
                {"name": "PfData", "text": {"allowMultipleLines": True, "textType": "richText"}},
            ]
        return httpx.Response(200, json={"value": columns})

    graph = _graph(handler)

    assert await planning_column_problems(graph, "site-1", "list-ok") == ()
    problems = await planning_column_problems(graph, "site-1", "list-bad")
    assert len(problems) == 3
    assert "PfDocId" in problems[0]
    assert "PfProject" in problems[1]
    assert "texte brut" in problems[2]


@pytest.mark.asyncio
async def test_planning_column_problems_flags_columns_of_the_wrong_type() -> None:
    columns = [
        {"name": "PfKind", "text": {"allowMultipleLines": False}},
        {"name": "PfDocId"},
        {"name": "PfProject", "number": {}},
        {"name": "PfData", "text": {"allowMultipleLines": False, "textType": "plain"}},
    ]
    graph = _graph(lambda _request: httpx.Response(200, json={"value": columns}))

    problems = await planning_column_problems(graph, "site-1", "list-1")

    assert len(problems) == 3
    assert all("type de colonne incorrect" in problem for problem in problems)
    assert [problem.split(":")[0] for problem in problems] == [
        "Colonne PfDocId",
        "Colonne PfProject",
        "Colonne PfData",
    ]


@pytest.mark.parametrize(
    "data_column",
    [
        {"name": "PfData", "number": {}},
        {"name": "PfData", "text": {"textType": "plain"}},
        {
            "name": "PfData",
            "text": {
                "allowMultipleLines": True,
                "textType": "plain",
                "appendChangesToExistingText": True,
            },
        },
    ],
)
@pytest.mark.asyncio
async def test_planning_column_problems_requires_plain_multi_line_data(
    data_column: dict[str, Any],
) -> None:
    columns = [{"name": name, "text": {}} for name in PLANNING_COLUMNS[:3]] + [data_column]
    graph = _graph(lambda _request: httpx.Response(200, json={"value": columns}))

    problems = await planning_column_problems(graph, "site-1", "list-1")

    assert len(problems) == 1
    assert problems[0].startswith("Colonne PfData: type de colonne incorrect")


@pytest.mark.asyncio
async def test_planning_column_problems_accepts_data_without_text_type() -> None:
    columns = [{"name": name, "text": {}} for name in PLANNING_COLUMNS[:3]] + [
        {"name": "PfData", "text": {"allowMultipleLines": True, "maxLength": 0}},
    ]
    graph = _graph(lambda _request: httpx.Response(200, json={"value": columns}))

    assert await planning_column_problems(graph, "site-1", "list-1") == ()


@pytest.mark.asyncio
async def test_group_site_id_reads_the_group_root_site() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "group-1" in request.url.path:
            return httpx.Response(200, json={"id": "tenant.sharepoint.com,a,b"})
        return httpx.Response(200, json={})

    graph = _graph(handler)

    assert await group_site_id(graph, " group-1 ") == "tenant.sharepoint.com,a,b"
    assert requests[0].url.path == "/v1.0/groups/group-1/sites/root"
    with pytest.raises(ConfigError, match="site SharePoint"):
        await group_site_id(graph, "group-2")
    with pytest.raises(ConfigError, match="non configure"):
        await group_site_id(graph, " ")


@pytest.mark.parametrize(
    ("url", "expected_path"),
    [
        ("https://tenant.sharepoint.com/sites/xyz", "/v1.0/sites/tenant.sharepoint.com:/sites/xyz"),
        (
            "https://tenant.sharepoint.com/sites/Atelier%20GE/Lists/Planning/AllItems.aspx",
            "/v1.0/sites/tenant.sharepoint.com:/sites/Atelier GE",
        ),
        (
            "https://tenant.sharepoint.com/teams/prod/",
            "/v1.0/sites/tenant.sharepoint.com:/teams/prod",
        ),
        ("https://tenant.sharepoint.com/", "/v1.0/sites/tenant.sharepoint.com"),
    ],
)
@pytest.mark.asyncio
async def test_site_id_from_url_uses_the_server_relative_path(url: str, expected_path: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "site-id"})

    assert await site_id_from_url(_graph(handler), url) == "site-id"
    assert requests[0].url.path == expected_path
    assert requests[0].url.params["$select"] == "id"


@pytest.mark.asyncio
async def test_site_id_from_url_rejects_invalid_or_unknown_sites() -> None:
    graph = _graph(lambda _request: httpx.Response(200, json={}))

    with pytest.raises(ConfigError, match="Adresse SharePoint invalide"):
        await site_id_from_url(graph, "http://tenant.sharepoint.com/sites/x")
    with pytest.raises(ConfigError, match="Adresse SharePoint invalide"):
        await site_id_from_url(graph, "sites/x")
    with pytest.raises(ConfigError, match="introuvable"):
        await site_id_from_url(graph, "https://tenant.sharepoint.com/sites/x")


@pytest.mark.asyncio
async def test_unreadable_documents_come_back_without_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "token=odd" in str(request.url):
            return httpx.Response(200, json={"value": "odd", "@odata.deltaLink": "https://g/d?t=2"})
        return httpx.Response(
            200,
            json={
                "value": [
                    {"id": "1", "eTag": ETAG_1, "fields": {"PfKind": "task", "PfData": "{broken"}},
                    {"id": "2", "eTag": ETAG_1, "fields": {"PfKind": "meta", "PfData": 3}},
                ],
                "@odata.deltaLink": "https://g/d?token=odd",
            },
        )

    store = _store(handler)
    changes = await store.changes(None)
    following = await store.changes(changes.delta_link)

    assert store.list_ref == PlanningListRef(site_id="site-1", list_id="list-1")
    assert [record.data for record in changes.records] == [None, None]
    assert [record.kind for record in changes.records] == ["task", "meta"]
    assert following.records == ()
