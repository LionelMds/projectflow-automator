from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from projectflow import build
from projectflow.build import (
    BuildSettings,
    declare_link_scheme,
    detect_target,
    pyinstaller_command,
)


def test_detect_target_windows() -> None:
    assert detect_target("Windows") == "windows"


def test_detect_target_macos() -> None:
    assert detect_target("Darwin") == "macos"


def test_detect_target_rejects_linux_packaging() -> None:
    with pytest.raises(ValueError, match="non supportee"):
        detect_target("Linux")


def test_windows_pyinstaller_command_uses_onefile(tmp_path: Path) -> None:
    command = pyinstaller_command(BuildSettings(project_root=tmp_path, target="windows"))

    assert command[:3] == [sys.executable, "-m", "PyInstaller"]
    assert "--onefile" in command
    assert "--windowed" in command
    assert "--workpath" in command
    assert "--specpath" in command
    assert "msal" in command
    assert "keyring.backends.Windows" in command
    assert "win32com.client" in command
    assert "win32timezone" in command
    assert "win32crypt" in command
    assert "comtypes.client" in command
    assert "pythoncom" in command
    assert "pywintypes" in command
    assert "mypy" in command
    assert "ProjectFlowAutomator" in command
    assert command[-1] == str(tmp_path / "src" / "projectflow" / "__main__.py")


def test_macos_pyinstaller_command_uses_app_bundle(tmp_path: Path) -> None:
    command = pyinstaller_command(BuildSettings(project_root=tmp_path, target="macos"))

    assert "--onedir" in command
    assert "ProjectFlow Automator" in command
    assert "ch.balzmetal.projectflow" in command
    assert "msal" in command
    assert "keyring.backends.macOS" in command


def test_pyinstaller_command_accepts_upx_dir(tmp_path: Path) -> None:
    upx_dir = tmp_path / "upx"
    command = pyinstaller_command(
        BuildSettings(project_root=tmp_path, target="windows", upx_dir=upx_dir),
    )

    assert "--upx-dir" in command
    assert str(upx_dir) in command


def test_resources_are_added_with_platform_separator(tmp_path: Path) -> None:
    ui_resources_dir = tmp_path / "src" / "projectflow" / "ui" / "resources"
    app_resources_dir = tmp_path / "src" / "projectflow" / "resources"
    ui_resources_dir.mkdir(parents=True)
    app_resources_dir.mkdir(parents=True)

    command = pyinstaller_command(BuildSettings(project_root=tmp_path, target="windows"))

    assert "--add-data" in command
    assert f"{ui_resources_dir};projectflow/ui/resources" in command
    assert f"{app_resources_dir};projectflow/resources" in command


def test_macos_bundle_declares_the_projectflow_links(tmp_path: Path) -> None:
    info_plist = tmp_path / "Info.plist"
    with info_plist.open("wb") as stream:
        plistlib.dump(
            {
                "CFBundleIdentifier": "ch.balzmetal.projectflow",
                "CFBundleURLTypes": [{"CFBundleURLSchemes": ["autre"]}],
            },
            stream,
        )

    declare_link_scheme(info_plist)
    declare_link_scheme(info_plist)

    with info_plist.open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleIdentifier"] == "ch.balzmetal.projectflow"
    assert [url_type["CFBundleURLSchemes"] for url_type in info["CFBundleURLTypes"]] == [
        ["autre"],
        ["projectflow"],
    ]


def test_macos_build_declares_the_links_before_the_bundle_is_signed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info_plist = tmp_path / "dist" / "ProjectFlow Automator.app" / "Contents" / "Info.plist"

    def fake_pyinstaller(*_args: object, **_kwargs: object) -> None:
        info_plist.parent.mkdir(parents=True)
        with info_plist.open("wb") as stream:
            plistlib.dump({"CFBundleName": "ProjectFlow Automator"}, stream)

    monkeypatch.setattr(build.subprocess, "run", fake_pyinstaller)

    build.run_pyinstaller(BuildSettings(project_root=tmp_path, target="macos"))

    with info_plist.open("rb") as stream:
        assert plistlib.load(stream)["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["projectflow"]


def test_windows_build_leaves_the_link_declaration_to_the_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(build.subprocess, "run", lambda *_args, **_kwargs: None)

    build.run_pyinstaller(BuildSettings(project_root=tmp_path, target="windows"))

    assert not (tmp_path / "dist").exists()
