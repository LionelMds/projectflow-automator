"""SolidWorks Document Manager license key, kept in the system credential store."""

from __future__ import annotations

from importlib import import_module
from typing import cast

from projectflow.auth.token_storage import CREDENTIAL_ERRORS, KEYRING_SERVICE, KeyringProtocol

SOLIDWORKS_LICENSE_ACCOUNT = "solidworks-document-manager-license"


class SolidWorksLicenseStorage:
    """Store the key with keyring only: a license key never goes to a plain file."""

    def __init__(
        self,
        *,
        service: str = KEYRING_SERVICE,
        account: str = SOLIDWORKS_LICENSE_ACCOUNT,
    ) -> None:
        self._service = service
        self._account = account

    def load(self) -> str:
        keyring = _keyring_module()
        if keyring is None:
            return ""
        try:
            value = keyring.get_password(self._service, self._account)
        except CREDENTIAL_ERRORS:
            return ""
        return value.strip() if isinstance(value, str) else ""

    def save(self, value: str) -> bool:
        keyring = _keyring_module()
        if keyring is None:
            return False
        try:
            keyring.set_password(self._service, self._account, value.strip())
        except CREDENTIAL_ERRORS:
            return False
        return True

    def clear(self) -> bool:
        keyring = _keyring_module()
        if keyring is None:
            return False
        try:
            keyring.delete_password(self._service, self._account)
        except CREDENTIAL_ERRORS:
            return self.load() == ""
        return True


def load_solidworks_license_key() -> str:
    return SolidWorksLicenseStorage().load()


def _keyring_module() -> KeyringProtocol | None:
    try:
        return cast("KeyringProtocol", import_module("keyring"))
    except ImportError:
        return None
