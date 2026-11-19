from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject, QUrl
from PySide6.QtGui import QFileOpenEvent

from projectflow import __main__ as entry_point
from projectflow.config import AppConfig
from projectflow.core.dossier_link import (
    LINK_SCHEME,
    DossierLinkError,
    dossier_dir,
    dossier_link,
    find_dossier_link,
    is_dossier_link,
    parse_dossier_link,
)
from projectflow.core.numero import parse_project_number
from projectflow.dossier_links import DossierLinkEvents, open_dossier_link, run_dossier_link

INSTALLER = Path(__file__).parents[2] / "installer" / "windows" / "ProjectFlowAutomator.iss"


def test_link_names_the_dossier_and_reads_back() -> None:
    number = parse_project_number("2024-4493")

    assert dossier_link(number) == "projectflow://dossier/2024-4493"
    assert parse_dossier_link(dossier_link(number)) == number


@pytest.mark.parametrize(
    "link",
    [
        "projectflow://dossier/2024-4493",
        "projectflow://dossier/2024-4493/",
        "PROJECTFLOW://Dossier/2024-4493",
        "projectflow:dossier/2024-4493",
        " projectflow://dossier/2024%2D4493 ",
    ],
)
def test_link_is_read_however_a_program_rewrote_it(link: str) -> None:
    assert parse_dossier_link(link) == parse_project_number("2024-4493")


def test_subproject_link_keeps_its_suffix() -> None:
    number = parse_project_number("2026-5102-REV03")

    assert parse_dossier_link("projectflow://dossier/2026-5102-rev03") == number
    assert parse_dossier_link(dossier_link(number)) == number


@pytest.mark.parametrize(
    "link",
    [
        "https://dossier/2024-4493",
        "projectflow://dossier",
        "projectflow://dossier/",
        "projectflow://dossier/2024-4493/Plans",
        "projectflow://dossier/..%2F..%2FWindows",
        "projectflow://dossier/C:%5CWindows",
        "projectflow://fiche/2024-4493",
        "projectflow://dossier/2024-4493?ouvrir=calc.exe",
        "2024-4493",
    ],
)
def test_link_that_is_not_one_dossier_number_is_refused(link: str) -> None:
    # A link can come from anywhere: it never carries a path nor a command.
    with pytest.raises(DossierLinkError):
        parse_dossier_link(link)


def test_link_is_found_among_the_arguments() -> None:
    link = "projectflow://dossier/2024-4493"

    assert find_dossier_link(["--background", link]) == link
    assert find_dossier_link(["--demo"]) is None
    assert is_dossier_link("Projectflow://dossier/2024-4493")
    assert not is_dossier_link("--mailflow-request")


def test_dossier_dir_is_built_under_the_root_of_this_poste(tmp_path: Path) -> None:
    folder = tmp_path / "Clients" / "2024" / "2024-4493"
    folder.mkdir(parents=True)

    assert dossier_dir(tmp_path / "Clients", parse_project_number("2024-4493")) == folder


def test_subproject_opens_its_own_folder_when_it_has_one(tmp_path: Path) -> None:
    folder = tmp_path / "2024" / "2024-4493"
    folder.mkdir(parents=True)
    number = parse_project_number("2024-4493-2")

    assert dossier_dir(tmp_path, number) == folder

    (folder / "2024-4493-2").mkdir()

    assert dossier_dir(tmp_path, number) == folder / "2024-4493-2"


def test_missing_folder_or_root_is_told(tmp_path: Path) -> None:
    number = parse_project_number("2024-4493")

    with pytest.raises(DossierLinkError, match="2024-4493 introuvable"):
        dossier_dir(tmp_path, number)
    with pytest.raises(DossierLinkError, match="racine projets"):
        dossier_dir(None, number)


def test_open_dossier_link_opens_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "2024" / "2024-4493"
    folder.mkdir(parents=True)
    opened: list[Path] = []

    result = open_dossier_link(
        "projectflow://dossier/2024-4493",
        tmp_path,
        open_folder=opened.append,
    )

    assert result == folder
    assert opened == [folder]


def _config(root: Path | None) -> AppConfig:
    config = AppConfig()
    config.paths.racine_projets = root
    return config


