from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from projectflow.logging import get_logger
from projectflow.platform.paths import data_dir

KEYRING_SERVICE = "ProjectFlow Automator"
KEYRING_ACCOUNT = "microsoft-token-cache"
# On Windows the file copy of the cache is encrypted for the Windows account (DPAPI),
# as the Credential Manager would have kept it: a copied file (backup, synchronised
# folder, another account or computer) cannot be read. A file without this prefix was
# written in clear by an earlier version: it is read, then encrypted at the next save.
ENCRYPTED_PREFIX = b"projectflow-dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DataProtectionProtocol(Protocol):
    """pywin32's win32crypt: the arguments after ``data`` are Windows' optional ones."""

    def CryptProtectData(self, data: bytes, *options: object) -> bytes:  # noqa: N802
        """Encrypt for the current Windows account."""

    def CryptUnprotectData(self, data: bytes, *options: object) -> tuple[str, bytes]:  # noqa: N802
        """Decrypt what CryptProtectData encrypted for this account; (description, data)."""


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
            content = self._fallback_path.read_bytes()
        except OSError:
            return ""
        return _decrypted(content)

    def save(self, value: str) -> None:
        # Keep a single copy: load() prefers the keyring, so a stale copy there
        # would hide the current connection saved in the file, and vice versa.
        if self._save_to_keyring(value):
            self._delete_fallback()
            return
        # Windows Credential Manager refuses secrets above ~1280 characters,
        # which a Microsoft token cache always exceeds.
        self._delete_from_keyring()
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._fallback_path.with_suffix(".tmp")
        temporary.write_bytes(_encrypted(value))
        # Another connection may read the cache concurrently: never expose half a file.
        temporary.replace(self._fallback_path)

    def clear(self) -> None:
        self._delete_from_keyring()
        self._delete_fallback()

    def _delete_fallback(self) -> None:
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


def _encrypted(value: str) -> bytes:
    content = value.encode("utf-8")
    data_protection = _data_protection_module()
    if data_protection is None:
        # macOS keeps the cache in its keychain: this file is only its last resort there.
        return content
    try:
        sealed = data_protection.CryptProtectData(
            content, None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN
        )
    except CREDENTIAL_ERRORS as exc:
        # Never lose the connection over it: the file stays as earlier versions wrote it.
        get_logger(__name__).warning("auth.cache.encrypt_failed", error=type(exc).__name__)
        return content
    return ENCRYPTED_PREFIX + sealed


def _decrypted(content: bytes) -> str:
    try:
        if not content.startswith(ENCRYPTED_PREFIX):
            return content.decode("utf-8")
        data_protection = _data_protection_module()
        if data_protection is None:
            return ""
        _description, data = data_protection.CryptUnprotectData(
            content.removeprefix(ENCRYPTED_PREFIX), None, None, None, _CRYPTPROTECT_UI_FORBIDDEN
        )
        return data.decode("utf-8")
    except CREDENTIAL_ERRORS as exc:
        # Encrypted by another Windows account or computer: the user signs in again.
        get_logger(__name__).warning("auth.cache.unreadable", error=type(exc).__name__)
        return ""


def _data_protection_module() -> DataProtectionProtocol | None:
    try:
        return cast("DataProtectionProtocol", import_module("win32crypt"))
    except ImportError:
        return None


def _keyring_module() -> KeyringProtocol | None:
    try:
        return cast("KeyringProtocol", import_module("keyring"))
    except ImportError:
        return None
