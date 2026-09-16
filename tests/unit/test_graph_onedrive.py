from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest

from projectflow.exceptions import ConfigError
from projectflow.graph import onedrive
from projectflow.graph.client import GraphClient
from projectflow.graph.onedrive import (
    OneDriveItemResolver,
    _relative_onedrive_path,
    _split_first_segment,
)


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


@pytest.fixture(autouse=True)
def isolated_sync_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onedrive, "onedrive_roots", lambda: ())
    monkeypatch.setattr(onedrive, "is_synchronized_path", lambda _path: False)


@pytest.mark.asyncio
async def test_onedrive_resolver_falls_back_to_unique_search_match(tmp_path: Path) -> None:
    workbook_path = tmp_path / "REPERTOIR CHANTIER ET FACTURES.xlsx"
    workbook_path.write_bytes(b"test")

    def handler(request: httpx.Request) -> httpx.Response:
        if "/search(" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "item",
                            "name": workbook_path.name,
                            "size": workbook_path.stat().st_size,
                            "parentReference": {"driveId": "drive"},
                            "webUrl": "https://example.test/item",
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )

        target = await resolver.resolve_workbook(workbook_path)

    assert target.drive_id == "drive"
    assert target.item_id == "item"


@pytest.mark.asyncio
async def test_onedrive_resolver_resolves_file_inside_root_shortcut(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workbook_path = tmp_path / "OneDrive - Balz Metal Sa" / "Entreprise" / "rep.xlsx"
    workbook_path.parent.mkdir(parents=True)
    workbook_path.write_bytes(b"test")
    onedrive_root = workbook_path.parents[1]
    monkeypatch.setattr(onedrive, "onedrive_roots", lambda: (onedrive_root,))

    def handler(request: httpx.Request) -> httpx.Response:
        raw_url = str(request.url)
        if "/me/drive/root:/Entreprise/rep.xlsx:" in raw_url:
            return httpx.Response(404, json={"error": {"message": "missing"}})
        if "/me/drive/root:/Entreprise:" in raw_url:
            return httpx.Response(
                200,
                json={
                    "id": "shortcut",
                    "remoteItem": {
                        "id": "remote-folder",
                        "parentReference": {"driveId": "remote-drive"},
                    },
                },
            )
        if "/drives/remote-drive/items/remote-folder:/rep.xlsx:" in raw_url:
            return httpx.Response(
                200,
                json={
                    "id": "item",
                    "name": "rep.xlsx",
                    "parentReference": {"driveId": "remote-drive"},
                },
            )
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )

        target = await resolver.resolve_workbook(workbook_path)

    assert target.drive_id == "remote-drive"
    assert target.item_id == "item"


@pytest.mark.asyncio
async def test_onedrive_resolver_rejects_ambiguous_search_matches(tmp_path: Path) -> None:
    workbook_path = tmp_path / "repertoire.xlsx"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "item-1",
                        "name": workbook_path.name,
                        "parentReference": {"driveId": "drive"},
                    },
                    {
                        "id": "item-2",
                        "name": workbook_path.name,
                        "parentReference": {"driveId": "drive"},
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )

        with pytest.raises(ConfigError, match="identifier de maniere unique"):
            await resolver.resolve_workbook(workbook_path)


def test_split_first_segment() -> None:
    assert _split_first_segment("Entreprise/rep.xlsx") == ("Entreprise", "rep.xlsx")


def _search_item(item_id: str, *, size: int = 4) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": "repertoire.xlsx",
        "size": size,
        "parentReference": {"driveId": "drive"},
    }


