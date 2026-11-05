from __future__ import annotations

import pytest

from projectflow.core.numero import (
    ProjectNumber,
    canonical_project_number,
    format_project_number,
    is_main_project_number,
    is_project_number,
    next_project_id,
    parse_project_number,
    project_folder_name,
    validate_project_id,
    validate_subproject_id,
    validate_year,
)
from projectflow.exceptions import ProjectNumberError


def test_validate_year_accepts_four_digits() -> None:
    assert validate_year("2026") == 2026


@pytest.mark.parametrize("value", ["26", "202", "20266", "abcd", "20 6"])
def test_validate_year_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ProjectNumberError):
        validate_year(value)


def test_validate_project_id_accepts_three_or_more_digits() -> None:
    assert validate_project_id("4995") == "4995"
    assert validate_project_id(123) == "123"


@pytest.mark.parametrize("value", ["12", "abc", "12A", ""])
def test_validate_project_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ProjectNumberError):
        validate_project_id(value)


def test_validate_subproject_id_normalizes_blank_to_none() -> None:
    assert validate_subproject_id(None) is None
    assert validate_subproject_id("") is None
    assert validate_subproject_id("  ") is None


@pytest.mark.parametrize("value", ["0", "-1", "A", "1.2"])
def test_validate_subproject_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ProjectNumberError):
        validate_subproject_id(value)


def test_format_project_number_formats_main_project() -> None:
    assert format_project_number("2026", "4995") == "2026-4995"


def test_format_project_number_formats_subproject() -> None:
    assert format_project_number("2026", "4995", "2") == "2026-4995-2"


def test_parse_project_number_parses_main_project() -> None:
    assert parse_project_number("2026-4995") == ProjectNumber(year=2026, project_id="4995")


def test_parse_project_number_parses_subproject() -> None:
    number = parse_project_number("2026-4995-2")

    assert number == ProjectNumber(year=2026, project_id="4995", subproject_id="2")
    assert number.is_subproject is True
    assert str(number.parent) == "2026-4995"


@pytest.mark.parametrize("value", ["2026-12", "202-4995", "2026/4995", "Total"])
def test_parse_project_number_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ProjectNumberError):
        parse_project_number(value)


def test_is_main_project_number_ignores_subprojects() -> None:
    assert is_main_project_number("2026-4995") is True
    assert is_main_project_number("2026-4995-2") is False


def test_project_folder_name_returns_parent_for_subproject() -> None:
    assert project_folder_name("2026-4995-2") == "2026-4995"


def test_next_project_id_uses_main_projects_for_given_year() -> None:
    assert next_project_id(["2026-4995", "2026-4995-2", "2025-8000", "note"], year=2026) == "4996"


def test_next_project_id_returns_none_when_year_has_no_projects() -> None:
    assert next_project_id(["2025-8000"], year=2026) is None


@pytest.mark.parametrize(
    ("typed", "number"),
    [
        ("2024-4493-A1", "2024-4493-A1"),
        ("2026-5102-REV03", "2026-5102-REV03"),
        ("2026-5102 REV03", "2026-5102-REV03"),
        ("2026-5102_rev03", "2026-5102-REV03"),
        ("2024 \u2013 4493 a1", "2024-4493-A1"),  # typographic dash
        (" 2026-118-02 ", "2026-118-2"),
        ("2026-4995-A", "2026-4995-A"),
    ],
)
def test_hand_written_numbers_are_still_dossiers(typed: str, number: str) -> None:
    assert canonical_project_number(typed) == number
    assert is_project_number(typed)
    parsed = parse_project_number(typed)
    assert str(parsed) == number
    assert project_folder_name(parsed) == number.rsplit("-", number.count("-") - 1)[0]


def test_text_that_is_no_number_stays_as_it_is() -> None:
    assert canonical_project_number("  Total  ") == "Total"
    assert not is_project_number("202-3502-2")
