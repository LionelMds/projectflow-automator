from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from projectflow.core.planning import poste_config
from projectflow.core.planning.models import Author
from projectflow.core.planning.poste_config import (
    MAX_PANEL_WIDTH,
    MIN_PANEL_WIDTH,
    PlanningPosteConfig,
    planning_config_file,
)
from projectflow.exceptions import ConfigError
from projectflow.graph.planning_store import DEFAULT_PLANNING_LIST_NAME


@pytest.fixture(autouse=True)
def planning_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(poste_config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(poste_config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(poste_config.platform, "node", lambda: " atelier-pc ")
    return tmp_path


def _saved() -> dict[str, object]:
    payload = json.loads(planning_config_file().read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_first_load_uses_defaults_and_keeps_the_generated_poste_id(planning_dirs: Path) -> None:
    config = PlanningPosteConfig.load()
    again = PlanningPosteConfig.load()

    assert planning_config_file() == planning_dirs / "config" / "planning.json"
    assert config.enabled is True
    assert config.backend == "local"
    assert config.sharepoint_list_name == "ProjectFlow Planning"
    assert config.poste_name == "ATELIER-PC"
    assert len(config.poste_id) == 32
    assert again.poste_id == config.poste_id
    assert _saved()["poste_id"] == config.poste_id
    assert config.default_view == "week"
    assert config.density == "normal"
    assert config.color_by == "type"
    assert config.hidden_resource_ids == []
    assert config.collapsed_teams == []
    assert config.panel_open is True
    assert config.panel_width == 312
    assert config.backlog_sort == "manual"
    assert config.local_store_directory() == planning_dirs / "data" / "planning" / "local-store"
    assert config.cache_directory() == planning_dirs / "data" / "planning" / config.cache_key()
    assert re.fullmatch(r"local-[0-9a-f]{8}", config.cache_key())
    assert config.is_identified is False


def test_save_then_load_round_trips_and_signs_changes(planning_dirs: Path) -> None:
    config = PlanningPosteConfig(
        backend="sharepoint",
        sharepoint_site_url=" https://balz.sharepoint.com/sites/atelier ",
        sharepoint_site_id="site-1",
        sharepoint_list_id="list-1",
        poste_name="Bureau 2",
        user_initials=" lm ",
        user_name="Lionel Mottiez",
        user_m365_id="user-1",
        default_view="month",
        density="large",
        color_by="dossier",
        hidden_resource_ids=["r1", " r1 ", "", "r2"],
        collapsed_teams=["atelier"],
        panel_open=False,
        panel_width=400,
        backlog_sort="deadline",
    )

    config.save()
    loaded = PlanningPosteConfig.load()

    assert loaded == config
    assert loaded.sharepoint_site_url == "https://balz.sharepoint.com/sites/atelier"
    assert loaded.user_initials == "LM"
    assert loaded.hidden_resource_ids == ["r1", "r2"]
    assert loaded.author() == Author(
        initials="LM",
        name="Lionel Mottiez",
        poste="Bureau 2",
        poste_id=config.poste_id,
    )
    assert loaded.author().label == "LM · Bureau 2"
    assert loaded.is_identified is True
    assert [path.name for path in (planning_dirs / "config").iterdir()] == ["planning.json"]


def test_corrupt_file_falls_back_to_defaults_and_keeps_a_backup(planning_dirs: Path) -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    with capture_logs() as logs:
        config = PlanningPosteConfig.load()

    assert config.backend == "local"
    assert (path.parent / "planning.json.bak").read_text(encoding="utf-8") == "{not json"
    assert _saved()["poste_id"] == config.poste_id
    assert [entry["event"] for entry in logs] == ["planning.config.unreadable"]
    assert logs[0]["log_level"] == "warning"


def test_invalid_values_reset_only_their_field(planning_dirs: Path) -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    original = {
        "poste_id": "poste-123",
        "poste_name": "ATELIER 1",
        "user_initials": "jd",
        "default_view": "year",
        "panel_width": "wide",
        "hidden_resource_ids": ["r1", 5],
        "a_future_setting": {"x": 1},
    }
    path.write_text(json.dumps(original), encoding="utf-8")

    with capture_logs() as logs:
        config = PlanningPosteConfig.load()

    assert config.poste_id == "poste-123"
    assert config.poste_name == "ATELIER 1"
    assert config.user_initials == "JD"
    assert config.default_view == "week"
    assert config.panel_width == 312
    assert config.hidden_resource_ids == []
    assert config.model_extra == {"a_future_setting": {"x": 1}}
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert logs[0]["event"] == "planning.config.fields_reset"
    assert logs[0]["fields"] == ["default_view", "hidden_resource_ids", "panel_width"]


def test_settings_of_a_newer_version_survive_a_save() -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"poste_id": "p", "user_initials": "LM", "week_starts_on": "monday"}),
        encoding="utf-8",
    )

    config = PlanningPosteConfig.load()
    config.panel_open = False
    config.save()

    assert _saved()["week_starts_on"] == "monday"
    assert _saved()["panel_open"] is False
    assert PlanningPosteConfig.load() == config


