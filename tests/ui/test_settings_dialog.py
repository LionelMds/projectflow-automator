from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from PySide6.QtWidgets import QDialog

from projectflow.config import AppConfig
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient
from projectflow.graph.excel import GraphExcelWorkbookGateway
from projectflow.graph.planner import PlannerBucket, PlannerPlan
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
