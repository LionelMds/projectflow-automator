from __future__ import annotations

import pytest

from projectflow.config import OutlookConfig
from projectflow.exceptions import ConfigError
from projectflow.outlook.local import create_local_outlook_client
from projectflow.outlook.windows import WindowsLocalOutlookClient


class FakeCollection:
    def __init__(self, items: list[object] | None = None) -> None:
        self.items = items or []

    @property
    def Count(self) -> int:  # noqa: N802
        return len(self.items)

    def Item(self, index: int) -> object:  # noqa: N802
        return self.items[index - 1]

    def Add(self, name: str) -> object:  # noqa: N802
        folder = FakeFolder(name)
        self.items.append(folder)
        return folder


class FakeFolder:
    def __init__(self, name: str) -> None:
        self.Name = name
        self.Folders = FakeCollection()


class FakeStore:
    def __init__(self, store_id: str, display_name: str) -> None:
        self.StoreID = store_id
        self.DisplayName = display_name
        self.root = FakeFolder(display_name)
        self.inbox = FakeFolder("Boite de reception")

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root

    def GetDefaultFolder(self, folder_type: int) -> FakeFolder:  # noqa: N802
        assert folder_type == 6
        return self.inbox


class FakeAccount:
    def __init__(self, store: FakeStore, email: str) -> None:
        self.DeliveryStore = store
        self.SmtpAddress = email


class FakeNamespace:
    def __init__(self, stores: list[FakeStore], accounts: list[FakeAccount]) -> None:
        self.Stores = FakeCollection(stores)
        self.Accounts = FakeCollection(accounts)


class FakeApp:
    def __init__(self, namespace: FakeNamespace) -> None:
        self.namespace = namespace

    def GetNamespace(self, name: str) -> FakeNamespace:  # noqa: N802
        assert name == "MAPI"
        return self.namespace


def test_windows_outlook_lists_local_stores_and_account_emails() -> None:
    store = FakeStore("store-1", "Boite Balz")
    archive = FakeStore("store-2", "Archives locales")
    namespace = FakeNamespace([store, archive], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(app_factory=lambda: FakeApp(namespace))

    accounts = client.list_accounts_sync()

    assert accounts[0].id == "store-1"
    assert accounts[0].label == "Boite Balz (lionel@balzmetal.ch)"
    assert accounts[1].label == "Archives locales"


def test_windows_outlook_validates_target_without_creating_folders() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        app_factory=lambda: FakeApp(namespace),
    )

    client.validate_target_sync()

    assert store.root.Folders.Count == 0


def test_windows_outlook_validates_inbox_target_without_creating_folders() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        base_folder="inbox",
        app_factory=lambda: FakeApp(namespace),
    )

    client.validate_target_sync()

    assert store.root.Folders.Count == 0
    assert store.inbox.Folders.Count == 0


@pytest.mark.asyncio
async def test_windows_outlook_creates_missing_folder_path_once() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        app_factory=lambda: FakeApp(namespace),
    )

    await client.ensure_folder_path(["2026", "2026-4995"])
    await client.ensure_folder_path(["2026", "2026-4995"])

    year_folder = store.root.Folders.Item(1)
    assert isinstance(year_folder, FakeFolder)
    project_folder = year_folder.Folders.Item(1)
    assert isinstance(project_folder, FakeFolder)
    assert year_folder.Name == "2026"
    assert project_folder.Name == "2026-4995"
    assert store.root.Folders.Count == 1
    assert year_folder.Folders.Count == 1


@pytest.mark.asyncio
async def test_windows_outlook_creates_folder_path_under_inbox() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        base_folder="inbox",
        app_factory=lambda: FakeApp(namespace),
    )

    await client.ensure_folder_path(["2026", "2026-4995"])

    assert store.root.Folders.Count == 0
    year_folder = store.inbox.Folders.Item(1)
    assert isinstance(year_folder, FakeFolder)
    assert year_folder.Name == "2026"
    assert year_folder.Folders.Count == 1


@pytest.mark.asyncio
async def test_windows_outlook_can_select_store_by_email_label() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_mailbox="Boite Balz (lionel@balzmetal.ch)",
        app_factory=lambda: FakeApp(namespace),
    )

    await client.ensure_folder_path(["2026"])

    assert store.root.Folders.Count == 1


def test_local_outlook_requires_selected_account_when_enabled() -> None:
    config = OutlookConfig(enabled=True)

    with pytest.raises(ConfigError, match="Selectionnez un compte Outlook"):
        create_local_outlook_client(config)
