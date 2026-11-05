from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from projectflow.config import AppConfig
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.graph.planner import PlannerBucket, PlannerMember, PlannerPlan
from projectflow.outlook.models import OutlookAccount
from projectflow.platform.paths import native_path_text
from projectflow.ui.dialogs.settings import SettingsDialog


def test_settings_dialog_applies_values(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.user_initials_edit.setText(" ab ")
    dialog.racine_edit.setText(str(tmp_path / "clients"))
    dialog.reference_edit.setText(str(tmp_path / "reference"))
    dialog.repertoire_path_edit.setText("Entreprise/Rep.xlsx")
    dialog.repertoire_open_path_edit.setText(str(tmp_path / "Rep.xlsx"))
    dialog.outlook_enabled_checkbox.setChecked(True)
    dialog.outlook_account_combo.setEditText("projets@balzmetal.ch")
    dialog.outlook_base_folder_combo.setCurrentIndex(
        dialog.outlook_base_folder_combo.findData("inbox"),
    )
    dialog.planner_enabled_checkbox.setChecked(True)
    dialog.planner_plan_combo.addItem("Plan projets", "plan-id")
    dialog.planner_plan_combo.setCurrentIndex(0)
    dialog.planner_bucket_combo.addItem("A faire", "bucket-id")
    dialog.planner_bucket_combo.setCurrentIndex(0)
    dialog.planner_due_days_spin.setValue(5)

    dialog.apply_to_config(config)

    assert config.user.initials == "AB"
    assert config.paths.racine_projets == tmp_path / "clients"
    assert config.paths.dossier_reference == tmp_path / "reference"
    assert config.paths.repertoire_chantier.display_path == native_path_text("Entreprise/Rep.xlsx")
    assert config.paths.repertoire_chantier.drive_id == ""
    assert config.paths.repertoire_chantier.item_id == ""
    assert config.paths.repertoire_chantier.open_path == str(tmp_path / "Rep.xlsx")
    assert config.outlook.enabled is True
    assert config.outlook.mailbox_email == "projets@balzmetal.ch"
    assert config.outlook.mailbox_store_id == ""
    assert config.outlook.base_folder == "inbox"
    assert config.planner.enabled is True
    assert config.planner.plan_id == "plan-id"
    assert config.planner.plan_name == "Plan projets"
    assert config.planner.bucket_id == "bucket-id"
    assert config.planner.bucket_name == "A faire"
    assert config.planner.due_days == 5


def test_settings_dialog_detects_outlook_accounts(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        "projectflow.ui.dialogs.settings.detect_local_outlook_accounts",
        lambda: [
            OutlookAccount(
                id="store-1",
                display_name="Boite Balz",
                email="lionel@balzmetal.ch",
            ),
        ],
    )

    dialog.outlook_refresh_button.click()
    dialog.outlook_enabled_checkbox.setChecked(True)
    dialog.apply_to_config(config)

    assert dialog.outlook_account_combo.currentText() == "Boite Balz (lionel@balzmetal.ch)"
    assert config.outlook.mailbox_email == "Boite Balz (lionel@balzmetal.ch)"
    assert config.outlook.mailbox_store_id == "store-1"


def test_settings_dialog_tests_selected_outlook_account(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    validated: list[tuple[str, str]] = []
    selected_base_folders: list[str] = []

    def fake_information(*_args: object) -> None:
        return None

    monkeypatch.setattr(
        "projectflow.ui.dialogs.settings.validate_local_outlook_account",
        lambda *, store_id, mailbox, base_folder: (
            validated.append((store_id, mailbox)),
            selected_base_folders.append(base_folder),
        ),
    )
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", fake_information)
    dialog.outlook_account_combo.addItem("Boite Balz", "store-1")
    dialog.outlook_account_combo.setCurrentIndex(0)
    dialog.outlook_base_folder_combo.setCurrentIndex(
        dialog.outlook_base_folder_combo.findData("inbox"),
    )

    dialog.outlook_test_button.click()

    assert validated == [("store-1", "Boite Balz")]
    assert selected_base_folders == ["inbox"]
    assert dialog.outlook_enabled_checkbox.isChecked() is True


def test_settings_dialog_refuses_enabled_outlook_without_account(
    qtbot,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.outlook_enabled_checkbox.setChecked(True)

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings == ["Selectionnez un compte Outlook ou desactivez la creation Outlook."]


def test_settings_dialog_refuses_enabled_planner_without_bucket(
    qtbot,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.planner_enabled_checkbox.setChecked(True)
    dialog.planner_plan_combo.addItem("Plan projets", "plan-id")
    dialog.planner_plan_combo.setCurrentIndex(0)

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings == ["Selectionnez une colonne Planner ou desactivez la creation Planner."]


def test_repertoire_reconnection_forgets_stale_ids_even_with_same_path(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    repertoire = config.paths.repertoire_chantier
    repertoire.display_path = native_path_text("Entreprise/Rep.xlsx")
    repertoire.drive_id = "old-drive"
    repertoire.item_id = "old-item"
    repertoire.open_path = native_path_text("OneDrive/Rep.xlsx")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.repertoire_reconnect_button.click()
    assert repertoire.item_id == "old-item"  # No mutation before accepting the dialog.
    dialog.apply_to_config(config)

    current = config.paths.repertoire_chantier
    assert current is not repertoire
    assert current.display_path == native_path_text("Entreprise/Rep.xlsx")
    assert current.drive_id == ""
    assert current.item_id == ""
    assert current.open_path == repertoire.open_path
    assert current.cloud_only
    assert repertoire.drive_id == "old-drive"
    assert repertoire.item_id == "old-item"


def test_settings_preserve_cloud_url_and_explicit_cloud_mode(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    url = "https://balz.sharepoint.com/:x:/s/site/abc"
    dialog.repertoire_path_edit.setText(url)
    dialog.repertoire_cloud_checkbox.setChecked(True)
    dialog.apply_to_config(config)
    assert config.paths.repertoire_chantier.display_path == url
    assert config.paths.repertoire_chantier.cloud_only


def test_unchanged_repertoire_keeps_ids_in_detached_config(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    previous = config.paths.repertoire_chantier
    previous.display_path = "https://balz.sharepoint.com/:x:/s/site/original"
    previous.drive_id = "drive"
    previous.item_id = "item"
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog.apply_to_config(config)

    current = config.paths.repertoire_chantier
    assert current is not previous
    assert current.drive_id == "drive"
    assert current.item_id == "item"


@pytest.mark.asyncio
@pytest.mark.parametrize("change_path", [False, True])
async def test_delayed_resolution_cannot_restore_ids_after_settings_change(
    qtbot,
    *,
    change_path: bool,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    config = AppConfig()
    previous = config.paths.repertoire_chantier
    previous.display_path = "https://balz.sharepoint.com/:x:/s/site/original"

    class TokenProvider:
        async def access_token(self) -> str:
            return "token"

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/shares/" in request.url.path:
            entered.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "id": "old-item",
                    "name": "repertoire.xlsx",
                    "parentReference": {"driveId": "old-drive"},
                },
            )
        if request.url.path.endswith("/createSession"):
            return httpx.Response(200, json={"id": "session"})
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        gateway = GraphExcelWorkbookGateway(
            graph=GraphClient(token_provider=TokenProvider(), http_client=http_client),
            config=previous,
        )

        async def resolve_old_target() -> None:
            async with gateway.session():
                pass

        task = asyncio.create_task(resolve_old_target())
        await entered.wait()
        dialog = SettingsDialog(config)
        qtbot.addWidget(dialog)
        if change_path:
            dialog.repertoire_path_edit.setText("https://balz.sharepoint.com/:x:/s/site/new")
        else:
            dialog.repertoire_reconnect_button.click()
        dialog.apply_to_config(config)
        release.set()
        await task

    current = config.paths.repertoire_chantier
    assert current is not previous
    assert current.drive_id == ""
    assert current.item_id == ""
    assert previous.drive_id == "old-drive"
    assert previous.item_id == "old-item"
    assert current.display_path == (
        "https://balz.sharepoint.com/:x:/s/site/new" if change_path else previous.display_path
    )


def _configured_settings() -> AppConfig:
    config = AppConfig()
    config.user.initials = "AB"
    config.paths.repertoire_chantier.display_path = "https://example.sharepoint.com/original"
    config.paths.repertoire_chantier.open_path = native_path_text("OneDrive/Repertoire.xlsx")
    config.outlook.enabled = True
    config.outlook.mailbox_email = "Boite projet (projets@example.test)"
    config.outlook.mailbox_store_id = "saved-store"
    config.outlook.base_folder = "inbox"
    config.planner.enabled = True
    config.planner.plan_id = "saved-plan"
    config.planner.plan_name = "Plan projets"
    config.planner.bucket_id = "saved-bucket"
    config.planner.bucket_name = "En cours"
    return config


def test_settings_survive_save_reload_and_dialog_reopening(qtbot, tmp_path: Path) -> None:
    config = _configured_settings()
    expected = config.model_dump()
    path = tmp_path / "config.json"
    config.save(path)

    for _ in range(2):
        config = AppConfig.load(path)
        dialog = SettingsDialog(config)
        qtbot.addWidget(dialog)
        assert dialog.user_initials_edit.text() == "AB"
        assert (
            dialog.repertoire_open_path_edit.text()
            == expected["paths"]["repertoire_chantier"]["open_path"]
        )
        dialog.apply_to_config(config)
        config.save(path)
        assert AppConfig.load(path).model_dump() == expected


@pytest.mark.parametrize("result", ["present", "missing", "empty", "error"])
def test_outlook_detection_preserves_saved_account(qtbot, monkeypatch, result: str) -> None:
    config = _configured_settings()
    expected = config.outlook.model_dump()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    def detect() -> list[OutlookAccount]:
        if result == "error":
            raise ConfigError("Detection indisponible")
        accounts = [OutlookAccount(id="other-store", display_name="Autre", email="")]
        if result == "present":
            accounts.append(
                OutlookAccount(
                    id="saved-store",
                    display_name="Boite projet",
                    email="projets@example.test",
                ),
            )
        return [] if result == "empty" else accounts

    monkeypatch.setattr("projectflow.ui.dialogs.settings.detect_local_outlook_accounts", detect)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *_args: None)

    dialog.outlook_refresh_button.click()
    dialog.apply_to_config(config)

    assert config.outlook.model_dump() == expected


def test_outlook_detection_preserves_manually_entered_mailbox(qtbot, monkeypatch) -> None:
    config = _configured_settings()
    config.outlook.mailbox_email = "manual@example.test"
    config.outlook.mailbox_store_id = ""
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        "projectflow.ui.dialogs.settings.detect_local_outlook_accounts",
        lambda: [OutlookAccount(id="other-store", display_name="Autre", email="")],
    )

    dialog.outlook_refresh_button.click()
    dialog.apply_to_config(config)

    assert config.outlook.mailbox_email == "manual@example.test"
    assert config.outlook.mailbox_store_id == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["present", "missing", "empty", "plan-error", "bucket-error"])
async def test_planner_detection_preserves_plan_and_bucket(qtbot, monkeypatch, result: str) -> None:
    config = _configured_settings()
    expected = config.planner.model_dump()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    class PlannerClient:
        async def aclose(self) -> None:
            pass

        async def list_plans(self) -> list[PlannerPlan]:
            if result == "plan-error":
                raise ConfigError("Plans indisponibles")
            plans = [PlannerPlan(id="other-plan", title="Autre plan")]
            if result in {"present", "bucket-error"}:
                plans.append(PlannerPlan(id="saved-plan", title="Plan projets"))
            return [] if result == "empty" else plans

        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            assert plan_id == "saved-plan"
            if result == "bucket-error":
                raise ConfigError("Colonnes indisponibles")
            buckets = [PlannerBucket(id="other-bucket", name="Autre", plan_id=plan_id)]
            if result == "present":
                buckets.append(PlannerBucket(id="saved-bucket", name="En cours", plan_id=plan_id))
            return [] if result == "empty" else buckets

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", lambda *_args: None)

    await dialog._load_planner_plans()  # noqa: SLF001
    dialog.apply_to_config(config)

    assert config.planner.model_dump() == expected


@pytest.mark.asyncio
async def test_delayed_buckets_do_not_replace_selection_for_new_plan(qtbot, monkeypatch) -> None:
    config = _configured_settings()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    entered = asyncio.Event()
    release = asyncio.Event()

    class PlannerClient:
        async def aclose(self) -> None:
            pass

        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            entered.set()
            await release.wait()
            return [PlannerBucket(id="saved-bucket", name="En cours", plan_id=plan_id)]

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    pending = asyncio.create_task(dialog._load_planner_buckets())  # noqa: SLF001
    await entered.wait()
    dialog.planner_plan_combo.addItem("Nouveau plan", "new-plan")
    dialog.planner_plan_combo.setCurrentIndex(1)
    assert dialog.planner_bucket_combo.count() == 0
    dialog.planner_bucket_combo.addItem("Nouvelle colonne", "new-bucket")
    dialog.planner_bucket_combo.setCurrentIndex(0)
    release.set()
    await pending
    dialog.apply_to_config(config)

    assert config.planner.plan_id == "new-plan"
    assert config.planner.bucket_id == "new-bucket"


def test_editing_plan_id_clears_previous_plans_bucket(qtbot) -> None:
    dialog = SettingsDialog(_configured_settings())
    qtbot.addWidget(dialog)

    dialog.planner_plan_combo.setEditText("new-plan")

    assert dialog.planner_bucket_combo.count() == 0


def test_manual_planner_ids_are_saved_without_detection(qtbot) -> None:
    config = AppConfig()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog.planner_plan_combo.setEditText("manual-plan")
    dialog.planner_bucket_combo.setEditText("manual-bucket")

    dialog.apply_to_config(config)

    assert config.planner.plan_id == "manual-plan"
    assert config.planner.bucket_id == "manual-bucket"


def test_excel_open_path_rejects_web_link(qtbot, monkeypatch) -> None:
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.repertoire_open_path_edit.setText("https://example.sharepoint.com/workbook")

    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert len(warnings) == 1
    assert "fichier synchronise" in warnings[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["plans", "buckets", "test"])
@pytest.mark.parametrize("outcome", ["success", "error"])
async def test_planner_buttons_prevent_duplicate_requests_and_restore_after_completion(
    qtbot,
    monkeypatch,
    action: str,
    outcome: str,
) -> None:
    config = _configured_settings()
    expected = config.planner.model_dump()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    entered = asyncio.Event()
    release = asyncio.Event()
    requests: list[str] = []
    closed: list[object] = []
    clients: list[object] = []
    warnings: list[str] = []

    class PlannerClient:
        def __init__(self) -> None:
            clients.append(self)

        async def aclose(self) -> None:
            closed.append(self)

        async def request(self, operation: str) -> None:
            requests.append(operation)
            entered.set()
            await release.wait()
            if outcome == "error":
                raise ConfigError("Erreur https://1drv.ms/x/private-sharing-key?e=secret")

        async def list_plans(self) -> list[PlannerPlan]:
            await self.request("plans")
            return [PlannerPlan(id="saved-plan", title="Plan projets")]

        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            await self.request("buckets")
            return [PlannerBucket(id="saved-bucket", name="En cours", plan_id=plan_id)]

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_args: None)
    buttons = {
        "plans": dialog.planner_refresh_button,
        "buckets": dialog.planner_bucket_refresh_button,
        "test": dialog.planner_test_button,
    }
    labels = {name: button.text() for name, button in buttons.items()}

    buttons[action].click()
    task = dialog._planner_task  # noqa: SLF001
    assert task is not None
    for button in buttons.values():
        assert not button.isEnabled()
        button.click()
    assert buttons[action].text() == "Chargement..."
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert len(clients) == 1
    assert len(requests) == 1
    release.set()
    await task

    assert closed == clients
    assert dialog._planner_task is None  # noqa: SLF001
    for name, button in buttons.items():
        assert button.isEnabled()
        assert button.text() == labels[name]
    assert requests == (
        ["plans", "buckets"]
        if action == "plans" and outcome == "success"
        else ["plans" if action == "plans" else "buckets"]
    )
    assert len(warnings) == (1 if outcome == "error" else 0)
    assert all(
        "private-sharing-key" not in message and "secret" not in message for message in warnings
    )
    dialog.apply_to_config(config)
    assert config.planner.model_dump() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["request", "close"])
async def test_unexpected_planner_failure_is_consumed_and_controls_restored(
    qtbot,
    monkeypatch,
    failure_at: str,
) -> None:
    dialog = SettingsDialog(_configured_settings())
    qtbot.addWidget(dialog)
    closed: list[bool] = []
    warnings: list[str] = []

    class PlannerClient:
        async def aclose(self) -> None:
            closed.append(True)
            if failure_at == "close":
                raise RuntimeError("Fermeture https://1drv.ms/x/private-token")

        async def list_plans(self) -> list[PlannerPlan]:
            if failure_at == "request":
                raise RuntimeError("Requete https://1drv.ms/x/private-token")
            return [PlannerPlan(id="saved-plan", title="Plan projets")]

        async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
            return [PlannerBucket(id="saved-bucket", name="En cours", plan_id=plan_id)]

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.planner_refresh_button.click()
    task = dialog._planner_task  # noqa: SLF001
    assert task is not None

    await task

    assert closed == [True]
    assert len(warnings) == 1
    assert "private-token" not in warnings[0]
    assert dialog.planner_refresh_button.isEnabled()
    assert dialog.planner_bucket_refresh_button.isEnabled()
    assert dialog.planner_test_button.isEnabled()
    assert dialog.planner_refresh_button.text() == "Détecter"


@pytest.mark.asyncio
async def test_closing_settings_cancels_detection_and_closes_planner_client(
    qtbot, monkeypatch
) -> None:
    dialog = SettingsDialog(_configured_settings())
    qtbot.addWidget(dialog)
    entered = asyncio.Event()
    closed: list[bool] = []
    warnings: list[str] = []

    class PlannerClient:
        async def aclose(self) -> None:
            closed.append(True)

        async def list_plans(self) -> list[PlannerPlan]:
            entered.set()
            await asyncio.Event().wait()
            return []

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.planner_refresh_button.click()
    task = dialog._planner_task  # noqa: SLF001
    assert task is not None
    await asyncio.wait_for(entered.wait(), timeout=2)

    dialog.reject()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed == [True]
    assert not warnings


def test_microsoft_sign_in_starts_on_click_without_saving(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = SettingsDialog(AppConfig())
    qtbot.addWidget(dialog)
    requested: list[bool] = []
    dialog.microsoft_sign_in_requested.connect(lambda: requested.append(True))

    dialog.microsoft_sign_in_button.click()

    assert requested == [True]
    assert not dialog.microsoft_sign_in_button.isEnabled()
    assert "navigateur" in dialog.microsoft_status_label.text()
    assert dialog.result() == 0  # Still open: no OK needed.

    dialog.microsoft_sign_in_finished("Connecté : alice@example.com")

    assert dialog.microsoft_sign_in_button.isEnabled()
    assert dialog.microsoft_sign_in_button.text() == "Se reconnecter au compte Microsoft"
    assert dialog.microsoft_status_label.text() == "Connecté : alice@example.com"


@pytest.mark.asyncio
async def test_default_planner_members_are_chosen_from_the_plan_and_saved(
    qtbot,
    monkeypatch,
) -> None:
    config = _configured_settings()
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    offered: list[tuple[list[str], list[str]]] = []

    class PlannerClient:
        async def aclose(self) -> None:
            pass

        async def list_members(self, *, plan_id: str) -> list[PlannerMember]:
            assert plan_id == "saved-plan"
            return [
                PlannerMember(id="lionel-id", display_name="Lionel", email=""),
                PlannerMember(id="romain-id", display_name="Romain", email=""),
            ]

    def choose(_parent, members, selected) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
        offered.append(([member.label for member in members], list(selected)))
        return ("romain-id",)

    monkeypatch.setattr("projectflow.ui.dialogs.settings._planner_client", PlannerClient)
    monkeypatch.setattr("projectflow.ui.dialogs.settings.choose_planner_members", choose)
    assert dialog.planner_members_label.text() == "Compte Microsoft connecté"

    await dialog._choose_planner_members()  # noqa: SLF001
    dialog.apply_to_config(config)

    assert offered == [(["Lionel", "Romain"], [])]
    assert dialog.planner_members_label.text() == "Romain"
    assert config.planner.default_assignee_ids == ["romain-id"]
    assert config.planner.default_assignee_names == ["Romain"]

    # Members belong to the plan's group: choosing another plan forgets them.
    dialog.planner_plan_combo.addItem("Autre plan", "other-plan")
    dialog.planner_plan_combo.setCurrentIndex(dialog.planner_plan_combo.count() - 1)
    dialog.apply_to_config(config)
    assert config.planner.default_assignee_ids == []


PLANNING_MEMBERS = [
    ("m-lm", "Lionel Mottiez", "lionel@balzmetal.ch"),
    ("m-kd", "Kevin Duc", "kevin.duc@balzmetal.ch"),
]


def _planning_dialog(  # type: ignore[no-untyped-def]
    qtbot,
    planning: PlanningPosteConfig | None,
    config: AppConfig | None = None,
) -> SettingsDialog:
    dialog = SettingsDialog(config or AppConfig(), planning_config=planning)
    qtbot.addWidget(dialog)
    return dialog


def test_planning_section_sits_before_microsoft_account(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = _planning_dialog(qtbot, PlanningPosteConfig(poste_name="BUREAU-LM"))

    dialog.show_section("planning")
    planning_index = dialog.pages.currentIndex()
    dialog.show_section("ms")

    assert dialog.pages.currentIndex() == planning_index + 1
    assert dialog._section_buttons.button(planning_index + 1).isChecked()  # noqa: SLF001
    dialog.show_section("appearance")
    assert dialog.pages.currentIndex() == dialog.pages.count() - 1


def test_planning_identity_is_chosen_from_microsoft_members(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = PlanningPosteConfig(poste_name="BUREAU-LM")
    dialog = _planning_dialog(qtbot, planning)
    assert not dialog.planning_member_combo.isEnabled()

    dialog.set_planning_members(PLANNING_MEMBERS)
    assert dialog.planning_member_combo.isEnabled()
    assert dialog.planning_member_combo.currentIndex() == -1
    dialog.planning_member_combo.setCurrentIndex(1)

    assert dialog.planning_name_edit.text() == "Kevin Duc"
    assert dialog.planning_initials_edit.text() == "KD"
    dialog.planning_initials_edit.setText("kdu")
    dialog.apply_to_planning_config(planning)

    assert planning.user_m365_id == "m-kd"
    assert planning.user_name == "Kevin Duc"
    assert planning.user_initials == "KDU"
    assert planning.author().label == "KDU · BUREAU-LM"
    assert dialog._section_meta["planning"].text() == "KDU · OneDrive"  # noqa: SLF001


def test_saved_planning_member_is_selected_and_another_name_unlinks_it(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = PlanningPosteConfig(
        user_initials="LM",
        user_name="Lionel Mottiez",
        user_m365_id="m-lm",
    )
    dialog = _planning_dialog(qtbot, planning)

    dialog.set_planning_members(PLANNING_MEMBERS)
    assert dialog.planning_member_combo.currentIndex() == 0

    dialog.planning_name_edit.setText("Julie Dumas")
    dialog.planning_name_edit.textEdited.emit("Julie Dumas")
    dialog.apply_to_planning_config(planning)

    assert dialog.planning_member_combo.currentIndex() == -1
    assert planning.user_m365_id == ""
    assert planning.user_name == "Julie Dumas"


def test_name_typed_back_keeps_the_microsoft_link(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = PlanningPosteConfig(user_name="Lionel Mottiez", user_m365_id="m-lm")
    dialog = _planning_dialog(qtbot, planning)

    # Before the members are loaded, a key typed then erased leaves the same name.
    qtbot.keyClick(dialog.planning_name_edit, Qt.Key.Key_Space)
    qtbot.keyClick(dialog.planning_name_edit, Qt.Key.Key_Backspace)
    assert dialog.planning_name_edit.text() == "Lionel Mottiez"
    dialog.set_planning_members(PLANNING_MEMBERS)

    assert dialog.planning_member_combo.currentIndex() == 0
    dialog.apply_to_planning_config(planning)
    assert planning.user_m365_id == "m-lm"

    dialog.planning_name_edit.setText("Julie Dumas")
    dialog.planning_name_edit.textEdited.emit("Julie Dumas")
    assert dialog.planning_member_combo.currentIndex() == -1
    dialog.planning_name_edit.setText("Lionel  Mottiez")
    dialog.planning_name_edit.textEdited.emit("Lionel  Mottiez")
    assert dialog.planning_member_combo.currentIndex() == 0


def test_planning_initials_default_to_the_fiche_initials(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    config.user.initials = "LM"

    dialog = _planning_dialog(qtbot, PlanningPosteConfig(), config)

    assert dialog.planning_initials_edit.text() == "LM"


def test_sharepoint_storage_shows_the_admin_consent_banner(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = _planning_dialog(qtbot, PlanningPosteConfig())
    assert dialog.planning_onedrive_radio.isChecked()
    assert dialog.planning_storage_banner.isHidden()
    assert dialog.planning_sharepoint_fields.isHidden()

    dialog.planning_sharepoint_radio.click()

    assert not dialog.planning_storage_banner.isHidden()
    assert not dialog.planning_sharepoint_fields.isHidden()
    message = dialog.planning_storage_banner.message_label.text()
    assert "Sites.ReadWrite.All" in message
    assert "administrateur" in message
    assert dialog._section_meta["planning"].text() == "SharePoint"  # noqa: SLF001

    dialog.planning_onedrive_radio.click()
    assert dialog.planning_storage_banner.isHidden()


def test_apply_to_planning_config_writes_storage_and_view_defaults(qtbot) -> None:  # type: ignore[no-untyped-def]
    planning = PlanningPosteConfig(
        backend="sharepoint",
        sharepoint_site_url="https://balz.sharepoint.com/sites/atelier",
        sharepoint_site_id="site-id",
        sharepoint_list_id="list-id",
    )
    dialog = _planning_dialog(qtbot, planning)
    assert not dialog.planning_storage_banner.isHidden()
    poste_id = planning.poste_id

    dialog.planning_poste_edit.setText(" atelier-1 ")
    dialog.planning_view_choice.option("month").click()
    dialog.planning_density_choice.option("compact").click()
    dialog.planning_color_choice.option("dossier").click()
    dialog.apply_to_planning_config(planning)

    # Unchanged site and list keep their resolved identifiers.
    assert (planning.sharepoint_site_id, planning.sharepoint_list_id) == ("site-id", "list-id")
    assert planning.poste_name == "atelier-1"
    assert planning.poste_id == poste_id
    assert (planning.default_view, planning.density, planning.color_by) == (
        "month",
        "compact",
        "dossier",
    )

    # SharePoint ignores the case and a closing slash: still the same site and list.
    key = planning.cache_key()
    dialog.planning_site_edit.setText("https://Balz.sharepoint.com/sites/Atelier/")
    dialog.planning_list_edit.setText(planning.sharepoint_list_name.upper())
    dialog.apply_to_planning_config(planning)
    assert (planning.sharepoint_site_id, planning.sharepoint_list_id) == ("site-id", "list-id")
    assert planning.cache_key() == key

    dialog.planning_list_edit.setText("Planning atelier")
    dialog.apply_to_planning_config(planning)
    assert (planning.sharepoint_site_id, planning.sharepoint_list_id) == ("site-id", "")
    assert planning.sharepoint_list_name == "Planning atelier"

    dialog.planning_site_edit.setText("https://balz.sharepoint.com/sites/bureau")
    dialog.planning_onedrive_radio.click()
    dialog.apply_to_planning_config(planning)
    assert planning.backend == "onedrive"
    assert planning.sharepoint_site_url == "https://balz.sharepoint.com/sites/bureau"
    assert (planning.sharepoint_site_id, planning.sharepoint_list_id) == ("", "")


def test_apply_to_config_leaves_the_planning_alone(qtbot) -> None:  # type: ignore[no-untyped-def]
    config = AppConfig()
    planning = PlanningPosteConfig(user_initials="LM")
    dialog = _planning_dialog(qtbot, planning, config)

    dialog.planning_initials_edit.setText("XY")
    dialog.apply_to_config(config)

    assert planning.user_initials == "LM"
    assert config.user.initials == ""


def test_sharepoint_without_site_is_refused(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    dialog = _planning_dialog(qtbot, PlanningPosteConfig())
    warnings: list[str] = []
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.warning",
        lambda _parent, _title, text: warnings.append(text),
    )
    dialog.planning_sharepoint_radio.click()

    dialog.accept()
    dialog.planning_site_edit.setText("balz.sharepoint.com/sites/atelier")
    dialog.accept()

    assert dialog.result() != QDialog.DialogCode.Accepted
    assert warnings == [
        "Indiquez l'adresse du site SharePoint du planning ou choisissez OneDrive.",
        "L'adresse du site SharePoint commence par https://.",
    ]


def test_planning_team_button_asks_for_the_roster(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = _planning_dialog(qtbot, PlanningPosteConfig())
    requested: list[bool] = []
    dialog.planning_team_requested.connect(lambda: requested.append(True))

    dialog.planning_team_button.click()

    assert requested == [True]
    assert dialog.result() == 0


def test_planning_section_without_config_is_disabled(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = _planning_dialog(qtbot, None)

    dialog.set_planning_members(PLANNING_MEMBERS)

    assert not dialog.planning_team_button.isEnabled()
    assert not dialog.planning_initials_edit.isEnabled()
    assert not dialog.planning_member_combo.isEnabled()
    assert dialog._section_meta["planning"].text() == ""  # noqa: SLF001
