from __future__ import annotations

from collections.abc import Sequence

import pytest

from projectflow.config import OutlookConfig
from projectflow.exceptions import ConfigError
from projectflow.outlook.local import create_local_outlook_client
from projectflow.outlook.macos_mail import (
    DELETE_MAILBOX_SCRIPT,
    ENSURE_MAILBOX_SCRIPT,
    MacNativeMailClient,
)
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
        folder = FakeFolder(name, parent=self)
        self.items.append(folder)
        return folder


class FakeFolder:
    def __init__(self, name: str, parent: FakeCollection | None = None) -> None:
        self.Name = name
        self.EntryID = f"id-{name}-{id(self)}"
        self.Folders = FakeCollection()
        self.parent = parent

    def Delete(self) -> None:  # noqa: N802
        if self.parent is not None:
            self.parent.items.remove(self)


class FakeStore:
    def __init__(self, store_id: str, display_name: str) -> None:
        self.StoreID = store_id
        self.DisplayName = display_name
        self.root = FakeFolder(display_name)
        self.inbox = FakeFolder("Boite de reception")
        self.deleted: FakeFolder | None = None

    def GetRootFolder(self) -> FakeFolder:  # noqa: N802
        return self.root

    def GetDefaultFolder(self, folder_type: int) -> FakeFolder:  # noqa: N802
        if folder_type == 3 and self.deleted is not None:
            return self.deleted
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
async def test_windows_outlook_renames_existing_project_folder_when_designation_changes() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        app_factory=lambda: FakeApp(namespace),
    )

    await client.ensure_folder_path(["2026", "2026-4995 (Ancien nom)"])
    await client.ensure_folder_path(["2026", "2026-4995 (Nouveau nom)"])

    year_folder = store.root.Folders.Item(1)
    assert isinstance(year_folder, FakeFolder)
    project_folder = year_folder.Folders.Item(1)
    assert isinstance(project_folder, FakeFolder)
    assert project_folder.Name == "2026-4995 (Nouveau nom)"
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
async def test_windows_outlook_deletes_project_folder_but_keeps_year_folder() -> None:
    store = FakeStore("store-1", "Boite Balz")
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        app_factory=lambda: FakeApp(namespace),
    )
    await client.ensure_folder_path(["2026", "2026-4995 (Escalier)"])

    deleted = await client.delete_folder_path(["2026", "2026-4995"])

    year_folder = store.root.Folders.Item(1)
    assert isinstance(year_folder, FakeFolder)
    assert deleted is True
    assert store.root.Folders.Count == 1
    assert year_folder.Folders.Count == 0


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


def test_macos_mail_lists_accounts_and_local_mailbox() -> None:
    client = MacNativeMailClient(
        script_runner=lambda _script, _args: (
            "Balz Metal\tBalz Metal\tlionel@balzmetal.ch\non-my-mac\tSur mon Mac\t"
        ),
    )

    accounts = client.list_accounts_sync()

    assert accounts[0].id == "Balz Metal"
    assert accounts[0].label == "Balz Metal (lionel@balzmetal.ch)"
    assert accounts[1].id == "on-my-mac"


@pytest.mark.asyncio
async def test_macos_mail_creates_nested_mailbox_on_selected_account() -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_runner(script: str, args: Sequence[str]) -> str:
        calls.append((script, list(args)))
        if script == ENSURE_MAILBOX_SCRIPT:
            return ""
        return "Balz Metal\tBalz Metal\tlionel@balzmetal.ch"

    client = MacNativeMailClient(
        target_store_id="Balz Metal",
        script_runner=fake_runner,
    )

    await client.ensure_folder_path(["2026", "2026-4995 / Escalier"])

    assert calls[-1] == (ENSURE_MAILBOX_SCRIPT, ["Balz Metal", "2026/2026-4995 - Escalier"])


