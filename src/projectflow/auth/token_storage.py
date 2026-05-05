from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import keyring

from projectflow.exceptions import AuthError

SERVICE_NAME = "ProjectFlow Automator"


class TokenStorage(Protocol):
    def load(self) -> str | None:
        """Return the serialized MSAL token cache, if present."""

    def save(self, serialized_cache: str) -> None:
        """Persist the serialized MSAL token cache."""

    def clear(self) -> None:
        """Delete the serialized MSAL token cache."""


@dataclass(frozen=True, slots=True)
class KeyringTokenStorage:
    account_name: str
    service_name: str = SERVICE_NAME

    def load(self) -> str | None:
        try:
            return keyring.get_password(self.service_name, self.account_name)
        except keyring.errors.KeyringError as exc:
            raise AuthError("Impossible de lire le cache Microsoft chiffre.") from exc

    def save(self, serialized_cache: str) -> None:
        try:
            keyring.set_password(self.service_name, self.account_name, serialized_cache)
        except keyring.errors.KeyringError as exc:
            raise AuthError("Impossible d'enregistrer le cache Microsoft chiffre.") from exc

    def clear(self) -> None:
        try:
            keyring.delete_password(self.service_name, self.account_name)
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as exc:
            raise AuthError("Impossible d'effacer le cache Microsoft chiffre.") from exc
