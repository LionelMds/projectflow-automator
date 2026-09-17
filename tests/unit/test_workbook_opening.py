from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
import pytest

from projectflow.config import RepertoireChantierConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.workbook_opening import excel_document_uri, resolve_workbook_open_url

DIRECT_URL = "https://example.sharepoint.com/sites/Projects/Documents/Repertoire.xlsx"
CONFIGURED = RepertoireChantierConfig(drive_id="drive", item_id="item")


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "test-bearer-kept-in-header"


@asynccontextmanager
async def metadata_graph(
    *responses: dict[str, object],
) -> AsyncIterator[tuple[GraphClient, list[httpx.Request]]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "graph.microsoft.com"
        assert "workbook" not in request.url.path.casefold()
        assert "workbook-session-id" not in request.headers
        assert request.headers["Authorization"] == "Bearer test-bearer-kept-in-header"
        assert "test-bearer" not in str(request.url)
        assert not request.content
        requests.append(request)
        return httpx.Response(200, json=responses[len(requests) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        graph = GraphClient(token_provider=FakeTokenProvider(), http_client=http_client)
        yield graph, requests


@pytest.mark.asyncio
async def test_configured_ids_read_metadata_and_prefer_webdav_without_excel_session() -> None:
    config = RepertoireChantierConfig(drive_id="drive/id", item_id="item id")
    async with metadata_graph(
        {
            "name": "Repertoire.xlsx",
            "webDavUrl": DIRECT_URL,
            "webUrl": "https://example.sharepoint.com/other.xlsx",
            "@microsoft.graph.downloadUrl": "https://download.example.test/file?token=secret",
        },
    ) as (graph, requests):
        result = await resolve_workbook_open_url(graph, config)

    assert result == DIRECT_URL
    assert len(requests) == 1
    assert requests[0].url.raw_path.startswith(b"/v1.0/drives/drive%2Fid/items/item%20id?")
    assert set(requests[0].url.params["$select"].split(",")) == {
        "name",
        "webDavUrl",
        "webUrl",
        "parentReference",
        "folder",
    }
    assert "token" not in excel_document_uri(result)


@pytest.mark.asyncio
async def test_opaque_share_is_resolved_before_document_metadata() -> None:
    shared_url = "https://1drv.ms/x/s!opaque-share?e=share-code"
    config = RepertoireChantierConfig(display_path=shared_url)
    async with metadata_graph(
        {
            "id": "item",
            "name": "Repertoire.xlsx",
            "parentReference": {"driveId": "drive"},
            "webUrl": "https://example.sharepoint.com/_layouts/15/Doc.aspx?sourcedoc=id",
        },
        {"name": "Repertoire.xlsx", "webDavUrl": DIRECT_URL},
    ) as (graph, requests):
        result = await resolve_workbook_open_url(graph, config)

    share_token = "u!" + base64.urlsafe_b64encode(shared_url.encode()).decode().rstrip("=")
    assert [request.url.path for request in requests] == [
        f"/v1.0/shares/{share_token}/driveItem",
        "/v1.0/drives/drive/items/item",
    ]
    assert result == DIRECT_URL
    assert "share-code" not in excel_document_uri(result)


@pytest.mark.parametrize("webdav", [None, "http://example.sharepoint.com/Repertoire.xlsx"])
@pytest.mark.asyncio
async def test_valid_direct_web_url_is_used_when_webdav_is_unavailable(webdav: str | None) -> None:
    async with metadata_graph(
        {"name": "Repertoire.xlsx", "webDavUrl": webdav, "webUrl": DIRECT_URL},
    ) as (graph, requests):
        result = await resolve_workbook_open_url(graph, CONFIGURED)

    assert result == DIRECT_URL
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_browser_document_page_resolves_parent_and_encodes_filename_once() -> None:
    filename = "Répertoire 100% #1.xlsx"
    folder_url = "https://example.sharepoint.com/sites/Projets/Documents%20partages/"
    async with metadata_graph(
        {
            "name": filename,
            "webUrl": "https://example.sharepoint.com/_layouts/15/Doc.aspx?sourcedoc=id",
            "parentReference": {"id": "parent/id"},
        },
        {"webUrl": folder_url},
    ) as (graph, requests):
        result = await resolve_workbook_open_url(graph, CONFIGURED)

    assert result == folder_url + "R%C3%A9pertoire%20100%25%20%231.xlsx"
    assert excel_document_uri(result) == "ms-excel:ofe|u|" + result
    assert requests[1].url.raw_path == b"/v1.0/drives/drive/items/parent%2Fid?$select=webUrl"


@pytest.mark.parametrize(
    "folder_url",
    [
        "https://example.sharepoint.com/Forms/AllItems.aspx?id=folder",
        "https://example.sharepoint.com/Documents?view=folder",
        "https://example.sharepoint.com/Documents#folder",
        "https://example.sharepoint.com/Forms/AllItems.aspx",
        "https://example.sharepoint.com/Forms/AllItems.ASPX",
        "https://example.sharepoint.com/Forms/AllItems%2Easpx",
        "https://untrusted.example.test/Documents",
    ],
)
@pytest.mark.asyncio
async def test_parent_must_be_a_trusted_folder_url(folder_url: str) -> None:
    async with metadata_graph(
        {"name": "Repertoire.xlsx", "parentReference": {"id": "parent"}},
        {"webUrl": folder_url},
    ) as (graph, requests):
        with pytest.raises(ConfigError):
            await resolve_workbook_open_url(graph, CONFIGURED)

    assert len(requests) == 2


@pytest.mark.parametrize(
    "metadata",
    [
        {"name": "Repertoire.xlsx", "folder": {}, "webUrl": DIRECT_URL},
        {"name": "Repertoire non fusionne.xlsx", "webUrl": DIRECT_URL},
        {"name": "Repertoire.xlsx", "webUrl": "https://untrusted.test/Repertoire.xlsx"},
        {"name": "Repertoire.xlsx", "@microsoft.graph.downloadUrl": DIRECT_URL + "?token=secret"},
        {"webUrl": DIRECT_URL},
    ],
)
@pytest.mark.asyncio
async def test_rejects_folders_recovery_copies_and_unusable_metadata(
    metadata: dict[str, object],
) -> None:
    async with metadata_graph(metadata) as (graph, requests):
        with pytest.raises(ConfigError):
            await resolve_workbook_open_url(graph, CONFIGURED)

    assert len(requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        DIRECT_URL,
        "https://d.docs.live.net/account/Documents/Repertoire.xlsx",
        "https://onedrive.live.com/account/Repertoire.XLSX",
        "https://example.sharepoint.com:443/" + quote("Répertoire #100%.xlsx", safe=""),
    ],
)
def test_excel_document_uri_accepts_direct_microsoft_workbooks(url: str) -> None:
    assert excel_document_uri(url) == "ms-excel:ofe|u|" + url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.sharepoint.com/Repertoire.xlsx",
        "https://example.test/Repertoire.xlsx",
        "https://example.sharepoint.com.evil.test/Repertoire.xlsx",
        "https://user:password@example.sharepoint.com/Repertoire.xlsx",
        "https://user@example.sharepoint.com/Repertoire.xlsx",
        "https://example.sharepoint.com/Repertoire.csv",
        "https://example.sharepoint.com/Doc.aspx?file=Repertoire.xlsx",
        "https://example.sharepoint.com/Repertoire.xlsx|command",
        "https://example.sharepoint.com/Repertoire.xlsx?query=|command",
        "https://example.sharepoint.com/Repertoire.xlsx\r\n",
        "https://example.sharepoint.com:444/Repertoire.xlsx",
        "https://example.sharepoint.com:bad-port/Repertoire.xlsx",
    ],
)
def test_excel_document_uri_rejects_untrusted_or_malformed_targets(url: str) -> None:
    with pytest.raises(ConfigError):
        excel_document_uri(url)