@pytest.mark.asyncio
async def test_macos_mail_can_create_under_inbox() -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_runner(script: str, args: Sequence[str]) -> str:
        calls.append((script, list(args)))
        if script == ENSURE_MAILBOX_SCRIPT:
            return ""
        return "Balz Metal\tBalz Metal\tlionel@balzmetal.ch"

    client = MacNativeMailClient(
        target_store_id="Balz Metal",
        base_folder="inbox",
        script_runner=fake_runner,
    )

    await client.ensure_folder_path(["2026", "2026-4995"])

    assert calls[-1] == (ENSURE_MAILBOX_SCRIPT, ["Balz Metal", "INBOX/2026/2026-4995"])


@pytest.mark.asyncio
async def test_macos_mail_deletes_selected_project_mailbox() -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_runner(script: str, args: Sequence[str]) -> str:
        calls.append((script, list(args)))
        if script == DELETE_MAILBOX_SCRIPT:
            return "1"
        return "Balz Metal\tBalz Metal\tlionel@balzmetal.ch"

    client = MacNativeMailClient(
        target_store_id="Balz Metal",
        base_folder="inbox",
        script_runner=fake_runner,
    )

    deleted = await client.delete_folder_path(["2026", "2026-4995"])

    assert deleted is True
    assert calls[-1] == (DELETE_MAILBOX_SCRIPT, ["Balz Metal", "INBOX/2026/2026-4995"])


def test_local_outlook_uses_macos_mail_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("projectflow.outlook.local._platform_name", lambda: "darwin")
    config = OutlookConfig(enabled=True, mailbox_store_id="Balz Metal")

    client = create_local_outlook_client(config)

    assert isinstance(client, MacNativeMailClient)


def archived_store() -> tuple[FakeStore, FakeFolder]:
    store = FakeStore("store-1", "Boite Balz")
    archives = store.inbox.Folders.Add("00-Archives")
    year = archives.Folders.Add("2026")
    project = year.Folders.Add("2026-4952 (Protections platelage)")
    store.inbox.Folders.Add("2026")
    return store, project


def inbox_client(store: FakeStore) -> WindowsLocalOutlookClient:
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    return WindowsLocalOutlookClient(
        target_store_id="store-1",
        base_folder="inbox",
        app_factory=lambda: FakeApp(namespace),
    )


def test_windows_outlook_reuses_an_archived_project_folder() -> None:
    store, project = archived_store()

    folder = inbox_client(store).ensure_folder_path_sync(
        ["2026", "2026-4952 (Nouvelle designation)"],
    )

    assert folder is project
    # Neither a second folder in 2026 nor a rename of the archived folder.
    assert store.inbox.Folders.Item(2).Folders.Count == 0
    assert project.Name == "2026-4952 (Protections platelage)"


def test_windows_outlook_creates_children_inside_the_archived_project_folder() -> None:
    store, project = archived_store()

    folder = inbox_client(store).ensure_folder_path_sync(
        ["2026", "2026-4952 (Protections platelage)", "Fournisseurs"],
    )

    assert folder is project.Folders.Item(1)
    assert project.Folders.Item(1).Name == "Fournisseurs"


def test_windows_outlook_keeps_renaming_the_project_folder_at_its_place() -> None:
    store, archived = archived_store()
    active = store.inbox.Folders.Item(2).Folders.Add("2026-4952")

    folder = inbox_client(store).ensure_folder_path_sync(["2026", "2026-4952 (Platelage)"])

    assert folder is active
    assert active.Name == "2026-4952 (Platelage)"
    assert archived.Name == "2026-4952 (Protections platelage)"


def test_windows_outlook_ignores_deleted_items_when_searching_archives() -> None:
    store = FakeStore("store-1", "Boite Balz")
    deleted = store.root.Folders.Add("Elements supprimes")
    deleted.Folders.Add("2026-4952 (Ancien)")
    store.deleted = deleted
    namespace = FakeNamespace([store], [FakeAccount(store, "lionel@balzmetal.ch")])
    client = WindowsLocalOutlookClient(
        target_store_id="store-1",
        app_factory=lambda: FakeApp(namespace),
    )

    folder = client.ensure_folder_path_sync(["2026", "2026-4952"])

    assert isinstance(folder, FakeFolder)
    assert folder.Name == "2026-4952"
    assert folder is not deleted.Folders.Item(1)
