from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError
from projectflow.updates import (
    GitHubReleaseChecker,
    ReleaseAsset,
    UpdateDownloader,
    UpdateInfo,
    prepare_install_plan,
    select_platform_asset,
)


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
                "body": "- Correction du repertoire chantier",
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
    assert update.release_notes == "- Correction du repertoire chantier"
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


def test_select_platform_asset_picks_windows_exe() -> None:
    update = UpdateInfo(
        current_version="0.1.0",
        latest_version="0.2.0",
        release_url="https://github.example/release",
        assets=(
            ReleaseAsset(
                name="ProjectFlow Automator.dmg",
                download_url="https://x/app.dmg",
                size=1,
            ),
            ReleaseAsset(name="ProjectFlowAutomator.exe", download_url="https://x/app.exe", size=1),
        ),
    )

    asset = select_platform_asset(update, system_name="Windows")

    assert asset is not None
    assert asset.name == "ProjectFlowAutomator.exe"


def test_select_platform_asset_picks_macos_dmg() -> None:
    update = UpdateInfo(
        current_version="0.1.0",
        latest_version="0.2.0",
        release_url="https://github.example/release",
        assets=(
            ReleaseAsset(name="ProjectFlowAutomator.exe", download_url="https://x/app.exe", size=1),
            ReleaseAsset(
                name="ProjectFlow Automator.dmg",
                download_url="https://x/app.dmg",
                size=1,
            ),
        ),
    )

    asset = select_platform_asset(update, system_name="Darwin")

    assert asset is not None
    assert asset.name == "ProjectFlow Automator.dmg"


@pytest.mark.asyncio
async def test_update_downloader_writes_asset(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://github.example/app.exe"
        return httpx.Response(200, content=b"binary")

    downloader = UpdateDownloader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asset = ReleaseAsset(
        name="ProjectFlowAutomator.exe",
        download_url="https://github.example/app.exe",
        size=6,
    )

    path = await downloader.download(asset, version="0.2.0", destination_dir=tmp_path)

    assert path.name == "ProjectFlowAutomator.exe"
    assert path.read_bytes() == b"binary"


def test_prepare_windows_install_plan_writes_script(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlowAutomator.exe"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(
        asset_path,
        current_executable=tmp_path / "current.exe",
        process_id=123,
        script_dir=tmp_path,
        system_name="Windows",
    )

    assert plan.should_quit_app is True
    assert plan.command[0] == "powershell.exe"
    assert "-WindowStyle" in plan.command
    script_path = tmp_path / "install_projectflow_update.ps1"
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    assert "install_projectflow_update.log" in script
    assert "Copy attempt" in script
    assert "Start-Process -FilePath $Target" in script
    assert "Relaunch-Elevated" in script


def test_prepare_macos_install_plan_opens_dmg(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlow Automator.dmg"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(asset_path, system_name="Darwin")

    assert plan.should_quit_app is False
    assert plan.command == ("open", str(asset_path))
