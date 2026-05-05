from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from platform import system
from urllib.parse import unquote, urlparse

import httpx

from projectflow.application_settings import ApplicationSettings
from projectflow.exceptions import ProjectFlowError
from projectflow.platform.paths import updates_dir

GITHUB_API_ROOT = "https://api.github.com"


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


@dataclass(frozen=True, slots=True)
class InstallPlan:
    command: tuple[str, ...]
    should_quit_app: bool


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
    ) -> Path:
        if not asset.download_url:
            raise ProjectFlowError("Aucun lien de telechargement disponible.")

        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60, follow_redirects=True)
        try:
            response = await client.get(asset.download_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProjectFlowError("Telechargement de la mise a jour impossible.") from exc
        finally:
            if close_client:
                await client.aclose()

        target_dir = destination_dir or updates_dir() / version
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(asset.name or _filename_from_url(asset.download_url))
        target_path = target_dir / filename
        target_path.write_bytes(response.content)
        return target_path


def select_platform_asset(
    update: UpdateInfo,
    *,
    system_name: str | None = None,
) -> ReleaseAsset | None:
    normalized_system = (system_name or system()).lower()
    if normalized_system == "windows":
        return _first_matching_asset(update.assets, suffixes=(".exe",))
    if normalized_system == "darwin":
        return _first_matching_asset(update.assets, suffixes=(".dmg", ".zip"))
    return None


def prepare_install_plan(
    asset_path: Path,
    *,
    current_executable: Path | None = None,
    process_id: int | None = None,
    script_dir: Path | None = None,
    system_name: str | None = None,
) -> InstallPlan:
    normalized_system = (system_name or system()).lower()
    executable = current_executable or Path(sys.executable)
    if normalized_system == "windows":
        if asset_path.suffix.lower() != ".exe":
            raise ProjectFlowError("L'artefact Windows doit etre un fichier .exe.")
        script_path = _write_windows_install_script(script_dir or updates_dir())
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
                "-Pid",
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
        subprocess.Popen(
            plan.command,
            start_new_session=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
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
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        release_url=html_url,
        assets=release_assets,
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


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    candidate = Path(unquote(parsed.path)).name
    return candidate or "projectflow-update"


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_. -]+", "_", value).strip(" .") or "projectflow-update"


def _write_windows_install_script(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    script_path = directory / "install_projectflow_update.ps1"
    script_path.write_text(
        """param(
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Target,
  [Parameter(Mandatory=$true)][int]$Pid
)
if ($Pid -gt 0) { Wait-Process -Id $Pid -ErrorAction SilentlyContinue }
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
