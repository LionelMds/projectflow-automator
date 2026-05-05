from __future__ import annotations

import httpx
import pytest

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError
from projectflow.updates import GitHubReleaseChecker


@pytest.mark.asyncio
async def test_update_checker_is_silent_without_repo_settings() -> None:
    checker = GitHubReleaseChecker(ApplicationSettings())

    assert await checker.check(current_version="0.1.0") is None


@pytest.mark.asyncio
async def test_update_checker_returns_newer_release() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.example/repos/balz/projectflow/releases/latest"
        return httpx.Response(
            200,
            json={
                "tag_name": "v0.2.0",
                "html_url": "https://github.example/release",
                "assets": [
                    {
                        "name": "ProjectFlowAutomator.exe",
                        "browser_download_url": "https://github.example/app.exe",
                        "size": 123,
                    },
                ],
            },
        )

    checker = GitHubReleaseChecker(
        ApplicationSettings(github_owner="balz", github_repo="projectflow"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_root="https://api.example",
    )

    update = await checker.check(current_version="0.1.0")

    assert update is not None
    assert update.latest_version == "0.2.0"
    assert update.assets[0].name == "ProjectFlowAutomator.exe"


@pytest.mark.asyncio
async def test_update_checker_returns_none_for_current_release() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={"tag_name": "v0.1.0", "html_url": "https://github.example/release"},
        )

    checker = GitHubReleaseChecker(
        ApplicationSettings(github_owner="balz", github_repo="projectflow"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await checker.check(current_version="0.1.0") is None


@pytest.mark.asyncio
async def test_update_checker_returns_none_when_no_release_exists() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404)

    checker = GitHubReleaseChecker(
        ApplicationSettings(github_owner="balz", github_repo="projectflow"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    assert await checker.check(current_version="0.1.0") is None


@pytest.mark.asyncio
async def test_update_checker_wraps_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(500)

    checker = GitHubReleaseChecker(
        ApplicationSettings(github_owner="balz", github_repo="projectflow"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ProjectFlowError, match="mise a jour"):
        await checker.check(current_version="0.1.0")
