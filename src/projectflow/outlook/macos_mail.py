from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Sequence

from projectflow.exceptions import ConfigError, OutlookError
from projectflow.outlook.models import OutlookAccount

ScriptRunner = Callable[[str, Sequence[str]], str]
ON_MY_MAC_ID = "on-my-mac"
MIN_ACCOUNT_COLUMNS = 2
EMAIL_COLUMN_INDEX = 2

LIST_ACCOUNTS_SCRIPT = """
on run
    set rows to {}
    tell application "Mail"
        repeat with mailAccount in accounts
            set accountName to name of mailAccount as text
            set emailText to ""
            try
                set emailList to email addresses of mailAccount
                if (count of emailList) > 0 then
                    set emailText to item 1 of emailList as text
                end if
            end try
            set end of rows to accountName & tab & accountName & tab & emailText
        end repeat
    end tell
    set end of rows to "on-my-mac" & tab & "Sur mon Mac" & tab & ""
    return my joinRows(rows)
end run

on joinRows(rows)
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to linefeed
    set output to rows as text
    set AppleScript's text item delimiters to oldDelimiters
    return output
end joinRows
"""

ENSURE_MAILBOX_SCRIPT = """
on run argv
    set accountName to item 1 of argv
    set mailboxPath to item 2 of argv
    tell application "Mail"
        if accountName is "" then
            if not (exists mailbox mailboxPath) then
                make new mailbox with properties {name:mailboxPath}
            end if
        else
            tell account accountName
                if not (exists mailbox mailboxPath) then
                    make new mailbox with properties {name:mailboxPath}
                end if
            end tell
        end if
    end tell
end run
"""

DELETE_MAILBOX_SCRIPT = """
on run argv
    set accountName to item 1 of argv
    set mailboxPath to item 2 of argv
    tell application "Mail"
        if accountName is "" then
            if exists mailbox mailboxPath then
                delete mailbox mailboxPath
                return "1"
            end if
        else
            tell account accountName
                if exists mailbox mailboxPath then
                    delete mailbox mailboxPath
                    return "1"
                end if
            end tell
        end if
    end tell
    return "0"
end run
"""


class MacNativeMailClient:
    def __init__(
        self,
        *,
        target_store_id: str = "",
        target_mailbox: str = "",
        base_folder: str = "root",
        script_runner: ScriptRunner | None = None,
    ) -> None:
        self._target_store_id = target_store_id.strip()
        self._target_mailbox = target_mailbox.strip().casefold()
        self._base_folder = base_folder.strip().casefold() or "root"
        self._script_runner = script_runner or _run_osascript

    async def list_accounts(self) -> list[OutlookAccount]:
        return await asyncio.to_thread(self.list_accounts_sync)

    def list_accounts_sync(self) -> list[OutlookAccount]:
        output = self._script_runner(LIST_ACCOUNTS_SCRIPT, [])
        return _parse_accounts(output)

    async def ensure_folder_path(self, names: list[str]) -> object:
        await asyncio.to_thread(self._ensure_folder_path_sync, names)
        return object()

    async def delete_folder_path(self, names: list[str]) -> bool:
        return await asyncio.to_thread(self._delete_folder_path_sync, names)

    async def validate_target(self) -> None:
        await asyncio.to_thread(self.validate_target_sync)

    def validate_target_sync(self) -> None:
        self._selected_account()

    def _ensure_folder_path_sync(self, names: list[str]) -> None:
        if not names:
            raise ValueError("La liste de dossiers Mail ne peut pas etre vide.")
        account = self._selected_account()
        mailbox_path = "/".join(_mailbox_segment(name) for name in self._path_segments(names))
        account_name = "" if account.id == ON_MY_MAC_ID else account.id
        self._script_runner(ENSURE_MAILBOX_SCRIPT, [account_name, mailbox_path])

    def _delete_folder_path_sync(self, names: list[str]) -> bool:
        if not names:
            raise ValueError("La liste de dossiers Mail ne peut pas etre vide.")
        account = self._selected_account()
        mailbox_path = "/".join(_mailbox_segment(name) for name in self._path_segments(names))
        account_name = "" if account.id == ON_MY_MAC_ID else account.id
        result = self._script_runner(DELETE_MAILBOX_SCRIPT, [account_name, mailbox_path])
        return result.strip() == "1"

    def _path_segments(self, names: list[str]) -> list[str]:
        if self._base_folder == "root":
            return names
        if self._base_folder == "inbox":
            return ["INBOX", *names]
        raise OutlookError(f"Emplacement Mail non supporte: {self._base_folder}")

    def _selected_account(self) -> OutlookAccount:
        accounts = self.list_accounts_sync()
        if self._target_store_id:
            account = _find_account(accounts, self._target_store_id)
        else:
            account = _find_account(accounts, self._target_mailbox)
        if account is None:
            raise OutlookError("Compte Mail introuvable dans l'application Mail.")
        return account


def _run_osascript(script: str, args: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["osascript", "-e", script, *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ConfigError("osascript est requis pour piloter Mail sur macOS.") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or "Mail macOS a refuse l'automation locale."
        raise OutlookError(message)
    return result.stdout.strip()


def _parse_accounts(output: str) -> list[OutlookAccount]:
    accounts: list[OutlookAccount] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < MIN_ACCOUNT_COLUMNS:
            continue
        account_id = parts[0]
        display_name = parts[1]
        email = parts[EMAIL_COLUMN_INDEX] if len(parts) > EMAIL_COLUMN_INDEX else ""
        if account_id and display_name:
            accounts.append(
                OutlookAccount(
                    id=account_id,
                    display_name=display_name,
                    email=email,
                    kind="mail",
                ),
            )
    if not any(account.id == ON_MY_MAC_ID for account in accounts):
        accounts.append(OutlookAccount(id=ON_MY_MAC_ID, display_name="Sur mon Mac"))
    return accounts


def _find_account(accounts: list[OutlookAccount], value: str) -> OutlookAccount | None:
    wanted = value.casefold()
    for account in accounts:
        candidates = [
            account.id,
            account.display_name,
            account.email,
            account.label,
        ]
        if wanted in {candidate.casefold() for candidate in candidates if candidate}:
            return account
    return None


def _mailbox_segment(value: str) -> str:
    return " ".join(value.replace("/", "-").split()) or "Projet"
