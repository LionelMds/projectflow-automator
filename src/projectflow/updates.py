from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from platform import system
from typing import Literal
from urllib.parse import unquote, urlparse

import httpx

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError
from projectflow.platform.paths import updates_dir

GITHUB_API_ROOT = "https://api.github.com"
# Passed by the installer when it starts the new version after an in-app update.
AFTER_UPDATE_ARGUMENT = "--after-update"
# Start in the notification area, without showing the window.
BACKGROUND_ARGUMENT = "--background"
STAGED_UPDATE_FILE = "staged.json"
# A release whose installer fails twice is offered with the old question instead.
MAX_INSTALL_ATTEMPTS = 2
# A second start during an installation must not run a second installer.
INSTALL_IN_PROGRESS_SECONDS = 60.0
_DOWNLOAD_CHUNK_BYTES = 256 * 1024

RelaunchMode = Literal["window", "background"]
"""How the installer starts the new version: window shown, or notification area only."""


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    assets: tuple[ReleaseAsset, ...]
    release_notes: str = ""


@dataclass(frozen=True, slots=True)
class InstallPlan:
    command: tuple[str, ...]
    should_quit_app: bool


@dataclass(frozen=True, slots=True)
class StagedUpdate:
    """An installer downloaded and verified, waiting for a moment nobody is using the app."""

    version: str
    installer: Path
    sha256: str
    attempts: int = 0
    attempted_at: float = 0.0

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_INSTALL_ATTEMPTS