def test_file_written_with_a_utf8_bom_is_read() -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    original = {"poste_id": "poste-123", "user_initials": "LM", "backend": "sharepoint"}
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(original).encode())

    config = PlanningPosteConfig.load()

    assert (config.poste_id, config.user_initials, config.backend) == (
        "poste-123",
        "LM",
        "sharepoint",
    )
    assert not (path.parent / "planning.json.bak").exists()
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("content", ["[]", "null", '"text"'])
def test_json_that_is_not_an_object_is_replaced(content: str) -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    config = PlanningPosteConfig.load()

    assert _saved()["poste_id"] == config.poste_id
    assert (path.parent / "planning.json.bak").exists()


def test_missing_poste_id_is_generated_and_saved() -> None:
    path = planning_config_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"poste_id": " ", "user_initials": "ab"}), encoding="utf-8")

    config = PlanningPosteConfig.load()

    assert config.poste_id.strip()
    assert _saved()["poste_id"] == config.poste_id
    assert _saved()["user_initials"] == "AB"


def test_values_are_normalized_on_assignment(planning_dirs: Path) -> None:
    config = PlanningPosteConfig()

    config.panel_width = 5000
    wide = config.panel_width
    config.panel_width = 10
    narrow = config.panel_width
    config.poste_name = "  "
    config.sharepoint_list_name = ""
    config.local_directory = " ~/planning-test "

    assert wide == MAX_PANEL_WIDTH
    assert narrow == MIN_PANEL_WIDTH
    assert config.poste_name == "ATELIER-PC"
    assert config.sharepoint_list_name == "ProjectFlow Planning"
    assert config.local_store_directory() == Path.home() / "planning-test"
    assert PlanningPosteConfig(poste_id="").poste_id


def test_unreadable_file_uses_defaults_without_overwriting(planning_dirs: Path) -> None:
    path = planning_dirs / "config-as-directory"
    path.mkdir()

    with capture_logs() as logs:
        config = PlanningPosteConfig.load(path)

    assert config.backend == "local"
    assert path.is_dir()
    assert logs[0]["event"] == "planning.config.unreadable"


def test_save_failure_raises_config_error(planning_dirs: Path) -> None:
    blocker = planning_dirs / "blocker"
    blocker.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="configuration du planning"):
        PlanningPosteConfig().save(blocker / "planning.json")


def test_load_survives_an_unwritable_location(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: PlanningPosteConfig, path: Path | None = None) -> None:
        raise ConfigError("disque plein")

    monkeypatch.setattr(PlanningPosteConfig, "save", refuse)

    with capture_logs() as logs:
        config = PlanningPosteConfig.load()

    assert config.backend == "local"
    assert not planning_config_file().exists()
    assert [entry["event"] for entry in logs] == ["planning.config.save_failed"]


# ------------------------------------------------------------ cache key ----


