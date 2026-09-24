from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.auth import token_storage
from projectflow.auth.token_storage import TokenCacheStorage


def test_token_storage_falls_back_to_file_when_keyring_save_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(token_storage, "_keyring_module", failing_keyring)
    fallback_path = tmp_path / "msal-cache.json"
    storage = TokenCacheStorage(fallback_path=fallback_path)

    storage.save("cache")

    assert fallback_path.read_text(encoding="utf-8") == "cache"


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