@pytest.mark.asyncio
async def test_search_does_not_select_homonym_by_stale_local_size(tmp_path: Path) -> None:
    workbook_path = tmp_path / "repertoire.xlsx"
    workbook_path.write_bytes(b"test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"value": [_search_item("original", size=8), _search_item("copy")]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match="2 candidat"):
            await resolver.resolve_workbook(workbook_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_item", ["original", "other"])
async def test_search_checks_later_pages_before_accepting_unique_match(
    tmp_path: Path,
    second_item: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "value": [_search_item("original")],
                    "@odata.nextLink": (
                        "https://graph.microsoft.com/v1.0/me/drive/root/search?$skipToken=2"
                    ),
                },
            )
        return httpx.Response(200, json={"value": [_search_item(second_item)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        if second_item == "other":
            with pytest.raises(ConfigError, match="2 candidat"):
                await resolver.resolve_workbook(tmp_path / "repertoire.xlsx")
        else:
            target = await resolver.resolve_workbook(tmp_path / "repertoire.xlsx")
            assert target.item_id == "original"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_search_rejects_pagination_to_another_host(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [_search_item("original")],
                "@odata.nextLink": "https://untrusted.example/next",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match="pagination"):
            await resolver.resolve_workbook(tmp_path / "repertoire.xlsx")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_search_rejects_pagination_loop(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [_search_item("original")],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/drive/root/search?$skipToken=2"
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match="incomplete"):
            await resolver.resolve_workbook(tmp_path / "repertoire.xlsx")
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_sharing_link_uses_exact_cloud_item_with_multiple_local_accounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        onedrive,
        "onedrive_roots",
        lambda: (tmp_path / "Personal", tmp_path / "Business"),
    )
    sharing_url = (
        "https://balz.sharepoint.com/:x:/s/Entreprise/AbCd?e=token&file=R%C3%A9pertoire.xlsx"
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token = "u!" + base64.urlsafe_b64encode(sharing_url.encode()).decode().rstrip("=")
        assert request.url.path == f"/v1.0/shares/{token}/driveItem"
        assert request.headers["Prefer"] == "redeemSharingLink"
        return httpx.Response(200, json=_search_item("original"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        target = await resolver.resolve_workbook(sharing_url)
    assert target.item_id == "original"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_shared_remote_item_uses_source_drive() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "shortcut-id",
                "name": "repertoire.xlsx",
                "parentReference": {"driveId": "my-drive"},
                "remoteItem": _search_item("original"),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        target = await resolver.resolve_workbook("https://1drv.ms/x/some-share")
    assert target.drive_id == "drive"
    assert target.item_id == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "http://balz.sharepoint.com/shared.xlsx",
        "https://balz.sharepoint.com.evil.example/shared.xlsx",
        "https://user:password@balz.sharepoint.com/shared.xlsx",
        "https://example.com/shared.xlsx",
        "repertoire - non fusionné.xlsx",
        "repertoire - non_fusionnee.xlsx",
        "repertoire - Unmerged.xlsx",
        "TemporaryBackupFile/repertoire.xlsx",
    ],
)
async def test_unsafe_sources_are_rejected_before_cloud_access(source: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_search_item("original"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError):
            await resolver.resolve_workbook(source)
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        {**_search_item("copy"), "name": "repertoire - non fusionné.xlsx"},
        {**_search_item("folder"), "folder": {}},
    ],
)
async def test_share_link_does_not_allow_conflict_copy_or_folder(item: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=item)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError):
            await resolver.resolve_workbook("https://1drv.ms/x/some-share")


def test_relative_path_deduplicates_normalized_account_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containing_root = tmp_path / "Work"
    monkeypatch.setattr(
        onedrive,
        "onedrive_roots",
        lambda: (containing_root, containing_root / "Documents" / "..", containing_root),
    )
    assert _relative_onedrive_path(containing_root / "repertoire.xlsx") == "repertoire.xlsx"


@pytest.mark.asyncio
@pytest.mark.parametrize("folder", ["Business", "Local"])
async def test_multiple_onedrive_accounts_require_link_before_cloud_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    folder: str,
) -> None:
    monkeypatch.setattr(
        onedrive,
        "onedrive_roots",
        lambda: (tmp_path / "Personal", tmp_path / "Business"),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_search_item("wrong-account-item"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match=r"Plusieurs comptes OneDrive.*lien de partage"):
            await resolver.resolve_workbook(tmp_path / folder / "repertoire.xlsx")
    assert not requests


@pytest.mark.asyncio
async def test_missing_exact_onedrive_path_does_not_select_homonym_elsewhere(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onedrive, "onedrive_roots", lambda: (tmp_path,))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/search(" in request.url.path:
            return httpx.Response(200, json={"value": [_search_item("wrong-item")]})
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match="lien de partage"):
            await resolver.resolve_workbook(tmp_path / "Projects" / "repertoire.xlsx")
    assert all("/search(" not in request.url.path for request in requests)


@pytest.mark.asyncio
async def test_sharepoint_sync_root_requires_explicit_link_when_mapping_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onedrive, "is_synchronized_path", lambda _path: True)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": [_search_item("wrong-item")]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        resolver = OneDriveItemResolver(
            GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        with pytest.raises(ConfigError, match="lien de partage"):
            await resolver.resolve_workbook(tmp_path / "Company" / "Site" / "repertoire.xlsx")
    assert not requests