def _sha1_prefix(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def test_cache_key_of_a_sharepoint_list_hashes_the_address_and_the_name() -> None:
    config = PlanningPosteConfig(
        backend="sharepoint",
        sharepoint_site_url="https://balz.sharepoint.com/sites/atelier",
        sharepoint_list_name="Planning atelier",
    )
    identity = "balz.sharepoint.com/sites/atelier" + chr(10) + "planning atelier"

    assert config.cache_key() == f"sharepoint-{_sha1_prefix(identity)}"
    assert config.cache_directory().parent == poste_config.planning_cache_directory()
    assert config.cache_directory().name == config.cache_key()


@pytest.mark.parametrize(
    ("site_url", "list_name"),
    [
        ("HTTPS://Balz.SharePoint.com/sites/Atelier/", "planning ATELIER"),
        ("balz.sharepoint.com/sites/atelier", " Planning atelier "),
    ],
)
def test_cache_key_ignores_how_the_address_and_the_name_are_written(
    site_url: str, list_name: str
) -> None:
    reference = PlanningPosteConfig(
        backend="sharepoint",
        sharepoint_site_url="https://balz.sharepoint.com/sites/atelier",
        sharepoint_list_name="Planning atelier",
    )
    written_otherwise = PlanningPosteConfig(
        backend="sharepoint", sharepoint_site_url=site_url, sharepoint_list_name=list_name
    )

    assert written_otherwise.cache_key() == reference.cache_key()


def test_cache_key_stays_when_the_ids_are_resolved_after_a_first_use() -> None:
    # The settings clear the ids when the address or the name change; they are looked up
    # again while the poste may already have used (and cached writes for) the list.
    first_use = PlanningPosteConfig(
        backend="sharepoint", sharepoint_site_url="https://balz.sharepoint.com/sites/atelier"
    )
    resolved = first_use.model_copy(
        update={"sharepoint_site_id": "balz.sharepoint.com,1111,2222", "sharepoint_list_id": "ab"}
    )

    assert resolved.cache_key() == first_use.cache_key()
    assert resolved.cache_directory() == first_use.cache_directory()


def test_cache_key_of_a_sharepoint_list_without_address_uses_its_ids() -> None:
    config = PlanningPosteConfig(
        backend="sharepoint", sharepoint_site_id="Site-1", sharepoint_list_id="List-1"
    )
    by_name = config.model_copy(update={"sharepoint_list_id": ""})

    assert config.cache_key() == f"sharepoint-{_sha1_prefix('site-1' + chr(10) + 'list-1')}"
    identity = "site-1" + chr(10) + DEFAULT_PLANNING_LIST_NAME.casefold()
    assert by_name.cache_key() == f"sharepoint-{_sha1_prefix(identity)}"


def test_cache_key_changes_with_the_store_the_site_or_the_list() -> None:
    by_address = PlanningPosteConfig(
        backend="sharepoint", sharepoint_site_url="https://balz.sharepoint.com/sites/atelier"
    )
    by_ids = PlanningPosteConfig(
        backend="sharepoint", sharepoint_site_id="site-1", sharepoint_list_id="list-1"
    )
    keys = {
        by_address.cache_key(),
        by_address.model_copy(update={"sharepoint_list_name": "Autre"}).cache_key(),
        by_address.model_copy(update={"sharepoint_site_url": "https://other"}).cache_key(),
        by_ids.cache_key(),
        by_ids.model_copy(update={"sharepoint_list_id": "list-2"}).cache_key(),
        by_ids.model_copy(update={"sharepoint_site_id": "site-2"}).cache_key(),
        by_ids.model_copy(update={"backend": "local"}).cache_key(),
    }

    assert len(keys) == 7


def test_cache_key_of_the_local_store_follows_its_folder(
    planning_dirs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = PlanningPosteConfig()
    shared = PlanningPosteConfig(local_directory=str(planning_dirs / "partage"))
    monkeypatch.chdir(planning_dirs)
    relative = PlanningPosteConfig(local_directory="partage")
    resolved = (planning_dirs / "data" / "planning" / "local-store").resolve()

    assert default.cache_key() == f"local-{_sha1_prefix(os.path.normcase(str(resolved)))}"
    assert shared.cache_key() != default.cache_key()
    assert relative.cache_key() == shared.cache_key()
    assert default.cache_directory() != default.local_store_directory()
    assert PlanningPosteConfig().cache_key() == default.cache_key()


@pytest.mark.skipif(os.name != "nt", reason="Windows compares folder names without case")
def test_cache_key_of_the_local_store_ignores_the_case_on_windows(planning_dirs: Path) -> None:
    lower = PlanningPosteConfig(local_directory=str(planning_dirs / "partage"))
    upper = PlanningPosteConfig(local_directory=str(planning_dirs / "PARTAGE"))

    assert lower.cache_key() == upper.cache_key()
