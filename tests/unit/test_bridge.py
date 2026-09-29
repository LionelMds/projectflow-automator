from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from projectflow import __main__ as entry_point
from projectflow import bridge
from projectflow.bridge import (
    ENSURE_OUTLOOK_FOLDERS,
    MAX_NUMBERS,
    PROTOCOL_VERSION,
    REQUEST_ARGUMENT,
    RESULT_ARGUMENT,
    BridgeDependencies,
    BridgeRequestError,
    handle_request,
    is_bridge_request,
    requested_numbers,
    run_bridge,
)
from projectflow.config import AppConfig, OutlookFolderConfig
from projectflow.core.repertoire_service import RepertoireRow, RepertoireSnapshot
from projectflow.exceptions import AuthError, OutlookError, ProjectCreationError


def request(*numbers: str, **changes: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_VERSION,
        "action": ENSURE_OUTLOOK_FOLDERS,
        "numbers": list(numbers),
        **changes,
    }


def row(index: int, *values: Any) -> RepertoireRow:
    return RepertoireRow(row_index=index, values=tuple(values))


class FakeRepertoire:
    def __init__(self, rows_by_year: dict[int, list[RepertoireRow]]) -> None:
        self.rows_by_year = rows_by_year
        self.years: list[int] = []

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        self.years.append(year)
        if year not in self.rows_by_year:
            raise ProjectCreationError(f"Onglet introuvable dans le repertoire: {year}")
        return RepertoireSnapshot(
            year=year,
            rows=tuple(self.rows_by_year[year]),
            next_available=None,
        )


class FakeOutlook:
    def __init__(self, *, fail_on: str = "", invalid_target: bool = False) -> None:
        self.paths: list[list[str]] = []
        self.fail_on = fail_on
        self.invalid_target = invalid_target
        self.validated = 0

    def validate_target_sync(self) -> None:
        self.validated += 1
        if self.invalid_target:
            raise OutlookError("Compte Outlook introuvable dans le profil local.")

    def ensure_folder_path_sync(self, names: list[str]) -> object:
        if self.fail_on and any(self.fail_on in name for name in names):
            raise OutlookError(f"Impossible de creer le dossier Outlook: {names[-1]}")
        self.paths.append(list(names))
        return names


def outlook_config(*, enabled: bool = True) -> AppConfig:
    config = AppConfig()
    config.outlook.enabled = enabled
    config.outlook.mailbox_email = "lionel@balzmetal.ch"
    config.outlook.base_folder = "inbox"
    config.outlook.arborescence = [
        OutlookFolderConfig(
            name="[YYYY]",
            children=[OutlookFolderConfig(name="[YYYY]-[XXXX]")],
        ),
    ]
    return config


def dependencies(
    repertoire: FakeRepertoire,
    outlook: FakeOutlook,
    config: AppConfig | None = None,
    closed: list[bool] | None = None,
) -> BridgeDependencies:
    closed_calls = closed if closed is not None else []

    async def close() -> None:
        closed_calls.append(True)

    return BridgeDependencies(
        load_config=lambda: config or outlook_config(),
        repertoire=lambda _config: (repertoire, close),
        outlook=lambda _config: outlook,
    )


def repertoire_2026() -> FakeRepertoire:
    return FakeRepertoire(
        {
            2026: [
                row(0, "2026-0150", date(2026, 3, 2), "Morges SA", "M. Blanc", "Hangar  Morges"),
                row(1, "2026-0150-1", date(2026, 4, 1), "Morges SA", "", "Avant-toit"),
                row(2, "2026-0151", "", "", "", ""),
                row(3, "2026-0152", date(2026, 3, 9), "Garage Nyon", "", ""),
            ],
        }
    )


def test_known_projects_get_their_outlook_folder_named_by_projectflow() -> None:
    outlook = FakeOutlook()
    closed: list[bool] = []

    result = handle_request(
        request("2026-0150", "2026-0152"),
        dependencies(repertoire_2026(), outlook, closed=closed),
    )

    assert outlook.paths == [
        ["2026", "2026-0150 (Hangar Morges)"],
        ["2026", "2026-0152"],
    ]
    assert result["ok"]
    assert result["outlook"] == {"mailbox": "lionel@balzmetal.ch", "base_folder": "inbox"}
    first, second = result["projects"]
    assert first == {
        "number": "2026-0150",
        "status": "ok",
        "designation": "Hangar  Morges",
        "societe": "Morges SA",
        "folder_paths": [["2026", "2026-0150 (Hangar Morges)"]],
        "message": "",
    }
    assert second["status"] == "ok"
    assert closed == [True]
    assert outlook.validated == 1


def test_unknown_or_unused_numbers_never_create_a_folder() -> None:
    outlook = FakeOutlook()
    repertoire = repertoire_2026()

    result = handle_request(
        request("2026-0151", "2026-0999", "2027-0001"),
        dependencies(repertoire, outlook),
    )

    assert outlook.paths == []
    statuses = {project["number"]: project for project in result["projects"]}
    assert statuses["2026-0151"]["status"] == "unknown"
    assert "absent du repertoire" in statuses["2026-0151"]["message"]
    assert statuses["2026-0999"]["status"] == "unknown"
    assert statuses["2027-0001"]["message"] == "Onglet 2027 absent du repertoire chantier."
    assert repertoire.years == [2026, 2027]


