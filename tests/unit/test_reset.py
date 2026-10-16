from __future__ import annotations

from pathlib import Path

import pytest

from projectflow import __main__ as entry
from projectflow.reset import RESET_ARGUMENT, reset_local_data


class FakeStorage:
    def __init__(self, *, error: OSError | None = None) -> None:
        self.cleared = 0
        self._error = error

    def clear(self) -> None:
        self.cleared += 1
        if self._error is not None:
            raise self._error


def _installed_data(root: Path) -> tuple[Path, Path]:
    logs = root / "Logs"
    logs.mkdir(parents=True)
    (logs / "projectflow.jsonl").write_text("{}", encoding="utf-8")
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "msal-cache.json").write_text("{}", encoding="utf-8")
    (root / "updates" / "0.1.62").mkdir(parents=True)
    (root / "updates" / "0.1.62" / "Setup.exe").write_bytes(b"x")
    (root / "demo").mkdir()
    return logs, root / "config.json"


def test_reset_erases_settings_connection_and_downloads_but_keeps_the_log(
    tmp_path: Path,
) -> None:
    logs, config = _installed_data(tmp_path)
    storages = [FakeStorage(), FakeStorage()]

    failures = reset_local_data(
        storages=storages,
        data_root=tmp_path,
        config_path=config,
        keep=logs,
    )

    assert failures == []
    assert [storage.cleared for storage in storages] == [1, 1]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Logs"]
    assert (logs / "projectflow.jsonl").exists()


def test_reset_reports_what_it_could_not_erase_and_goes_on(tmp_path: Path) -> None:
    logs, config = _installed_data(tmp_path)

    failures = reset_local_data(
        storages=[FakeStorage(error=PermissionError("acces refuse")), FakeStorage()],
        data_root=tmp_path,
        config_path=config,
        keep=logs,
    )

    assert len(failures) == 1
    assert "acces refuse" in failures[0]
    assert not config.exists()


def test_reset_handles_a_config_outside_the_data_folder(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    config = tmp_path / "config" / "config.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")

    reset_local_data(
        storages=[],
        data_root=data_root,
        config_path=config,
        keep=tmp_path / "logs",
    )

    assert not config.exists()


@pytest.mark.parametrize(("failures", "code"), [([], 0), (["config.json: verrouille"], 1)])
def test_installer_reset_argument_erases_without_opening_a_window(
    monkeypatch: pytest.MonkeyPatch,
    failures: list[str],
    code: int,
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(entry.sys, "argv", ["ProjectFlowAutomator.exe", RESET_ARGUMENT])
    monkeypatch.setattr(entry, "configure_logging", lambda: None)
    monkeypatch.setattr(entry, "reset_local_data", lambda: calls.append(True) or failures)

    assert entry.main() == code
    assert calls == [True]