class GitHubReleaseChecker:
    def __init__(
        self,
        settings: ApplicationSettings,
        *,
        client: httpx.AsyncClient | None = None,
        api_root: str = GITHUB_API_ROOT,
    ) -> None:
        self._settings = settings
        self._client = client
        self._api_root = api_root.rstrip("/")

    async def check(self, *, current_version: str) -> UpdateInfo | None:
        owner = self._settings.github_owner.strip()
        repo = self._settings.github_repo.strip()
        if not owner or not repo:
            return None

        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            response = await client.get(
                f"{self._api_root}/repos/{owner}/{repo}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProjectFlowError("Verification de mise a jour impossible.") from exc
        finally:
            if close_client:
                await client.aclose()

        return _update_from_payload(payload, current_version=current_version)


class UpdateDownloader:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def download(
        self,
        asset: ReleaseAsset,
        *,
        version: str,
        destination_dir: Path | None = None,
        checksum_asset: ReleaseAsset | None = None,
        require_checksum: bool = True,
    ) -> Path:
        if not asset.download_url:
            raise ProjectFlowError("Aucun lien de telechargement disponible.")

        target_dir = destination_dir or updates_dir() / version
        filename = _safe_filename(asset.name or _filename_from_url(asset.download_url))
        target_path = target_dir / filename
        # Written beside its final name: an interrupted download never looks complete.
        partial_path = target_dir / f"{filename}.part"
        digest = sha256()
        size = 0
        complete = False
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60, follow_redirects=True)
        try:
            # The checksum first: without it the installer is not downloaded at all.
            checksum_text = await _download_checksum_text(
                client,
                checksum_asset,
                require_checksum=require_checksum,
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            async with client.stream("GET", asset.download_url) as response:
                response.raise_for_status()
                with partial_path.open("wb") as output:
                    async for chunk in response.aiter_bytes(_DOWNLOAD_CHUNK_BYTES):
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            if asset.size > 0 and size != asset.size:
                raise ProjectFlowError("Telechargement de la mise a jour incomplet.")
            _verify_sha256(digest.hexdigest(), asset.name, checksum_text)
            partial_path.replace(target_path)
            complete = True
        except httpx.HTTPError as exc:
            raise ProjectFlowError("Telechargement de la mise a jour impossible.") from exc
        finally:
            if not complete:
                partial_path.unlink(missing_ok=True)
            if close_client:
                await client.aclose()
        return target_path


def silent_updates_supported(
    *,
    system_name: str | None = None,
    frozen: bool | None = None,
) -> bool:
    """Only the built Windows app installs releases by itself, never a source checkout."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    return is_frozen and (system_name or system()).lower() == "windows"


def installs_unattended(asset: ReleaseAsset) -> bool:
    """Tell whether ``asset`` is the Windows installer, which runs without any window."""
    return _looks_like_windows_installer_name(asset.name)


def load_staged_update(
    *,
    current_version: str,
    directory: Path | None = None,
) -> StagedUpdate | None:
    """Return the downloaded release waiting on this poste; forget what is installed."""
    root = directory or updates_dir()
    staged = _read_staged_update(root)
    if staged is not None and _version_key(staged.version) > _version_key(current_version):
        return staged
    discard_staged_update(directory=root)
    return None


def save_staged_update(staged: StagedUpdate, *, directory: Path | None = None) -> None:
    root = directory or updates_dir()
    try:
        installer = staged.installer.relative_to(root)
    except ValueError as exc:
        raise ProjectFlowError("Mise a jour hors du dossier des telechargements.") from exc
    root.mkdir(parents=True, exist_ok=True)
    (root / STAGED_UPDATE_FILE).write_text(
        json.dumps(
            {
                "version": staged.version,
                "installer": installer.as_posix(),
                "sha256": staged.sha256,
                "attempts": staged.attempts,
                "attempted_at": staged.attempted_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _remove_downloads(root, keep=staged.installer.parent)


def discard_staged_update(*, directory: Path | None = None) -> None:
    """Forget the waiting release and delete every downloaded installer."""
    root = directory or updates_dir()
    if not root.is_dir():
        return
    (root / STAGED_UPDATE_FILE).unlink(missing_ok=True)
    _remove_downloads(root, keep=None)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_staged_installer(staged: StagedUpdate) -> bool:
    """Check that the installer is still the file whose checksum was verified."""
    try:
        return file_sha256(staged.installer).lower() == staged.sha256.lower()
    except OSError:
        return False


def install_in_progress(staged: StagedUpdate, *, now: float | None = None) -> bool:
    """Tell whether an installer was started a moment ago: a second start waits for it."""
    elapsed = (time.time() if now is None else now) - staged.attempted_at
    return staged.attempts > 0 and 0 <= elapsed < INSTALL_IN_PROGRESS_SECONDS


def begin_staged_install(
    staged: StagedUpdate,
    *,
    relaunch: RelaunchMode,
    now: float | None = None,
    directory: Path | None = None,
) -> InstallPlan:
    """Count this attempt, then return the command that installs without any window.

    The count is saved first: an installer that keeps failing is tried
    ``MAX_INSTALL_ATTEMPTS`` times, never at every start.
    """
    plan = prepare_install_plan(
        staged.installer,
        quiet=True,
        relaunch=relaunch,
        system_name="Windows",
    )
    save_staged_update(
        replace(
            staged,
            attempts=staged.attempts + 1,
            attempted_at=time.time() if now is None else now,
        ),
        directory=directory,
    )
    return plan


def select_platform_asset(
    update: UpdateInfo,
    *,
    system_name: str | None = None,
) -> ReleaseAsset | None:
    normalized_system = (system_name or system()).lower()
    if normalized_system == "windows":
        return _select_windows_asset(update.assets)
    if normalized_system == "darwin":
        return _first_matching_asset(update.assets, suffixes=(".dmg", ".zip"))
    return None


def select_checksum_asset(update: UpdateInfo, asset: ReleaseAsset) -> ReleaseAsset | None:
    expected_name = f"{asset.name}.sha256".lower()
    for candidate in update.assets:
        if candidate.name.lower() == expected_name and candidate.download_url:
            return candidate
    return _first_matching_asset(update.assets, suffixes=(".sha256", ".sha256sum", ".txt"))


def prepare_install_plan(
    asset_path: Path,
    *,
    current_executable: Path | None = None,
    process_id: int | None = None,
    script_dir: Path | None = None,
    system_name: str | None = None,
    quiet: bool = False,
    relaunch: RelaunchMode = "window",
) -> InstallPlan:
    """Command that installs ``asset_path``; ``quiet`` hides the installer's progress window."""
    normalized_system = (system_name or system()).lower()
    executable = current_executable or Path(sys.executable)
    if normalized_system == "windows":
        if asset_path.suffix.lower() != ".exe":
            raise ProjectFlowError("L'artefact Windows doit etre un fichier .exe.")
        if _looks_like_windows_installer(asset_path):
            return InstallPlan(
                command=(
                    str(asset_path),
                    "/VERYSILENT" if quiet else "/SILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                    # The installer starts the new version itself (/RELAUNCH), once.
                    "/NORESTARTAPPLICATIONS",
                    # A silent run reuses the tasks of the last manual install: never
                    # « tout reinitialiser » again without the user ticking it.
                    "/MERGETASKS=!cleaninstall",
                    f"/RELAUNCH={relaunch}",
                ),
                should_quit_app=True,
            )
        script_path = _write_windows_legacy_update_script(script_dir or updates_dir())
        return InstallPlan(
            command=(
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-Source",
                str(asset_path),
                "-Target",
                str(executable),
                "-ProcessId",
                str(process_id or 0),
            ),
            should_quit_app=True,
        )
    if normalized_system == "darwin":
        if asset_path.suffix.lower() not in {".dmg", ".zip"}:
            raise ProjectFlowError("L'artefact macOS doit etre un fichier .dmg ou .zip.")
        return InstallPlan(command=("open", str(asset_path)), should_quit_app=False)
    raise ProjectFlowError("Installation automatique non supportee sur cette plateforme.")


def launch_install_plan(plan: InstallPlan) -> None:
    if system().lower() == "windows":
        # The installer starts the new version: a one-file build must unpack itself
        # again instead of reusing the files of this process, deleted when it exits.
        subprocess.Popen(
            plan.command,
            start_new_session=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            close_fds=True,
        )
        return
    subprocess.Popen(plan.command, start_new_session=True)


def _update_from_payload(payload: object, *, current_version: str) -> UpdateInfo | None:
    if not isinstance(payload, dict):
        raise ProjectFlowError("Reponse GitHub Releases invalide.")

    tag_name = payload.get("tag_name")
    html_url = payload.get("html_url")
    if not isinstance(tag_name, str) or not isinstance(html_url, str):
        raise ProjectFlowError("Release GitHub incomplete.")

    latest_version = tag_name.removeprefix("v")
    if _version_key(latest_version) <= _version_key(current_version):
        return None

    assets = payload.get("assets", [])
    release_assets = tuple(
        _asset_from_payload(asset) for asset in assets if isinstance(asset, dict)
    )
    body = payload.get("body")
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        release_url=html_url,
        assets=release_assets,
        release_notes=body.strip() if isinstance(body, str) else "",
    )


