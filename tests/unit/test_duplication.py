from __future__ import annotations

import pytest

from projectflow.core.duplication import assert_description_empty, prepare_subproject_row
from projectflow.core.numero import parse_project_number
from projectflow.exceptions import ProjectCreationError


def test_duplicate_subproject_row_inserts_after_contiguous_group() -> None:
    rows = [
        ["2026-4995", "Balz", "", "", "Main"],
        ["2026-4995-1", "Balz", "", "", "Sub 1"],
        ["2026-5000", "", "", "", ""],
    ]

    insert_index, prepared = prepare_subproject_row(rows, parse_project_number("2026-4995-2"))

    assert insert_index == 2
    assert prepared == ["2026-4995-2", "", "", "", ""]


def test_prepare_subproject_row_preserves_width_without_copying_values() -> None:
    rows = [
        ["2026-4995", "Balz", "Lionel", "Zurich", "Main", "Code"],
        ["2026-5000", "", "", "", "", ""],
    ]

    _insert_index, prepared = prepare_subproject_row(
        rows,
        parse_project_number("2026-4995-2"),
    )

    assert prepared == ["2026-4995-2", "", "", "", "", ""]


def test_duplicate_subproject_row_rejects_missing_parent() -> None:
    with pytest.raises(ProjectCreationError):
        prepare_subproject_row([], parse_project_number("2026-4995-2"))


def test_assert_description_empty_rejects_non_empty_description() -> None:
    with pytest.raises(ProjectCreationError):
        assert_description_empty(["2026-4995", "", "", "", "Projet"], force_overwrite=False)


def test_assert_description_empty_rejects_non_empty_client_columns() -> None:
    with pytest.raises(ProjectCreationError):
        assert_description_empty(["2026-4995", "", "Contact", "", ""], force_overwrite=False)


def test_assert_description_empty_allows_force_overwrite() -> None:
    assert_description_empty(["2026-4995", "", "", "", "Projet"], force_overwrite=True)
