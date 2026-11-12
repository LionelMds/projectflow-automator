from __future__ import annotations

import os
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
import structlog

from projectflow import __version__
from projectflow.app import _install_staged_update
from projectflow.exceptions import ProjectFlowError
from projectflow.updates import (
    AFTER_UPDATE_ARGUMENT,
    BACKGROUND_ARGUMENT,
    MACOS_TEAM_ID,
    MACOS_UPDATE_LOG,
    UPDATE_FAILED_ARGUMENT,
    InstallPlan,
    ReleaseAsset,
    StagedUpdate,
    begin_staged_install,
    installs_unattended,
    load_staged_update,
    running_app_bundle,
    save_staged_update,
)

DMG = "ProjectFlow Automator.dmg"
NEWER = "999.0.0"


def _bundle(parent: Path, *, content: str = "old") -> Path:
    bundle = parent / "ProjectFlow Automator.app"
    executable = bundle / "Contents" / "MacOS" / "ProjectFlow Automator"
    executable.parent.mkdir(parents=True)
    executable.write_text(content, encoding="utf-8")
    return bundle


def _staged(root: Path, *, version: str = NEWER, attempts: int = 0) -> StagedUpdate:
    image = root / version / DMG
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")
    staged = StagedUpdate(
        version=version,
        installer=image,
        sha256=sha256(b"image").hexdigest(),
        attempts=attempts,
    )
    save_staged_update(staged, directory=root)
    return staged


def test_running_app_bundle_is_the_app_folder(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "Applications")
    executable = bundle / "Contents" / "MacOS" / "ProjectFlow Automator"

    assert running_app_bundle(executable) == bundle.resolve()
    assert running_app_bundle(tmp_path / "python") is None


def test_dmg_installs_unattended_only_where_the_app_can_be_replaced(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    bundle = _bundle(applications)
    image = ReleaseAsset(DMG, "https://x", 1)

    assert installs_unattended(image, bundle=bundle) is True
    assert (
        installs_unattended(ReleaseAsset("ProjectFlow.zip", "https://x", 1), bundle=bundle) is False
    )
    assert installs_unattended(image, bundle=tmp_path / "missing.app") is False

    applications.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(applications, os.W_OK):
            pytest.skip("permissions are not enforced for this user")
        # Another account's /Applications, or an app started from the image itself.
        assert installs_unattended(image, bundle=bundle) is False
    finally:
        applications.chmod(stat.S_IRWXU)


def test_begin_macos_install_counts_the_attempt_and_runs_the_script(tmp_path: Path) -> None:
    downloads = tmp_path / "updates"
    staged = _staged(downloads)
    bundle = _bundle(tmp_path / "Applications")

    plan = begin_staged_install(
        staged,
        relaunch="background",
        now=1000.0,
        directory=downloads,
        system_name="Darwin",
        bundle=bundle,
        process_id=4321,
    )

    assert plan.should_quit_app is True
    assert plan.command[0] == "/bin/bash"
    script = Path(plan.command[1])
    assert script.parent == downloads
    assert plan.command[2:] == (
        "4321",
        str(staged.installer),
        str(bundle),
        "background",
        MACOS_TEAM_ID,
        str(downloads / MACOS_UPDATE_LOG),
    )
    saved = load_staged_update(current_version="0.1.0", directory=downloads)
    assert saved is not None
    assert saved.attempts == 1


def test_begin_macos_install_refuses_an_app_it_cannot_replace(tmp_path: Path) -> None:
    downloads = tmp_path / "updates"
    staged = _staged(downloads)

    with pytest.raises(ProjectFlowError):
        begin_staged_install(
            staged,
            relaunch="window",
            directory=downloads,
            system_name="Darwin",
            bundle=tmp_path / "missing.app",
        )

    # Nothing ran: the attempt is not counted.
    saved = load_staged_update(current_version="0.1.0", directory=downloads)
    assert saved is not None
    assert saved.attempts == 0


def test_start_after_a_failed_macos_update_never_retries_at_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plans: list[InstallPlan] = []
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)
    monkeypatch.setattr("projectflow.updates.updates_dir", lambda: tmp_path)
    monkeypatch.setattr("projectflow.app.silent_updates_supported", lambda: True)
    monkeypatch.setattr("projectflow.app.launch_install_plan", plans.append)
    staged = _staged(tmp_path, attempts=1)

    started = _install_staged_update(
        ["ProjectFlow Automator", UPDATE_FAILED_ARGUMENT],
        structlog.get_logger(),
    )

    assert started is False
    assert plans == []
    # Kept and counted: the running app tries once more, then asks.
    assert load_staged_update(current_version=__version__) == staged


