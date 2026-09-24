from __future__ import annotations

import pytest

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
