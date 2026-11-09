from __future__ import annotations

import sys
from pathlib import Path

import pytest

from projectflow.auth import token_storage
from projectflow.auth.token_storage import ENCRYPTED_PREFIX, TokenCacheStorage


def test_token_storage_falls_back_to_file_when_keyring_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    monkeypatch.setattr(token_storage, "_data_protection_module", lambda: None)
    fallback_path = tmp_path / "msal-cache.json"
    storage = TokenCacheStorage(fallback_path=fallback_path)

    storage.save("cache")

    assert fallback_path.read_text(encoding="utf-8") == "cache"


def test_fallback_file_is_encrypted_when_data_protection_is_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    monkeypatch.setattr(token_storage, "_data_protection_module", FakeDataProtection)
    fallback_path = tmp_path / "msal-cache.json"
    storage = TokenCacheStorage(fallback_path=fallback_path)

    storage.save('{"RefreshToken": "secret"}')

    content = fallback_path.read_bytes()
    assert content.startswith(ENCRYPTED_PREFIX)
    assert b"secret" not in content
    assert storage.load() == '{"RefreshToken": "secret"}'


def test_clear_cache_from_an_earlier_version_is_read_then_encrypted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    monkeypatch.setattr(token_storage, "_data_protection_module", FakeDataProtection)
    fallback_path = tmp_path / "msal-cache.json"
    fallback_path.write_text('{"RefreshToken": "secret"}', encoding="utf-8")
    storage = TokenCacheStorage(fallback_path=fallback_path)

    # The connection keeps working after the update, without a new sign-in.
    loaded = storage.load()
    assert loaded == '{"RefreshToken": "secret"}'

    # MSAL saves the cache after each token refresh: the file is then encrypted.
    storage.save(loaded)
    assert fallback_path.read_bytes().startswith(ENCRYPTED_PREFIX)
    assert storage.load() == loaded


def test_unreadable_encrypted_cache_asks_for_a_new_sign_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    monkeypatch.setattr(token_storage, "_data_protection_module", RefusingDataProtection)
    fallback_path = tmp_path / "msal-cache.json"
    fallback_path.write_bytes(ENCRYPTED_PREFIX + b"from-another-account")
    storage = TokenCacheStorage(fallback_path=fallback_path)

    assert storage.load() == ""


def test_failed_encryption_keeps_the_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    monkeypatch.setattr(token_storage, "_data_protection_module", RefusingDataProtection)
    storage = TokenCacheStorage(fallback_path=tmp_path / "msal-cache.json")

    storage.save("cache")

    assert storage.load() == "cache"


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI exists only on Windows")
def test_windows_data_protection_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    fallback_path = tmp_path / "msal-cache.json"
    storage = TokenCacheStorage(fallback_path=fallback_path)

    storage.save('{"RefreshToken": "secret"}')

    content = fallback_path.read_bytes()
    assert content.startswith(ENCRYPTED_PREFIX)
    assert b"secret" not in content
    assert storage.load() == '{"RefreshToken": "secret"}'


def test_token_storage_loads_fallback_file_when_keyring_load_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    fallback_path = tmp_path / "msal-cache.json"
    fallback_path.write_text("cache", encoding="utf-8")
    storage = TokenCacheStorage(fallback_path=fallback_path)

    assert storage.load() == "cache"


def test_oversized_cache_replaces_stale_keyring_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    keyring = SizeLimitedKeyring()
    keyring.stored = "stale-small-cache"
    monkeypatch.setattr(token_storage, "_keyring_module", lambda: keyring)
    storage = TokenCacheStorage(fallback_path=tmp_path / "msal-cache.json")

    storage.save("x" * 5000)

    assert keyring.stored is None
    assert storage.load() == "x" * 5000
    assert not (tmp_path / "msal-cache.tmp").exists()


def test_keyring_save_removes_stale_fallback_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    keyring = SizeLimitedKeyring()
    monkeypatch.setattr(token_storage, "_keyring_module", lambda: keyring)
    fallback_path = tmp_path / "msal-cache.json"
    fallback_path.write_text("stale", encoding="utf-8")
    storage = TokenCacheStorage(fallback_path=fallback_path)

    storage.save("small")

    assert not fallback_path.exists()
    assert storage.load() == "small"


class SizeLimitedKeyring:
    def __init__(self) -> None:
        self.stored: str | None = None

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.stored

    def set_password(self, service_name: str, username: str, password: str) -> None:
        if len(password) > 1280:
            raise OSError(1783, "CredWrite")
        self.stored = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.stored = None


class FailingKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise OSError

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise OSError

    def delete_password(self, service_name: str, username: str) -> None:
        raise OSError


def failing_keyring() -> FailingKeyring:
    return FailingKeyring()


class FakeDataProtection:
    """Reversible stand-in for win32crypt, bound to nothing."""

    @staticmethod
    def CryptProtectData(data: bytes, *_options: object) -> bytes:  # noqa: N802
        return b"sealed:" + data[::-1]

    @staticmethod
    def CryptUnprotectData(data: bytes, *_options: object) -> tuple[str, bytes]:  # noqa: N802
        if not data.startswith(b"sealed:"):
            raise OSError(13, "CryptUnprotectData")
        return "", data.removeprefix(b"sealed:")[::-1]


class RefusingDataProtection:
    @staticmethod
    def CryptProtectData(*_args: object) -> bytes:  # noqa: N802
        raise OSError(13, "CryptProtectData")

    @staticmethod
    def CryptUnprotectData(*_args: object) -> tuple[str, bytes]:  # noqa: N802
        raise OSError(13, "CryptUnprotectData")
