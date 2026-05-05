from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError

GITHUB_API_ROOT = "https://api.github.com"


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    assets: tuple[ReleaseAsset, ...]


class GitHubReleaseChecker:
    def __init__(
        self,
        settings: ApplicationSettings,
        *,
        client: httpx.AsyncClient | None = None,
        api_root: str = GITHUB_API_ROOT,
    ) -> None:
        self._settings = settings
        self._client = client
        self._api_root = api_root.rstrip("/")

    async def check(self, *, current_version: str) -> UpdateInfo | None:
        owner = self._settings.github_owner.strip()
        repo = self._settings.github_repo.strip()
        if not owner or not repo:
            return None

        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            response = await client.get(
                f"{self._api_root}/repos/{owner}/{repo}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProjectFlowError("Verification de mise a jour impossible.") from exc
        finally:
            if close_client:
                await client.aclose()

        return _update_from_payload(payload, current_version=current_version)


def _update_from_payload(payload: object, *, current_version: str) -> UpdateInfo | None:
    if not isinstance(payload, dict):
        raise ProjectFlowError("Reponse GitHub Releases invalide.")

    tag_name = payload.get("tag_name")
    html_url = payload.get("html_url")
    if not isinstance(tag_name, str) or not isinstance(html_url, str):
        raise ProjectFlowError("Release GitHub incomplete.")

    latest_version = tag_name.removeprefix("v")
    if _version_key(latest_version) <= _version_key(current_version):
        return None

    assets = payload.get("assets", [])
    release_assets = tuple(
        _asset_from_payload(asset) for asset in assets if isinstance(asset, dict)
    )
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        release_url=html_url,
        assets=release_assets,
    )


def _asset_from_payload(payload: dict[object, object]) -> ReleaseAsset:
    name = payload.get("name")
    download_url = payload.get("browser_download_url")
    size = payload.get("size")
    return ReleaseAsset(
        name=name if isinstance(name, str) else "",
        download_url=download_url if isinstance(download_url, str) else "",
        size=size if isinstance(size, int) else 0,
    )


def _version_key(version: str) -> tuple[int, int, int]:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
