from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError
from projectflow.updates import (
    INSTALL_IN_PROGRESS_SECONDS,
    MAX_INSTALL_ATTEMPTS,
    STAGED_UPDATE_FILE,
    GitHubReleaseChecker,
    ReleaseAsset,
    StagedUpdate,
    UpdateDownloader,
    UpdateInfo,
    begin_staged_install,
    discard_staged_update,
    file_sha256,
    install_in_progress,
    installs_unattended,
    load_staged_update,
    prepare_install_plan,
    save_staged_update,
    select_checksum_asset,
    select_platform_asset,
    silent_updates_supported,
    verify_staged_installer,
)


def _staged(root: Path, *, version: str = "0.2.0", attempts: int = 0) -> StagedUpdate:
    installer = root / version / "ProjectFlowAutomatorSetup.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    installer.write_bytes(b"installer")
    return StagedUpdate(
        version=version,
        installer=installer,
        sha256=sha256(b"installer").hexdigest(),
        attempts=attempts,
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
            ReleaseAsset(
                name="ProjectFlowAutomatorSetup.exe",
                download_url="https://x/setup.exe",
                size=1,
            ),
        ),
    )

    asset = select_platform_asset(update, system_name="Windows")

    assert asset is not None
    assert asset.name == "ProjectFlowAutomatorSetup.exe"


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


def test_select_checksum_asset_prefers_matching_sha256() -> None:
    asset = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe",
        download_url="https://x/setup.exe",
        size=1,
    )
    update = UpdateInfo(
        current_version="0.1.0",
        latest_version="0.2.0",
        release_url="https://github.example/release",
        assets=(
            asset,
            ReleaseAsset(name="SHA256SUMS.txt", download_url="https://x/all.txt", size=1),
            ReleaseAsset(
                name="ProjectFlowAutomatorSetup.exe.sha256",
                download_url="https://x/setup.exe.sha256",
                size=1,
            ),
        ),
    )

    checksum = select_checksum_asset(update, asset)

    assert checksum is not None
    assert checksum.name == "ProjectFlowAutomatorSetup.exe.sha256"


@pytest.mark.asyncio
async def test_update_downloader_writes_asset(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://github.example/app.exe":
            return httpx.Response(200, content=b"binary")
        assert str(request.url) == "https://github.example/app.exe.sha256"
        digest = sha256(b"binary").hexdigest()
        return httpx.Response(200, text=f"{digest}  ProjectFlowAutomatorSetup.exe")

    downloader = UpdateDownloader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asset = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe",
        download_url="https://github.example/app.exe",
        size=6,
    )
    checksum = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe.sha256",
        download_url="https://github.example/app.exe.sha256",
        size=1,
    )

    path = await downloader.download(
        asset,
        version="0.2.0",
        destination_dir=tmp_path,
        checksum_asset=checksum,
    )

    assert path.name == "ProjectFlowAutomatorSetup.exe"
    assert path.read_bytes() == b"binary"
    assert [item.name for item in tmp_path.iterdir()] == ["ProjectFlowAutomatorSetup.exe"]


