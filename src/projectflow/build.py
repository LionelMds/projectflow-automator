from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

BuildTarget = Literal["windows", "macos"]


@dataclass(frozen=True)
class BuildSettings:
    project_root: Path
    target: BuildTarget
    clean: bool = True
    upx_dir: Path | None = None


def detect_target(system_name: str | None = None) -> BuildTarget:
    normalized = (system_name or platform.system()).lower()
    if normalized == "windows":
        return "windows"
    if normalized == "darwin":
        return "macos"
    msg = f"Plateforme de packaging non supportee: {system_name or platform.system()}"
    raise ValueError(msg)


def pyinstaller_command(settings: BuildSettings) -> list[str]:
    source_root = settings.project_root / "src"
    entrypoint = source_root / "projectflow" / "__main__.py"
    resource_dirs = (
        (source_root / "projectflow" / "ui" / "resources", "projectflow/ui/resources"),
        (source_root / "projectflow" / "resources", "projectflow/resources"),
    )

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--paths",
        str(source_root),
        "--workpath",
        str(_work_path(settings) / "work"),
        "--specpath",
        str(_work_path(settings) / "spec"),
    ]
    if settings.upx_dir is not None:
        command.extend(["--upx-dir", str(settings.upx_dir)])
    for hidden_import in _hidden_imports(settings.target):
        command.extend(["--hidden-import", hidden_import])
    for excluded_module in _excluded_modules():
        command.extend(["--exclude-module", excluded_module])
    if settings.clean:
        command.append("--clean")
    for resources_dir, destination in resource_dirs:
        if not resources_dir.exists():
            continue
        command.extend(
            [
                "--add-data",
                f"{resources_dir}{_add_data_separator(settings.target)}{destination}",
            ],
        )

    if settings.target == "windows":
        command.extend(["--onefile", "--name", "ProjectFlowAutomator"])
        _append_icon(command, source_root / "projectflow" / "ui" / "resources" / "icon.ico")
    else:
        command.extend(
            [
                "--onedir",
                "--name",
                "ProjectFlow Automator",
                "--osx-bundle-identifier",
                "ch.balzmetal.projectflow",
            ],
        )
        _append_icon(command, source_root / "projectflow" / "ui" / "resources" / "icon.icns")

    command.append(str(entrypoint))
    return command


def run_pyinstaller(settings: BuildSettings) -> None:
    subprocess.run(pyinstaller_command(settings), cwd=settings.project_root, check=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    target = _target_from_argument(cast("str", args.target))
    settings = BuildSettings(
        project_root=Path(cast("str", args.project_root)).resolve(),
        target=target,
        clean=not cast("bool", args.no_clean),
        upx_dir=_upx_dir_from_argument(cast("str | None", args.upx_dir)),
    )
    sys.stdout.write(f"Build ProjectFlow Automator pour {settings.target}...\n")
    run_pyinstaller(settings)
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construit les artefacts ProjectFlow.")
    parser.add_argument(
        "--target",
        choices=["auto", "windows", "macos"],
        default="auto",
        help="Plateforme cible. Par defaut: detection automatique.",
    )
    parser.add_argument(
        "--project-root",
        default=str(Path.cwd()),
        help="Racine du projet contenant pyproject.toml.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Conserve le cache PyInstaller pour accelerer les builds locaux.",
    )
    parser.add_argument(
        "--upx-dir",
        default=None,
        help="Dossier contenant upx.exe. Defaut: variable PROJECTFLOW_UPX_DIR si definie.",
    )
    return parser.parse_args(argv)


def _target_from_argument(value: str) -> BuildTarget:
    if value == "auto":
        return detect_target()
    if value in {"windows", "macos"}:
        return cast("BuildTarget", value)
    msg = f"Cible inconnue: {value}"
    raise ValueError(msg)


def _add_data_separator(target: BuildTarget) -> str:
    return ";" if target == "windows" else ":"


def _work_path(settings: BuildSettings) -> Path:
    root_name = settings.project_root.name.replace(" ", "-")
    return Path(tempfile.gettempdir()) / "projectflow-automator" / root_name / settings.target


def _upx_dir_from_argument(value: str | None) -> Path | None:
    raw_value = value or os.environ.get("PROJECTFLOW_UPX_DIR")
    if raw_value is None or raw_value.strip() == "":
        return None
    return Path(raw_value).expanduser().resolve()


def _hidden_imports(target: BuildTarget) -> tuple[str, ...]:
    hidden_imports = ["qasync"]
    if target == "windows":
        hidden_imports.extend(["pythoncom", "pywintypes", "win32com.client"])
    return tuple(hidden_imports)


def _excluded_modules() -> tuple[str, ...]:
    return (
        "IPython",
        "PIL",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "click",
        "matplotlib",
        "mypy",
        "numpy",
        "pytest",
        "rich",
        "ruff",
        "tkinter",
        "trio",
        "unittest",
    )


def _append_icon(command: list[str], icon_path: Path) -> None:
    if icon_path.exists():
        command.extend(["--icon", str(icon_path)])


if __name__ == "__main__":
    raise SystemExit(main())
