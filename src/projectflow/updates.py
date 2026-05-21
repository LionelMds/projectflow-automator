from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
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
    release_notes: str = ""


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
        checksum_asset: ReleaseAsset | None = None,
        require_checksum: bool = True,
    ) -> Path:
        if not asset.download_url:
            raise ProjectFlowError("Aucun lien de telechargement disponible.")

        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60, follow_redirects=True)
        try:
            response = await client.get(asset.download_url)
            response.raise_for_status()
            checksum_text = await _download_checksum_text(
                client,
                checksum_asset,
                require_checksum=require_checksum,
            )
        except httpx.HTTPError as exc:
            raise ProjectFlowError("Telechargement de la mise a jour impossible.") from exc
        finally:
            if close_client:
                await client.aclose()

        if asset.size > 0 and len(response.content) != asset.size:
            raise ProjectFlowError("Telechargement de la mise a jour incomplet.")
        _verify_sha256(response.content, asset.name, checksum_text)

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
) -> InstallPlan:
    normalized_system = (system_name or system()).lower()
    executable = current_executable or Path(sys.executable)
    if normalized_system == "windows":
        if asset_path.suffix.lower() != ".exe":
            raise ProjectFlowError("L'artefact Windows doit etre un fichier .exe.")
        if _looks_like_windows_installer(asset_path):
            return InstallPlan(
                command=(
                    str(asset_path),
                    "/SILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                    "/RESTARTAPPLICATIONS",
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


def _verify_sha256(content: bytes, asset_name: str, checksum_text: str | None) -> None:
    if checksum_text is None:
        return
    expected = _expected_sha256(checksum_text, asset_name)
    actual = sha256(content).hexdigest()
    if actual.lower() != expected.lower():
        raise ProjectFlowError("Verification SHA256 de la mise a jour echouee.")


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
