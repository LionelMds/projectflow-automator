from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from projectflow import __version__
from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.exceptions import ProjectFlowError
from projectflow.ui.controller import UPDATE_IDLE_POLLS, ProjectFlowController
from projectflow.ui.main_window import MainWindow
from projectflow.updates import (
    MAX_INSTALL_ATTEMPTS,
    ReleaseAsset,
    StagedUpdate,
    UpdateInfo,
    load_staged_update,
    save_staged_update,
)

NEW_VERSION = "999.0.0"
INSTALLER = b"installer"


class Release:
    """What GitHub answers, and what was downloaded, during one test."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.update: UpdateInfo | None = UpdateInfo(
            current_version=__version__,
            latest_version=NEW_VERSION,
            release_url="https://github.example/release",
            assets=(
                ReleaseAsset("ProjectFlowAutomatorSetup.exe", "https://x/setup.exe", 9),
                ReleaseAsset("ProjectFlowAutomatorSetup.exe.sha256", "https://x/sha", 1),
            ),
        )
        self.downloads = 0
        self.quits = 0
        self.questions: list[str] = []
        self.answer = QMessageBox.StandardButton.No


@pytest.fixture
def release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Release:
    state = Release(tmp_path / "updates")

    class Checker:
        def __init__(self, _settings: object) -> None:
            pass

        async def check(self, *, current_version: str) -> UpdateInfo | None:
            assert current_version == __version__
            return state.update

    class Downloader:
        async def download(
            self,
            asset: ReleaseAsset,
            *,
            version: str,
            checksum_asset: ReleaseAsset | None = None,
        ) -> Path:
            assert checksum_asset is not None
            state.downloads += 1
            target = state.root / version / asset.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(INSTALLER)
            return target

    def ask(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        state.questions.append(text)
        return state.answer

    def quit_app() -> None:
        state.quits += 1

    monkeypatch.setattr("projectflow.updates.updates_dir", lambda: state.root)
    monkeypatch.setattr("projectflow.ui.controller.GitHubReleaseChecker", Checker)
    monkeypatch.setattr("projectflow.ui.controller.UpdateDownloader", Downloader)
    monkeypatch.setattr("projectflow.ui.controller.silent_updates_supported", lambda: True)
    monkeypatch.setattr("projectflow.updates.system", lambda: "Windows")
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", ask)
    monkeypatch.setattr(QApplication, "quit", staticmethod(quit_app))
    return state


class Services:
    """The update never touches a project: only the fiche service is asked for, at start."""

    def fiche(self) -> FicheService:
        return FicheService()


def _controller(qtbot: Any, tmp_path: Path) -> tuple[ProjectFlowController, MainWindow]:
    config = AppConfig()
    config.paths.racine_projets = tmp_path / "clients"
    window = MainWindow(config)
    qtbot.addWidget(window)
    controller = ProjectFlowController(window=window, config=config, services=Services())  # type: ignore[arg-type]
    return controller, window


def _poll_until_idle(controller: ProjectFlowController) -> None:
    for _ in range(UPDATE_IDLE_POLLS):
        controller._install_staged_update_when_idle()  # noqa: SLF001


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_background_check_downloads_without_any_question(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, window = _controller(qtbot, tmp_path)
    window.show()

    await controller.check_updates(show_no_update=False)

    assert release.questions == []
    assert release.downloads == 1
    staged = load_staged_update(current_version=__version__)
    assert staged is not None
    assert staged.version == NEW_VERSION
    assert staged.attempts == 0
    # Downloaded, but nothing is installed under a window the user is looking at.
    assert controller.install_plan is None
    assert release.quits == 0


@pytest.mark.asyncio
async def test_staged_update_waits_for_a_hidden_window(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, window = _controller(qtbot, tmp_path)
    window.show()
    await controller.check_updates(show_no_update=False)

    _poll_until_idle(controller)
    await _settle()
    assert controller.install_plan is None

    window.hide()
    controller._install_staged_update_when_idle()  # noqa: SLF001
    await _settle()
    assert controller.install_plan is None  # Hidden a moment ago: it may come back.

    _poll_until_idle(controller)
    await _settle()

    plan = controller.install_plan
    assert plan is not None
    assert "/VERYSILENT" in plan.command
    assert "/RELAUNCH=background" in plan.command
    assert release.quits == 1
    staged = load_staged_update(current_version=__version__)
    assert staged is not None
    assert staged.attempts == 1


@pytest.mark.asyncio
async def test_staged_update_never_installs_under_an_open_dialog(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, _window = _controller(qtbot, tmp_path)
    await controller.check_updates(show_no_update=False)
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.show()

    _poll_until_idle(controller)
    _poll_until_idle(controller)
    await _settle()

    assert controller.install_plan is None
    assert release.quits == 0


@pytest.mark.asyncio
async def test_staged_update_waits_for_a_running_operation(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, _window = _controller(qtbot, tmp_path)
    await controller.check_updates(show_no_update=False)
    controller._set_mutation_busy(busy=True)  # noqa: SLF001

    _poll_until_idle(controller)
    await _settle()
    assert controller.install_plan is None

    controller._set_mutation_busy(busy=False)  # noqa: SLF001
    _poll_until_idle(controller)
    await _settle()
    assert controller.install_plan is not None


@pytest.mark.asyncio
async def test_requested_check_installs_at_once_and_reopens_the_window(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, window = _controller(qtbot, tmp_path)
    window.show()

    await controller.check_updates(show_no_update=True)

    assert release.questions == []
    plan = controller.install_plan
    assert plan is not None
    assert "/RELAUNCH=window" in plan.command
    assert release.quits == 1


@pytest.mark.asyncio
async def test_second_check_reuses_the_downloaded_installer(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, window = _controller(qtbot, tmp_path)
    window.show()

    await controller.check_updates(show_no_update=False)
    await controller.check_updates(show_no_update=False)

    assert release.downloads == 1


@pytest.mark.asyncio
async def test_damaged_installer_is_downloaded_again(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    controller, window = _controller(qtbot, tmp_path)
    window.show()
    await controller.check_updates(show_no_update=False)
    staged = load_staged_update(current_version=__version__)
    assert staged is not None
    staged.installer.write_bytes(b"damaged")

    await controller.check_updates(show_no_update=False)

    assert release.downloads == 2
    assert staged.installer.read_bytes() == INSTALLER


@pytest.mark.asyncio
async def test_installer_that_keeps_failing_is_offered_with_a_question(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    installer = release.root / NEW_VERSION / "ProjectFlowAutomatorSetup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(INSTALLER)
    save_staged_update(
        StagedUpdate(
            version=NEW_VERSION,
            installer=installer,
            sha256=sha256(INSTALLER).hexdigest(),
            attempts=MAX_INSTALL_ATTEMPTS,
        ),
    )
    controller, window = _controller(qtbot, tmp_path)
    window.show()

    await controller.check_updates(show_no_update=False)
    await controller.check_updates(show_no_update=False)

    # Asked once, refused: neither installed nor asked again by the next background check.
    assert len(release.questions) == 1
    assert controller.install_plan is None
    assert release.quits == 0


@pytest.mark.asyncio
async def test_background_check_stays_quiet_when_github_is_unreachable(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Offline:
        def __init__(self, _settings: object) -> None:
            pass

        async def check(self, *, current_version: str) -> UpdateInfo | None:
            del current_version
            raise ProjectFlowError("Verification de mise a jour impossible.")

    errors: list[str] = []
    monkeypatch.setattr("projectflow.ui.controller.GitHubReleaseChecker", Offline)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.critical",
        lambda _parent, _title, text: errors.append(text),
    )
    controller, _window = _controller(qtbot, tmp_path)

    await controller.check_updates(show_no_update=False)
    await _settle()
    assert errors == []

    await controller.check_updates(show_no_update=True)
    await _settle()
    assert errors == ["Verification de mise a jour impossible."]


@pytest.mark.asyncio
async def test_update_notice_after_the_restart(
    qtbot: Any,
    tmp_path: Path,
    release: Release,
) -> None:
    del release
    controller, window = _controller(qtbot, tmp_path)
    notices: list[tuple[str, str]] = []
    controller.set_notifier(lambda title, message: notices.append((title, message)))

    controller.announce_update_installed()

    assert notices == [("ProjectFlow mis à jour", f"Version {__version__} installée.")]
    assert __version__ in window.creation_tab.logs.toPlainText()
