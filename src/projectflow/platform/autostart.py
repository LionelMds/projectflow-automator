"""Start ProjectFlow with the session of the user, hidden in the notification area.

The alerts of the planning ring only while ProjectFlow runs. Windows: a value under the
``Run`` key of the user (no administrator right). macOS: a launch agent of the user. Both
start the installed application with ``--background``, as after a silent update. A
ProjectFlow run from its sources is not an installed application: nothing is offered.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from projectflow.updates import BACKGROUND_ARGUMENT

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ProjectFlow Automator"
AGENT_LABEL = "ch.balzmetal.projectflow.autostart"
_PLATFORM: str = sys.platform
"""Read once as plain text, so that every branch is type-checked on every system."""


def autostart_supported() -> bool:
    """Whether this ProjectFlow is an installed application of Windows or macOS."""
    return bool(getattr(sys, "frozen", False)) and _PLATFORM in {"win32", "darwin"}


def run_command(executable: str) -> str:
    """Command line of the ``Run`` value: the application, quoted, in the background."""
    return f'"{executable}" {BACKGROUND_ARGUMENT}'


def launch_agent_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"


def launch_agent(executable: str) -> dict[str, object]:
    return {
        "Label": AGENT_LABEL,
        "ProgramArguments": [executable, BACKGROUND_ARGUMENT],
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }


def autostart_enabled(*, run_key: str = RUN_KEY, home: Path | None = None) -> bool:
    """Whether the session starts this ProjectFlow (another path does not count)."""
    if _PLATFORM == "win32":
        return _read_run_value(run_key) == run_command(sys.executable)
    if _PLATFORM == "darwin":
        try:
            agent = plistlib.loads(launch_agent_path(home).read_bytes())
        except (OSError, ValueError):
            return False
        wanted = [sys.executable, BACKGROUND_ARGUMENT]
        return isinstance(agent, dict) and agent.get("ProgramArguments") == wanted
    return False


def set_autostart(*, enabled: bool, run_key: str = RUN_KEY, home: Path | None = None) -> None:
    """Start ProjectFlow with the session, or stop doing so; raises OSError when refused."""
    if _PLATFORM == "win32":
        _write_run_value(run_key, run_command(sys.executable) if enabled else None)
    elif _PLATFORM == "darwin":
        path = launch_agent_path(home)
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(plistlib.dumps(launch_agent(sys.executable)))
        else:
            path.unlink(missing_ok=True)


def _read_run_value(run_key: str) -> str | None:
    import winreg  # noqa: PLC0415 - Windows only

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key) as key:
            value, _kind = winreg.QueryValueEx(key, RUN_VALUE)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def _write_run_value(run_key: str, command: str | None) -> None:
    import winreg  # noqa: PLC0415 - Windows only

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                return
        else:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
