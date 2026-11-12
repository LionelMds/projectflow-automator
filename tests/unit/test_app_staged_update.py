from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
import structlog

from projectflow import __version__
from projectflow.app import _install_staged_update
from projectflow.updates import (
    AFTER_UPDATE_ARGUMENT,
    BACKGROUND_ARGUMENT,
    MAX_INSTALL_ATTEMPTS,
    STAGED_UPDATE_FILE,
    InstallPlan,
    StagedUpdate,
    load_staged_update,
    save_staged_update,
)

NEWER = "999.0.0"
EXE = "ProjectFlowAutomator.exe"


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[InstallPlan]:
    """Act as the built Windows app, downloads in ``tmp_path``; list the installers started."""
    plans: list[InstallPlan] = []
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)
    monkeypatch.setattr("projectflow.updates.updates_dir", lambda: tmp_path)
    monkeypatch.setattr("projectflow.app.silent_updates_supported", lambda: True)
    monkeypatch.setattr("projectflow.app.launch_install_plan", plans.append)
    return plans


def _stage(root: Path, *, version: str = NEWER, attempts: int = 0) -> StagedUpdate:
    installer = root / version / "ProjectFlowAutomatorSetup.exe"
    installer.parent.mkdir(parents=True)
    installer.write_bytes(b"installer")
    staged = StagedUpdate(
        version=version,
        installer=installer,
        sha256=sha256(b"installer").hexdigest(),
        attempts=attempts,
    )
    save_staged_update(staged, directory=root)
    return staged


def _start(*arguments: str) -> bool:
    return _install_staged_update([EXE, *arguments], structlog.get_logger())


def test_start_installs_the_release_downloaded_earlier(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    staged = _stage(tmp_path)

    assert _start() is True

    assert len(launched) == 1
    assert launched[0].command[0] == str(staged.installer)
    assert "/VERYSILENT" in launched[0].command
    assert "/RELAUNCH=window" in launched[0].command
    saved = load_staged_update(current_version=__version__)
    assert saved is not None
    assert saved.attempts == 1


def test_background_start_comes_back_in_the_background(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    _stage(tmp_path)

    assert _start(BACKGROUND_ARGUMENT) is True

    assert "/RELAUNCH=background" in launched[0].command


def test_start_after_an_update_deletes_the_installer_that_ran(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    staged = _stage(tmp_path, version=__version__, attempts=1)

    assert _start(AFTER_UPDATE_ARGUMENT, BACKGROUND_ARGUMENT) is False

    assert launched == []
    assert not staged.installer.exists()
    assert not (tmp_path / STAGED_UPDATE_FILE).exists()


def test_start_after_an_update_never_runs_another_installer(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    staged = _stage(tmp_path)

    assert _start(AFTER_UPDATE_ARGUMENT) is False

    # Kept for the running app, which installs it when its window is hidden.
    assert launched == []
    assert staged.installer.exists()
    assert load_staged_update(current_version=__version__) == staged


def test_second_start_during_an_installation_waits_for_it(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    _stage(tmp_path)

    assert _start() is True
    assert _start() is True

    assert len(launched) == 1


def test_start_shows_the_app_when_the_installer_keeps_failing(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    _stage(tmp_path, attempts=MAX_INSTALL_ATTEMPTS)

    assert _start() is False

    assert launched == []


def test_start_discards_a_damaged_installer(
    launched: list[InstallPlan],
    tmp_path: Path,
) -> None:
    staged = _stage(tmp_path)
    staged.installer.write_bytes(b"damaged")

    assert _start() is False

    assert launched == []
    assert load_staged_update(current_version=__version__) is None
    assert not staged.installer.exists()


def test_start_from_source_never_installs(
    launched: list[InstallPlan],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = _stage(tmp_path)
    monkeypatch.setattr("projectflow.app.silent_updates_supported", lambda: False)

    assert _start() is False

    assert launched == []
    assert load_staged_update(current_version=__version__) == staged


def test_demo_start_never_installs(launched: list[InstallPlan], tmp_path: Path) -> None:
    _stage(tmp_path)

    assert _start("--demo") is False

    assert launched == []
