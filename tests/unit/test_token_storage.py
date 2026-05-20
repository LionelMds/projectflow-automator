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


class FailingKeyring:
    def get_password(self, service_name: str, username: str) -> str | None:
        raise OSError

    def set_password(self, service_name: str, username: str, password: str) -> None:
        raise OSError

    def delete_password(self, service_name: str, username: str) -> None:
        raise OSError


def failing_keyring() -> FailingKeyring:
    return FailingKeyring()
