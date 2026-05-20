from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from projectflow.exceptions import ConfigError
from projectflow.graph import onedrive
from projectflow.graph.client import GraphClient
from projectflow.graph.onedrive import OneDriveItemResolver, _split_first_segment


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


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
    monkeypatch.setattr(onedrive, "detect_onedrive_balz_root", lambda: onedrive_root)

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
