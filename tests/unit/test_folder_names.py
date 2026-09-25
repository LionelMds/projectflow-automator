from __future__ import annotations

from pathlib import Path

from projectflow.platform.folder_names import find_child_directory, folder_key


def test_folder_key_ignores_accents_case_and_outer_spaces() -> None:
    assert folder_key(" Plan d'Exécution ") == folder_key("plan d'execution")
    assert folder_key("Plans") != folder_key("Plan")


def test_find_child_directory_matches_accented_folder(tmp_path: Path) -> None:
    accented = tmp_path / "Plan d'exécution"
    accented.mkdir()
    (tmp_path / "plan d'execution.pdf").touch()

    assert find_child_directory(tmp_path, "plan d'execution") == accented
    assert find_child_directory(tmp_path, "photos") is None
    assert find_child_directory(tmp_path / "absent", "photos") is None
