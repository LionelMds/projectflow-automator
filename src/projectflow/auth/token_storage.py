from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from projectflow.platform.paths import data_dir

KEYRING_SERVICE = "ProjectFlow Automator"
KEYRING_ACCOUNT = "microsoft-token-cache"


class KeyringProtocol(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None:
        """Return a stored secret."""

    def set_password(self, service_name: str, username: str, password: str) -> None:
        """Store a secret."""

    def delete_password(self, service_name: str, username: str) -> None:
        """Delete a stored secret."""


class TokenCacheStorage:
    def __init__(
        self,
        *,
        service: str = KEYRING_SERVICE,
        account: str = KEYRING_ACCOUNT,
        fallback_path: Path | None = None,
    ) -> None:
        self._service = service
        self._account = account
        self._fallback_path = fallback_path or data_dir() / "msal-cache.json"

    def load(self) -> str:
        keyring_value = self._load_from_keyring()
        if keyring_value:
            return keyring_value
        try:
            return self._fallback_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save(self, value: str) -> None:
        if self._save_to_keyring(value):
            return
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_path.write_text(value, encoding="utf-8")

    def clear(self) -> None:
        self._delete_from_keyring()
        try:
            self._fallback_path.unlink()
        except FileNotFoundError:
            return

    def _load_from_keyring(self) -> str:
        keyring = _keyring_module()
        if keyring is None:
            return ""
        try:
            value = keyring.get_password(self._service, self._account)
        except CREDENTIAL_ERRORS:
            return ""
        return value if isinstance(value, str) else ""

    def _save_to_keyring(self, value: str) -> bool:
        keyring = _keyring_module()
        if keyring is None:
            return False
        try:
            keyring.set_password(self._service, self._account, value)
        except CREDENTIAL_ERRORS:
            return False
        return True

    def _delete_from_keyring(self) -> None:
        keyring = _keyring_module()
        if keyring is None:
            return
        try:
            keyring.delete_password(self._service, self._account)
        except CREDENTIAL_ERRORS:
            return


def _credential_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError, RuntimeError, ValueError]
    for module_name, attribute_name in [
        ("keyring.errors", "KeyringError"),
        ("pywintypes", "error"),
        ("win32ctypes.pywin32.pywintypes", "error"),
    ]:
        try:
            error_type = getattr(import_module(module_name), attribute_name)
        except (AttributeError, ImportError):
            continue
        if isinstance(error_type, type) and issubclass(error_type, BaseException):
            errors.append(error_type)
    return tuple(errors)


CREDENTIAL_ERRORS = _credential_errors()


def _keyring_module() -> KeyringProtocol | None:
    try:
        return cast("KeyringProtocol", import_module("keyring"))
    except ImportError:
        return None