@pytest.mark.asyncio
async def test_update_downloader_needs_the_checksum_before_the_installer(tmp_path: Path) -> None:
    requested: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"binary")

    downloader = UpdateDownloader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asset = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe",
        download_url="https://github.example/setup.exe",
        size=6,
    )

    with pytest.raises(ProjectFlowError, match="SHA256"):
        await downloader.download(asset, version="0.2.0", destination_dir=tmp_path)

    assert requested == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_update_downloader_rejects_truncated_download(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith(".sha256"):
            return httpx.Response(200, text=sha256(b"binary").hexdigest())
        return httpx.Response(200, content=b"bin")

    downloader = UpdateDownloader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asset = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe",
        download_url="https://github.example/setup.exe",
        size=6,
    )
    checksum = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe.sha256",
        download_url="https://github.example/setup.exe.sha256",
        size=1,
    )

    with pytest.raises(ProjectFlowError, match="incomplet"):
        await downloader.download(
            asset,
            version="0.2.0",
            destination_dir=tmp_path,
            checksum_asset=checksum,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_update_downloader_rejects_bad_checksum(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://github.example/setup.exe":
            return httpx.Response(200, content=b"binary")
        return httpx.Response(200, text=f"{'0' * 64}  ProjectFlowAutomatorSetup.exe")

    downloader = UpdateDownloader(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    asset = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe",
        download_url="https://github.example/setup.exe",
        size=6,
    )
    checksum = ReleaseAsset(
        name="ProjectFlowAutomatorSetup.exe.sha256",
        download_url="https://github.example/setup.exe.sha256",
        size=1,
    )

    with pytest.raises(ProjectFlowError, match="SHA256"):
        await downloader.download(
            asset,
            version="0.2.0",
            destination_dir=tmp_path,
            checksum_asset=checksum,
        )

    # Neither the installer nor its partial file stays behind.
    assert list(tmp_path.iterdir()) == []


def test_prepare_windows_install_plan_runs_installer(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlowAutomatorSetup.exe"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(
        asset_path,
        current_executable=tmp_path / "current.exe",
        process_id=123,
        script_dir=tmp_path,
        system_name="Windows",
    )

    assert plan.should_quit_app is True
    assert plan.command[0] == str(asset_path)
    assert "/SILENT" in plan.command
    assert "/CLOSEAPPLICATIONS" in plan.command
    assert "/RELAUNCH=window" in plan.command
    # A silent run reuses the tasks of the last manual install: never the full reset.
    assert "/MERGETASKS=!cleaninstall" in plan.command


def test_prepare_windows_install_plan_quiet_hides_the_installer(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlowAutomatorSetup.exe"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(
        asset_path,
        system_name="Windows",
        quiet=True,
        relaunch="background",
    )

    assert "/VERYSILENT" in plan.command
    assert "/SILENT" not in plan.command
    assert "/RELAUNCH=background" in plan.command
    # The installer starts the new version itself: Windows must not start it a second time.
    assert "/NORESTARTAPPLICATIONS" in plan.command
    assert "/RESTARTAPPLICATIONS" not in plan.command


def test_silent_updates_only_for_the_built_app() -> None:
    assert silent_updates_supported(system_name="Windows", frozen=True) is True
    assert silent_updates_supported(system_name="Windows", frozen=False) is False
    assert silent_updates_supported(system_name="Darwin", frozen=True) is True
    assert silent_updates_supported(system_name="Darwin", frozen=False) is False
    assert silent_updates_supported(system_name="Linux", frozen=True) is False


def test_installs_unattended_only_the_windows_installer() -> None:
    assert installs_unattended(ReleaseAsset("ProjectFlowAutomatorSetup.exe", "https://x", 1))
    assert not installs_unattended(ReleaseAsset("ProjectFlowAutomator.exe", "https://x", 1))


def test_staged_update_survives_a_restart(tmp_path: Path) -> None:
    staged = _staged(tmp_path)

    save_staged_update(staged, directory=tmp_path)

    assert load_staged_update(current_version="0.1.0", directory=tmp_path) == staged
    assert verify_staged_installer(staged) is True


def test_staged_update_is_forgotten_once_installed(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    save_staged_update(staged, directory=tmp_path)
    kept = tmp_path / "notes.txt"
    kept.write_text("x", encoding="utf-8")

    assert load_staged_update(current_version="0.2.0", directory=tmp_path) is None

    # The installer is deleted with its record; nothing else of the folder is touched.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["notes.txt"]


def test_saving_a_staged_update_deletes_older_downloads(tmp_path: Path) -> None:
    older = _staged(tmp_path, version="0.2.0")
    save_staged_update(older, directory=tmp_path)

    newer = _staged(tmp_path, version="0.3.0")
    save_staged_update(newer, directory=tmp_path)

    assert not older.installer.exists()
    assert load_staged_update(current_version="0.1.0", directory=tmp_path) == newer


def test_staged_update_never_names_a_file_outside_the_downloads(tmp_path: Path) -> None:
    root = tmp_path / "updates"
    root.mkdir()
    for installer in ("../evil.exe", str(tmp_path / "evil.exe"), ""):
        (root / STAGED_UPDATE_FILE).write_text(
            json.dumps({"version": "0.2.0", "installer": installer, "sha256": "00"}),
            encoding="utf-8",
        )

        assert load_staged_update(current_version="0.1.0", directory=root) is None

    outside = StagedUpdate(version="0.2.0", installer=tmp_path / "evil.exe", sha256="00")
    with pytest.raises(ProjectFlowError, match="hors du dossier"):
        save_staged_update(outside, directory=root)


def test_staged_update_ignores_an_unreadable_record(tmp_path: Path) -> None:
    (tmp_path / STAGED_UPDATE_FILE).write_text("{not json", encoding="utf-8")

    assert load_staged_update(current_version="0.1.0", directory=tmp_path) is None


def test_verify_staged_installer_detects_a_changed_or_missing_file(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    assert file_sha256(staged.installer) == staged.sha256

    staged.installer.write_bytes(b"something else")
    assert verify_staged_installer(staged) is False

    staged.installer.unlink()
    assert verify_staged_installer(staged) is False


def test_begin_staged_install_counts_the_attempt_before_running(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    save_staged_update(staged, directory=tmp_path)

    plan = begin_staged_install(
        staged, system_name="Windows", relaunch="background", now=1000.0, directory=tmp_path
    )

    assert plan.command[0] == str(staged.installer)
    assert "/VERYSILENT" in plan.command
    assert "/RELAUNCH=background" in plan.command
    saved = load_staged_update(current_version="0.1.0", directory=tmp_path)
    assert saved is not None
    assert saved.attempts == 1
    assert saved.attempted_at == 1000.0
    assert saved.exhausted is False


def test_staged_update_gives_up_after_repeated_failures(tmp_path: Path) -> None:
    staged = _staged(tmp_path)
    for attempt in range(MAX_INSTALL_ATTEMPTS):
        begin_staged_install(
            staged, system_name="Windows", relaunch="window", now=float(attempt), directory=tmp_path
        )
        loaded = load_staged_update(current_version="0.1.0", directory=tmp_path)
        assert loaded is not None
        staged = loaded

    assert staged.exhausted is True


def test_install_in_progress_only_just_after_an_attempt(tmp_path: Path) -> None:
    never_tried = _staged(tmp_path)
    assert install_in_progress(never_tried, now=10.0) is False

    tried = StagedUpdate(
        version="0.2.0",
        installer=never_tried.installer,
        sha256=never_tried.sha256,
        attempts=1,
        attempted_at=1000.0,
    )
    assert install_in_progress(tried, now=1000.0 + INSTALL_IN_PROGRESS_SECONDS - 1) is True
    assert install_in_progress(tried, now=1000.0 + INSTALL_IN_PROGRESS_SECONDS) is False
    # A clock set back never blocks the start for long.
    assert install_in_progress(tried, now=900.0) is False


def test_discard_staged_update_without_downloads_folder(tmp_path: Path) -> None:
    discard_staged_update(directory=tmp_path / "missing")

    assert not (tmp_path / "missing").exists()


def test_prepare_windows_install_plan_keeps_legacy_portable_fallback(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlowAutomator.exe"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(
        asset_path,
        current_executable=tmp_path / "current.exe",
        process_id=123,
        script_dir=tmp_path,
        system_name="Windows",
    )

    assert plan.command[0] == "powershell"
    assert plan.command[plan.command.index("-ProcessId") + 1] == "123"
    script_path = tmp_path / "legacy_projectflow_update.ps1"
    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")
    assert "Copy-Item" in script
    # $Pid is read-only in PowerShell: a parameter of that name stops the script.
    assert "$Pid" not in script


def test_prepare_macos_install_plan_opens_dmg(tmp_path: Path) -> None:
    asset_path = tmp_path / "ProjectFlow Automator.dmg"
    asset_path.write_bytes(b"binary")

    plan = prepare_install_plan(asset_path, system_name="Darwin")

    assert plan.should_quit_app is False
    assert plan.command == ("open", str(asset_path))
