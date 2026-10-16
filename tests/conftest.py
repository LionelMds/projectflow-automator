from __future__ import annotations

from pathlib import Path

import pytest

from projectflow.auth import token_storage
from projectflow.cad import license_storage


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


@pytest.fixture(autouse=True)
def memory_license_keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyring:
    """Never read or write the real credential store from the tests."""
    keyring = MemoryKeyring()
    monkeypatch.setattr(license_storage, "_keyring_module", lambda: keyring)
    return keyring


@pytest.fixture(autouse=True)
def memory_microsoft_token_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> MemoryKeyring:
    """Never read, replace or erase this computer's Microsoft connection from the tests."""
    keyring = MemoryKeyring()
    monkeypatch.setattr(token_storage, "_keyring_module", lambda: keyring)
    monkeypatch.setattr(token_storage, "data_dir", lambda: tmp_path / "token-data")
    return keyring