def _asset_from_payload(payload: dict[object, object]) -> ReleaseAsset:
    name = payload.get("name")
    download_url = payload.get("browser_download_url")
    size = payload.get("size")
    return ReleaseAsset(
        name=name if isinstance(name, str) else "",
        download_url=download_url if isinstance(download_url, str) else "",
        size=size if isinstance(size, int) else 0,
    )


def _first_matching_asset(
    assets: tuple[ReleaseAsset, ...],
    *,
    suffixes: tuple[str, ...],
) -> ReleaseAsset | None:
    for asset in assets:
        if asset.name.lower().endswith(suffixes) and asset.download_url:
            return asset
    return None


def _select_windows_asset(assets: tuple[ReleaseAsset, ...]) -> ReleaseAsset | None:
    installers = tuple(
        asset
        for asset in assets
        if asset.name.lower().endswith(".exe")
        and asset.download_url
        and _looks_like_windows_installer_name(asset.name)
    )
    if installers:
        return installers[0]
    return _first_matching_asset(assets, suffixes=(".exe",))


async def _download_checksum_text(
    client: httpx.AsyncClient,
    checksum_asset: ReleaseAsset | None,
    *,
    require_checksum: bool,
) -> str | None:
    if checksum_asset is None:
        if require_checksum:
            raise ProjectFlowError("Signature SHA256 de la mise a jour introuvable.")
        return None
    if not checksum_asset.download_url:
        raise ProjectFlowError("Lien SHA256 de la mise a jour introuvable.")
    response = await client.get(checksum_asset.download_url)
    response.raise_for_status()
    return response.text


def _verify_sha256(actual: str, asset_name: str, checksum_text: str | None) -> None:
    if checksum_text is None:
        return
    expected = _expected_sha256(checksum_text, asset_name)
    if actual.lower() != expected.lower():
        raise ProjectFlowError("Verification SHA256 de la mise a jour echouee.")


def _read_staged_update(root: Path) -> StagedUpdate | None:
    try:
        payload = json.loads((root / STAGED_UPDATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    installer = payload.get("installer")
    digest = payload.get("sha256")
    attempts = payload.get("attempts", 0)
    attempted_at = payload.get("attempted_at", 0.0)
    if not (
        isinstance(version, str)
        and isinstance(installer, str)
        and isinstance(digest, str)
        and isinstance(attempts, int)
        and isinstance(attempted_at, int | float)
    ):
        return None
    relative = Path(installer)
    # Only an installer of the downloads folder: the file never names another program.
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        return None
    return StagedUpdate(
        version=version,
        installer=root / relative,
        sha256=digest,
        attempts=max(0, attempts),
        attempted_at=float(attempted_at),
    )


def _remove_downloads(root: Path, *, keep: Path | None) -> None:
    """Delete the per-version download folders, except ``keep``."""
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if child == keep or not child.is_dir():
            continue
        if re.match(r"^v?\d+\.\d+\.\d+", child.name) is None:
            continue
        shutil.rmtree(child, ignore_errors=True)


def _expected_sha256(checksum_text: str, asset_name: str) -> str:
    target = Path(asset_name).name
    fallback = ""
    for raw_line in checksum_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(?P<hash>[A-Fa-f0-9]{64})(?:\s+\*?(?P<name>.+))?$", line)
        if match is None:
            continue
        digest = match.group("hash")
        name = (match.group("name") or "").strip()
        if not fallback:
            fallback = digest
        if not name or Path(name).name == target:
            return digest
    if fallback:
        return fallback
    raise ProjectFlowError("Fichier SHA256 de la mise a jour invalide.")


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    candidate = Path(unquote(parsed.path)).name
    return candidate or "projectflow-update"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", value).strip(" .") or "projectflow-update"


def _looks_like_windows_installer(asset_path: Path) -> bool:
    return _looks_like_windows_installer_name(asset_path.name)


def _looks_like_windows_installer_name(name: str) -> bool:
    normalized = name.lower()
    return "setup" in normalized or "installer" in normalized or "installateur" in normalized


def _write_windows_legacy_update_script(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script_path = directory / "legacy_projectflow_update.ps1"
    # Not $Pid: PowerShell's read-only automatic variable made the script fail at once.
    script_path.write_text(
        """param(
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Target,
  [Parameter(Mandatory=$true)][int]$ProcessId
)
if ($ProcessId -gt 0) { Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue }
Copy-Item -LiteralPath $Source -Destination $Target -Force
Start-Process -FilePath $Target
""",
        encoding="utf-8",
    )
    return script_path


def _version_key(version: str) -> tuple[int, int, int]:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