def test_run_dossier_link_opens_the_folder_of_the_configured_root(tmp_path: Path) -> None:
    folder = tmp_path / "2024" / "2024-4493"
    folder.mkdir(parents=True)
    opened: list[Path] = []
    errors: list[str] = []

    code = run_dossier_link(
        "projectflow://dossier/2024-4493",
        load_config=lambda: _config(tmp_path),
        open_folder=opened.append,
        show_error=errors.append,
    )

    assert code == 0
    assert opened == [folder]
    assert errors == []


@pytest.mark.parametrize(
    ("link", "message"),
    [
        ("projectflow://dossier/2024-4493", "introuvable"),
        ("projectflow://dossier/pas-un-numero", "numero de dossier"),
    ],
)
def test_run_dossier_link_tells_why_nothing_opened(
    tmp_path: Path,
    link: str,
    message: str,
) -> None:
    opened: list[Path] = []
    errors: list[str] = []

    code = run_dossier_link(
        link,
        load_config=lambda: _config(tmp_path),
        open_folder=opened.append,
        show_error=errors.append,
    )

    assert code == 1
    assert opened == []
    assert len(errors) == 1
    assert message in errors[0]


_SHOW_ERROR_ALONE = """
import threading, time
from PySide6.QtCore import QMetaObject, Qt
from PySide6.QtWidgets import QApplication
from projectflow.dossier_links import _show_error

def close_the_box():
    while QApplication.instance() is None or not QApplication.topLevelWidgets():
        time.sleep(0.05)
    time.sleep(0.2)
    QMetaObject.invokeMethod(
        QApplication.instance(), "closeAllWindows", Qt.ConnectionType.QueuedConnection
    )

threading.Thread(target=close_the_box, daemon=True).start()
_show_error("Dossier 2024-4493 introuvable")
print("box closed")
"""


def test_error_box_is_shown_by_a_process_that_has_no_window_yet() -> None:
    # The link handler has no window and no event loop when it must tell a failure: a
    # static QMessageBox.warning() crashed there. No Qt timer in this script on purpose,
    # a pending one hid the crash.
    source_root = Path(__file__).parents[2] / "src"
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(source_root)}

    completed = subprocess.run(
        [sys.executable, "-c", _SHOW_ERROR_ALONE],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "box closed" in completed.stdout


def test_entry_point_serves_a_link_without_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    link = "projectflow://dossier/2024-4493"
    received: list[str] = []
    monkeypatch.setattr(entry_point.sys, "argv", ["ProjectFlowAutomator.exe", link])
    monkeypatch.setattr(
        "projectflow.dossier_links.run_dossier_link",
        lambda value: received.append(value) or 0,
    )

    assert entry_point.main() == 0
    assert received == [link]


def test_link_events_keep_early_links_until_something_can_open_them(qtbot) -> None:  # type: ignore[no-untyped-def]
    events = DossierLinkEvents()
    target = QObject()
    link = "projectflow://dossier/2024-4493"
    received: list[str] = []

    assert events.eventFilter(target, QFileOpenEvent(QUrl(link))) is True
    assert received == []

    events.deliver_to(received.append)
    assert received == [link]

    events.eventFilter(target, QFileOpenEvent(QUrl("projectflow://dossier/2025-4801")))
    assert received == [link, "projectflow://dossier/2025-4801"]


def test_link_events_leave_other_events_alone(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    events = DossierLinkEvents()
    received: list[str] = []
    events.deliver_to(received.append)
    target = QObject()

    assert events.eventFilter(target, QFileOpenEvent(QUrl.fromLocalFile(str(tmp_path)))) is False
    assert events.eventFilter(target, QEvent(QEvent.Type.Timer)) is False
    assert received == []


def test_windows_installer_hands_the_links_to_the_application() -> None:
    script = INSTALLER.read_text(encoding="utf-8")
    key = rf"Software\Classes\{LINK_SCHEME}"

    assert f'Subkey: "{key}"; ValueType: string; ValueName: "URL Protocol"' in script
    assert rf'Subkey: "{key}\shell\open\command"' in script
    assert 'ValueData: """{app}\\{#MyAppExeName}"" ""%1"""' in script
    assert "Flags: uninsdeletekey" in script
