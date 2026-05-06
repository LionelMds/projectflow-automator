from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from typing import Any

from projectflow.exceptions import ConfigError, OutlookError
from projectflow.outlook.models import OutlookAccount

OutlookAppFactory = Callable[[], Any]
OL_FOLDER_INBOX = 6


class WindowsLocalOutlookClient:
    def __init__(
        self,
        *,
        target_store_id: str = "",
        target_mailbox: str = "",
        base_folder: str = "root",
        app_factory: OutlookAppFactory | None = None,
    ) -> None:
        self._target_store_id = target_store_id.strip()
        self._target_mailbox = target_mailbox.strip().casefold()
        self._base_folder = base_folder.strip().casefold() or "root"
        self._app_factory = app_factory or _dispatch_outlook

    async def list_accounts(self) -> list[OutlookAccount]:
        return await asyncio.to_thread(self.list_accounts_sync)

    def list_accounts_sync(self) -> list[OutlookAccount]:
        namespace = self._namespace()
        email_by_store_id = _account_email_by_store_id(namespace)
        accounts: list[OutlookAccount] = []
        stores = namespace.Stores
        for index in range(1, int(stores.Count) + 1):
            store = stores.Item(index)
            store_id = _clean_text(store.StoreID)
            display_name = _clean_text(store.DisplayName) or "Compte Outlook"
            accounts.append(
                OutlookAccount(
                    id=store_id,
                    display_name=display_name,
                    email=email_by_store_id.get(store_id, ""),
                    kind="store",
                ),
            )
        return accounts

    async def ensure_folder_path(self, names: list[str]) -> object:
        return await asyncio.to_thread(self._ensure_folder_path_sync, names)

    async def validate_target(self) -> None:
        await asyncio.to_thread(self.validate_target_sync)

    def validate_target_sync(self) -> None:
        self._base_target_folder()

    def _ensure_folder_path_sync(self, names: list[str]) -> object:
        if not names:
            raise ValueError("La liste de dossiers Outlook ne peut pas etre vide.")
        current = self._base_target_folder()
        for name in names:
            current = _ensure_child_folder(current, name)
        return current

    def _base_target_folder(self) -> Any:
        store = self._selected_store()
        if self._base_folder == "root":
            return store.GetRootFolder()
        if self._base_folder == "inbox":
            try:
                return store.GetDefaultFolder(OL_FOLDER_INBOX)
            except (_com_error_type(), AttributeError, RuntimeError, OSError) as exc:
                raise OutlookError(
                    "La boite de reception est introuvable pour ce compte Outlook.",
                ) from exc
        raise OutlookError(f"Emplacement Outlook non supporte: {self._base_folder}")

    def _selected_store(self) -> Any:
        namespace = self._namespace()
        stores = namespace.Stores
        if self._target_store_id:
            store = _find_store_by_id(stores, self._target_store_id)
            if store is None:
                raise OutlookError("Compte Outlook introuvable dans le profil local.")
            return store
        store = _find_store_by_text(
            stores,
            self._target_mailbox,
            _account_email_by_store_id(namespace),
        )
        if store is None:
            raise OutlookError("Compte Outlook introuvable dans le profil local.")
        return store

    def _namespace(self) -> Any:
        app = self._app_factory()
        return app.GetNamespace("MAPI")


def _dispatch_outlook() -> Any:
    try:
        win32_client = importlib.import_module("win32com.client")
    except ImportError as exc:
        raise ConfigError(
            "pywin32 est requis pour piloter Outlook localement sur Windows.",
        ) from exc

    try:
        return win32_client.Dispatch("Outlook.Application")
    except (_com_error_type(), AttributeError, RuntimeError, OSError) as exc:
        raise OutlookError(
            "Outlook Windows classique est introuvable ou refuse l'automation locale.",
        ) from exc


def _com_error_type() -> type[BaseException]:
    try:
        pywintypes = importlib.import_module("pywintypes")
    except ImportError:
        return OSError
    error_type = getattr(pywintypes, "com_error", OSError)
    if isinstance(error_type, type) and issubclass(error_type, BaseException):
        return error_type
    return OSError


def _account_email_by_store_id(namespace: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    accounts = namespace.Accounts
    for index in range(1, int(accounts.Count) + 1):
        account = accounts.Item(index)
        store_id = _clean_text(account.DeliveryStore.StoreID)
        email = _clean_text(account.SmtpAddress)
        if store_id and email:
            result[store_id] = email
    return result


def _find_store_by_id(stores: Any, store_id: str) -> Any | None:
    for index in range(1, int(stores.Count) + 1):
        store = stores.Item(index)
        if _clean_text(store.StoreID) == store_id:
            return store
    return None


def _find_store_by_text(stores: Any, value: str, email_by_store_id: dict[str, str]) -> Any | None:
    wanted = value.casefold()
    for index in range(1, int(stores.Count) + 1):
        store = stores.Item(index)
        display_name = _clean_text(store.DisplayName)
        email = email_by_store_id.get(_clean_text(store.StoreID), "")
        candidates = [
            _clean_text(store.StoreID),
            display_name,
            email,
            OutlookAccount(id="", display_name=display_name, email=email).label,
        ]
        if wanted in {candidate.casefold() for candidate in candidates if candidate}:
            return store
    return None


def _ensure_child_folder(parent: Any, name: str) -> Any:
    folders = parent.Folders
    existing = _find_folder(folders, name)
    if existing is not None:
        return existing
    return folders.Add(name)


def _find_folder(folders: Any, name: str) -> Any | None:
    wanted = name.casefold()
    for index in range(1, int(folders.Count) + 1):
        folder = folders.Item(index)
        if _clean_text(folder.Name).casefold() == wanted:
            return folder
    return None


def _clean_text(value: object) -> str:
    return str(value or "").strip()