def test_outlook_failure_for_one_project_does_not_stop_the_others() -> None:
    outlook = FakeOutlook(fail_on="0150")

    result = handle_request(
        request("2026-0150", "2026-0152"),
        dependencies(repertoire_2026(), outlook),
    )

    assert [project["status"] for project in result["projects"]] == ["error", "ok"]
    assert "Impossible de creer" in result["projects"][0]["message"]
    assert outlook.paths == [["2026", "2026-0152"]]


def test_disabled_or_unavailable_outlook_is_reported() -> None:
    with pytest.raises(BridgeRequestError, match="desactivee"):
        handle_request(
            request("2026-0150"),
            dependencies(repertoire_2026(), FakeOutlook(), outlook_config(enabled=False)),
        )
    with pytest.raises(OutlookError, match="introuvable"):
        handle_request(
            request("2026-0150"),
            dependencies(repertoire_2026(), FakeOutlook(invalid_target=True)),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (request("2026-0150", protocol=2), "format inconnu"),
        (request("2026-0150", action="delete_project"), "Action MailFlow inconnue"),
        (request(), "Aucun numero"),
        (request("2026-0150-1"), "Numero de projet invalide"),
        (request("../2026"), "Numero de projet invalide"),
        ({**request(), "numbers": [2026]}, "Numero de projet invalide"),
        (request(*[f"2026-{index:04d}" for index in range(MAX_NUMBERS + 1)]), "Trop de projets"),
    ],
)
def test_invalid_requests_are_refused(payload: dict[str, Any], message: str) -> None:
    with pytest.raises(BridgeRequestError, match=message):
        requested_numbers(payload)


def test_numbers_are_deduplicated_in_order() -> None:
    assert requested_numbers(request("2026-0152", "2026-0150", "2026-0152")) == [
        "2026-0152",
        "2026-0150",
    ]


def write_request(tmp_path: Path, payload: object) -> list[str]:
    request_path = tmp_path / "demande.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    return [
        "ProjectFlowAutomator.exe",
        REQUEST_ARGUMENT,
        str(request_path),
        RESULT_ARGUMENT,
        str(tmp_path / "resultat.json"),
    ]


def read_result(tmp_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads((tmp_path / "resultat.json").read_text(encoding="utf-8"))
    return result


def test_run_bridge_writes_the_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge, "configure_logging", lambda: None)
    argv = write_request(tmp_path, request("2026-0150"))

    code = run_bridge(argv, dependencies(repertoire_2026(), FakeOutlook()))

    assert code == 0
    result = read_result(tmp_path)
    assert result["ok"]
    assert result["projects"][0]["status"] == "ok"
    assert list(tmp_path.glob(".resultat.json.*")) == []


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        ("not an object", None, "Demande MailFlow illisible."),
        (
            request("2026-0150"),
            AuthError("Connexion Microsoft a renouveler."),
            "Connexion Microsoft a renouveler.",
        ),
        (
            request("2026-0150"),
            KeyError("secret"),
            "Erreur inattendue dans ProjectFlow: consultez son journal.",
        ),
    ],
)
def test_run_bridge_reports_failures_without_raising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error: Exception | None,
    message: str,
) -> None:
    monkeypatch.setattr(bridge, "configure_logging", lambda: None)
    argv = write_request(tmp_path, payload)
    deps = dependencies(repertoire_2026(), FakeOutlook())
    if error is not None:

        def fail() -> AppConfig:
            raise error

        deps = BridgeDependencies(
            load_config=fail,
            repertoire=deps.repertoire,
            outlook=deps.outlook,
        )

    code = run_bridge(argv, deps)

    assert code == 1
    result = read_result(tmp_path)
    assert result == {
        "protocol": PROTOCOL_VERSION,
        "ok": False,
        "error": message,
        "projectflow_version": result["projectflow_version"],
        "projects": [],
    }


def test_run_bridge_without_result_path_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "configure_logging", lambda: None)

    assert run_bridge(["ProjectFlowAutomator.exe", REQUEST_ARGUMENT, "demande.json"]) == 2


def test_entry_point_routes_mailflow_requests_without_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = ["ProjectFlowAutomator.exe", REQUEST_ARGUMENT, "a.json", RESULT_ARGUMENT, "b.json"]
    received: list[list[str]] = []
    monkeypatch.setattr(entry_point.sys, "argv", argv)
    monkeypatch.setattr(entry_point, "run_bridge", lambda value: received.append(value) or 0)

    assert entry_point.main() == 0
    assert received == [argv]
    assert is_bridge_request(argv)
    assert not is_bridge_request(["ProjectFlowAutomator.exe", "--demo"])
