from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any

import pytest

from projectflow.platform import filemanager


def test_windows_excel_launcher_preserves_office_delimiters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = "ms-excel:ofe|u|https://example.sharepoint.com/Documents/rep%20projets.xlsx"
    opened: list[str] = []
    monkeypatch.setattr(filemanager.platform, "system", lambda: "Windows")
    monkeypatch.setattr(filemanager.os, "startfile", opened.append, raising=False)
    assert filemanager.open_excel_uri(uri)
    assert opened == [uri]


@pytest.mark.parametrize("return_code", [0, 1])
def test_macos_excel_launcher_preserves_uri_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    return_code: int,
) -> None:
    uri = "ms-excel:ofe|u|https://example.sharepoint.com/Documents/rep%20projets.xlsx"
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> CompletedProcess[bytes]:
        commands.append(command)
        return CompletedProcess(command, return_code)

    monkeypatch.setattr(filemanager.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(filemanager.subprocess, "run", run)
    assert filemanager.open_excel_uri(uri) is (return_code == 0)
    assert commands == [["open", uri]]
