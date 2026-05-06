from __future__ import annotations

import sys
from typing import Protocol

from projectflow.config import OutlookConfig
from projectflow.exceptions import ConfigError
from projectflow.outlook.models import OutlookAccount
from projectflow.outlook.windows import WindowsLocalOutlookClient


class LocalOutlookGateway(Protocol):
    async def validate_target(self) -> None:
        """Validate that the configured Outlook target is available."""

    async def ensure_folder_path(self, names: list[str]) -> object:
        """Ensure the nested Outlook folder path exists."""


def create_local_outlook_client(config: OutlookConfig) -> LocalOutlookGateway | None:
    if not config.enabled:
        return None
    if not config.target_store_id and not config.target_mailbox:
        raise ConfigError("Selectionnez un compte Outlook dans les parametres.")
    platform_name = _platform_name()
    if platform_name.startswith("win"):
        return WindowsLocalOutlookClient(
            target_store_id=config.target_store_id,
            target_mailbox=config.target_mailbox,
            base_folder=config.target_base_folder,
        )
    if platform_name == "darwin":
        raise ConfigError(
            "Outlook local macOS n'est pas encore disponible. "
            "Desactivez Outlook ou utilisez un poste Windows Outlook classique.",
        )
    raise ConfigError("Outlook local n'est disponible que sur Windows et macOS.")


def detect_local_outlook_accounts() -> list[OutlookAccount]:
    platform_name = _platform_name()
    if platform_name.startswith("win"):
        return WindowsLocalOutlookClient().list_accounts_sync()
    if platform_name == "darwin":
        raise ConfigError(
            "Detection Outlook macOS pas encore disponible dans ProjectFlow.",
        )
    raise ConfigError("Detection Outlook disponible uniquement sur Windows et macOS.")


def validate_local_outlook_account(*, store_id: str, mailbox: str, base_folder: str) -> None:
    platform_name = _platform_name()
    if platform_name.startswith("win"):
        WindowsLocalOutlookClient(
            target_store_id=store_id,
            target_mailbox=mailbox,
            base_folder=base_folder,
        ).validate_target_sync()
        return
    if platform_name == "darwin":
        raise ConfigError(
            "Validation Outlook macOS pas encore disponible dans ProjectFlow.",
        )
    raise ConfigError("Validation Outlook disponible uniquement sur Windows et macOS.")


def _platform_name() -> str:
    return sys.platform