# The script itself, with stand-ins for the tools that touch the system: hdiutil "mounts"
# a folder, codesign and spctl answer as told, open records how the app is started again.
_FAKE_TOOLS = {
    "hdiutil": """#!/bin/bash
if [ "$1" = attach ]; then
  while [ "$#" -gt 0 ]; do
    if [ "$1" = -mountpoint ]; then cp -R "$FAKE_IMAGE"/. "$2"/; fi
    shift
  done
fi
exit 0
""",
    "codesign": (
        '#!/bin/bash\necho "codesign $*" >> "$FAKE_CALLS"\nexit "${FAKE_CODESIGN_STATUS:-0}"\n'
    ),
    "spctl": "#!/bin/bash\nexit 0\n",
    "open": '#!/bin/bash\necho "open $*" >> "$FAKE_CALLS"\n',
}


@pytest.fixture
def script_run(tmp_path: Path) -> dict[str, Path]:
    if sys.platform != "darwin":
        pytest.skip("the update script runs on macOS only (ditto)")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name, body in _FAKE_TOOLS.items():
        tool = tools / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    image = tmp_path / "image"
    image.mkdir()
    _bundle(image, content="new")
    return {
        "tools": tools,
        "image": image,
        "bundle": _bundle(tmp_path / "Applications"),
        "downloads": tmp_path / "updates",
        "calls": tmp_path / "calls.log",
    }


def _run_script(paths: dict[str, Path], *, codesign_status: int = 0) -> list[str]:
    staged = _staged(paths["downloads"])
    plan = begin_staged_install(
        staged,
        relaunch="background",
        directory=paths["downloads"],
        system_name="Darwin",
        bundle=paths["bundle"],
        # A process that already exited: the script does not wait.
        process_id=_exited_process_id(),
    )
    environment = {
        **os.environ,
        "PATH": f"{paths['tools']}{os.pathsep}{os.environ['PATH']}",
        "FAKE_IMAGE": str(paths["image"]),
        "FAKE_CALLS": str(paths["calls"]),
        "FAKE_CODESIGN_STATUS": str(codesign_status),
    }
    subprocess.run(plan.command, env=environment, check=False, timeout=60)
    return paths["calls"].read_text(encoding="utf-8").splitlines()


def _exited_process_id() -> int:
    process = subprocess.Popen(["/usr/bin/true"])
    process.wait()
    return process.pid


def _executable_content(bundle: Path) -> str:
    return (bundle / "Contents" / "MacOS" / "ProjectFlow Automator").read_text(encoding="utf-8")


def test_script_replaces_the_app_and_starts_the_new_version(script_run: dict[str, Path]) -> None:
    calls = _run_script(script_run)

    bundle = script_run["bundle"]
    assert _executable_content(bundle) == "new"
    assert not any(path.name.endswith(".previous") for path in bundle.parent.iterdir())
    signature_check = next(call for call in calls if call.startswith("codesign"))
    assert MACOS_TEAM_ID in signature_check
    assert calls[-1] == f"open -n {bundle} --args {AFTER_UPDATE_ARGUMENT} {BACKGROUND_ARGUMENT}"


def test_script_keeps_the_old_app_when_the_signature_is_refused(
    script_run: dict[str, Path],
) -> None:
    calls = _run_script(script_run, codesign_status=1)

    bundle = script_run["bundle"]
    assert _executable_content(bundle) == "old"
    assert calls[-1] == f"open -n {bundle} --args {UPDATE_FAILED_ARGUMENT} {BACKGROUND_ARGUMENT}"
    log = (script_run["downloads"] / MACOS_UPDATE_LOG).read_text(encoding="utf-8")
    assert "signature" in log
