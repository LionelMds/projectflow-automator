from __future__ import annotations

import asyncio
import os
import stat
from datetime import date
from pathlib import Path
from typing import BinaryIO

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill
from openpyxl.worksheet.table import Table

from projectflow.core import local_repertoire
from projectflow.core.local_repertoire import LocalWorkbookGateway
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ConfigError, ProjectCreationError

TODAY = date(2026, 5, 11)


def _create_repertoire(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026"
    worksheet.append(
        [
            "Numero",
            "Date",
            "Societe",
            "Contact",
            "Description",
            "F",
            "G",
            "H",
            "I",
            "J",
            "K",
            "L",
        ]
    )
    worksheet.append(["2026-4995", "", "", "", "", "", "", "", "", "", "", ""])
    worksheet.append(["2026-5000", "", "", "", "", "", "", "", "", "", "", ""])
    workbook.save(path)
    workbook.close()


def _create_styled_repertoire(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "2026"
    worksheet.append(
        [
            "Numero",
            "Date",
            "Societe",
            "Contact",
            "Description",
            "F",
            "G",
            "H",
            "I",
            "J",
            "K",
            "L",
        ]
    )
    worksheet.append(
        [
            "2026-4995",
            TODAY,
            "Balz",
            "Lionel",
            "Escalier",
            "Parent-F",
            "Parent-G",
            "Parent-H",
            "Parent-I",
            "Parent-J",
            "Parent-K",
            "Parent-L",
        ]
    )
    worksheet.append(
        [
            "2026-5000",
            "",
            "",
            "",
            "",
            "Next-F",
            "Next-G",
            "Next-H",
            "Next-I",
            "Next-J",
            "Next-K",
            "Next-L",
        ]
    )
    fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    for column_index in range(1, 13):
        worksheet.cell(row=3, column=column_index).fill = fill
    worksheet.row_dimensions[3].height = 28
    workbook.save(path)
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_updates_main_project(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path), today=lambda: TODAY)
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="LM",
    )

    await service.upsert_project(project)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A2"].value == "2026-4995"
    assert worksheet["B2"].value.date() == TODAY
    assert worksheet["B2"].number_format == "DD.MM.YYYY"
    assert worksheet["C2"].value == "Balz"
    assert worksheet["D2"].value == "Lionel"
    assert worksheet["E2"].value == "Escalier"
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_inserts_subproject_without_overwriting_next_row(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path), today=lambda: TODAY)
    parent = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")
    subproject = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await service.upsert_project(parent)
    await service.upsert_project(subproject)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A2"].value == "2026-4995"
    assert worksheet["A3"].value == "2026-4995-2"
    assert worksheet["A4"].value == "2026-5000"
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_inserts_subproject_with_blank_values_and_available_row_format(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_styled_repertoire(path)
    service = RepertoireService(LocalWorkbookGateway(path), today=lambda: TODAY)
    subproject = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
        contact="Contact saisi",
    )

    await service.upsert_project(subproject)

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A3"].value == "2026-4995-2"
    assert worksheet["B3"].value.date() == TODAY
    assert worksheet["B3"].number_format == "DD.MM.YYYY"
    assert worksheet["C3"].value is None
    assert worksheet["D3"].value == "Contact saisi"
    assert worksheet["E3"].value == "Variante"
    assert worksheet["F3"].value is None
    assert worksheet["G3"].value is None
    assert worksheet["L3"].value is None
    assert worksheet["A4"].value == "2026-5000"
    assert worksheet["F2"].value == "Parent-F"
    assert worksheet["L2"].value == "Parent-L"
    assert worksheet["F4"].value == "Next-F"
    assert worksheet["L4"].value == "Next-L"
    assert worksheet["A3"].fill.fgColor.rgb == "00FFF2CC"
    assert worksheet.row_dimensions[3].height == 28
    workbook.close()


@pytest.mark.asyncio
async def test_local_repertoire_inserts_subproject_without_copying_accounting_table_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_styled_repertoire(path)
    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    worksheet.add_table(Table(displayName="Repertoire", ref="A1:L3"))
    workbook.save(path)
    workbook.close()
    service = RepertoireService(LocalWorkbookGateway(path), today=lambda: TODAY)

    await service.upsert_project(
        ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante"),
    )

    workbook = load_workbook(path)
    worksheet = workbook["2026"]
    assert worksheet["A3"].value == "2026-4995-2"
    assert worksheet["E3"].value == "Variante"
    assert [worksheet.cell(row=3, column=column).value for column in range(6, 13)] == [
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]
    assert [worksheet.cell(row=2, column=column).value for column in range(6, 13)] == [
        "Parent-F",
        "Parent-G",
        "Parent-H",
        "Parent-I",
        "Parent-J",
        "Parent-K",
        "Parent-L",
    ]
    assert [worksheet.cell(row=4, column=column).value for column in range(6, 13)] == [
        "Next-F",
        "Next-G",
        "Next-H",
        "Next-I",
        "Next-J",
        "Next-K",
        "Next-L",
    ]
    assert worksheet.tables["Repertoire"].ref == "A1:L4"
    workbook.close()


@pytest.mark.asyncio
async def test_reading_local_repertoire_never_saves_or_changes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    modified_at = path.stat().st_mtime_ns

    def unexpected_save(*args: object, **kwargs: object) -> None:
        pytest.fail("A read-only operation must not save the workbook")

    monkeypatch.setattr(Workbook, "save", unexpected_save)
    service = RepertoireService(LocalWorkbookGateway(path))

    snapshot = await service.read_snapshot(year=2026)
    available = await service.next_available(year=2026)
    await service.validate_project_deletion(
        number=parse_project_number("2026-4995"),
        rows=[snapshot.rows[0]],
    )

    assert available is not None
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == modified_at
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_unchanged_local_values_do_not_save(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    gateway = LocalWorkbookGateway(path)

    async with gateway.session():
        await gateway.update_range_values("2026", "A2", [["2026-4995"]])

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_failed_serialization_preserves_original_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    gateway = LocalWorkbookGateway(path)

    def incomplete_save(self: Workbook, destination: BinaryIO) -> None:
        destination.write(b"incomplete workbook")
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(Workbook, "save", incomplete_save)
        with pytest.raises(ProjectCreationError, match="original a ete conserve"):
            async with gateway.session():
                await gateway.update_range_values("2026", "E2", [["Escalier"]])

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))
    async with gateway.session():
        await gateway.update_range_values("2026", "E2", [["Escalier"]])
    workbook = load_workbook(path)
    assert workbook["2026"]["E2"].value == "Escalier"
    workbook.close()


@pytest.mark.asyncio
async def test_invalid_serialized_workbook_never_replaces_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()

    def incomplete_save(self: Workbook, destination: BinaryIO) -> None:
        destination.write(b"incomplete workbook")

    monkeypatch.setattr(Workbook, "save", incomplete_save)
    gateway = LocalWorkbookGateway(path)
    with pytest.raises(ProjectCreationError, match="original a ete conserve"):
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_failed_atomic_replacement_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()

    def locked_replace(source: Path, destination: Path) -> None:
        raise PermissionError("Workbook opened in Excel")

    monkeypatch.setattr(local_repertoire.os, "replace", locked_replace)
    gateway = LocalWorkbookGateway(path)
    with pytest.raises(ProjectCreationError, match="droits d'ecriture"):
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_concurrent_edit_is_detected_even_when_size_and_mtime_are_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    original_stat = path.stat()
    gateway = LocalWorkbookGateway(path)
    # Change the ZIP header bytes without changing its length or timestamp. The
    # external content must be preserved regardless of whether it remains valid.
    concurrent_content = b"xx" + original[2:]

    async def write_with_concurrent_change() -> None:
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])
            path.write_bytes(concurrent_content)
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    with pytest.raises(ProjectCreationError, match="modifie pendant"):
        await write_with_concurrent_change()

    assert path.read_bytes() == concurrent_content


@pytest.mark.asyncio
async def test_edit_during_serialization_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    concurrent_path = tmp_path / "concurrent.xlsx"
    _create_repertoire(concurrent_path)
    concurrent_workbook = load_workbook(concurrent_path)
    concurrent_workbook["2026"]["F2"] = "Accounting update"
    concurrent_workbook.save(concurrent_path)
    concurrent_workbook.close()
    concurrent_content = concurrent_path.read_bytes()
    original_save = Workbook.save

    def save_with_external_edit(self: Workbook, destination: BinaryIO) -> None:
        original_save(self, destination)
        path.write_bytes(concurrent_content)

    monkeypatch.setattr(Workbook, "save", save_with_external_edit)
    gateway = LocalWorkbookGateway(path)
    with pytest.raises(ProjectCreationError, match="modifie pendant"):
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])

    assert path.read_bytes() == concurrent_content
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_excel_owner_file_blocks_writes_but_allows_reads(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    owner_file = path.with_name(f"~${path.name}")
    owner_file.write_bytes(b"Excel owner")
    gateway = LocalWorkbookGateway(path)

    async with gateway.session():
        assert await gateway.worksheet_exists("2026")
    with pytest.raises(ProjectCreationError, match="verrouille par Excel"):
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])
    assert path.read_bytes() == original

    owner_file.unlink()
    async with gateway.session():
        await gateway.update_range_values("2026", "E2", [["Escalier"]])


@pytest.mark.asyncio
async def test_read_only_workbook_is_not_replaced(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    gateway = LocalWorkbookGateway(path)
    try:
        async with gateway.session():
            assert await gateway.worksheet_exists("2026")
        with pytest.raises(ProjectCreationError, match="lecture seule"):
            async with gateway.session():
                await gateway.update_range_values("2026", "E2", [["Escalier"]])
        assert path.read_bytes() == original
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.asyncio
async def test_corrupt_workbook_error_does_not_poison_next_session(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    path.write_bytes(b"corrupt workbook")
    gateway = LocalWorkbookGateway(path)

    with pytest.raises(ProjectCreationError, match="endommage"):
        async with gateway.session():
            pytest.fail("A corrupt workbook must not open")
    assert path.read_bytes() == b"corrupt workbook"

    _create_repertoire(path)
    async with gateway.session():
        assert await gateway.worksheet_exists("2026")


@pytest.mark.asyncio
async def test_nested_session_commits_only_after_outer_session(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    gateway = LocalWorkbookGateway(path)

    async with gateway.session():
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Escalier"]])
        assert path.read_bytes() == original

    workbook = load_workbook(path)
    assert workbook["2026"]["E2"].value == "Escalier"
    workbook.close()


@pytest.mark.asyncio
async def test_caught_nested_failure_does_not_commit_partial_changes(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    gateway = LocalWorkbookGateway(path)

    async def failing_nested_operation() -> None:
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Partial change"]])
            raise ValueError("operation failed")

    async def outer_operation() -> None:
        async with gateway.session():
            with pytest.raises(ValueError, match="operation failed"):
                await failing_nested_operation()

    with pytest.raises(ProjectCreationError, match="Enregistrement annule"):
        await outer_operation()
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_another_task_cannot_join_active_session(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    gateway = LocalWorkbookGateway(path)

    async def competing_operation() -> None:
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Other task"]])

    async with gateway.session():
        with pytest.raises(ProjectCreationError, match="autre operation"):
            await asyncio.create_task(competing_operation())
        await gateway.update_range_values("2026", "E2", [["Original task"]])

    workbook = load_workbook(path)
    assert workbook["2026"]["E2"].value == "Original task"
    workbook.close()


@pytest.mark.asyncio
async def test_concurrent_gateway_cannot_overwrite_successful_commit(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    first = LocalWorkbookGateway(path)
    second = LocalWorkbookGateway(path)

    async def write_with_competing_gateway() -> None:
        async with first.session():
            await first.update_range_values("2026", "E2", [["Old snapshot"]])
            async with second.session():
                await second.update_range_values("2026", "E2", [["Committed change"]])

    with pytest.raises(ProjectCreationError, match="modifie pendant"):
        await write_with_competing_gateway()

    workbook = load_workbook(path)
    assert workbook["2026"]["E2"].value == "Committed change"
    workbook.close()


@pytest.mark.asyncio
async def test_released_process_lock_never_blocks_future_writes(tmp_path: Path) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    gateway = LocalWorkbookGateway(path)

    with (
        local_repertoire._exclusive_write_lock(path),  # noqa: SLF001
        pytest.raises(ProjectCreationError, match="autre instance"),
    ):
        async with gateway.session():
            await gateway.update_range_values("2026", "E2", [["Blocked"]])

    async with gateway.session():
        await gateway.update_range_values("2026", "E2", [["Retry succeeded"]])
    workbook = load_workbook(path)
    assert workbook["2026"]["E2"].value == "Retry succeeded"
    workbook.close()


@pytest.mark.asyncio
async def test_local_gateway_refuses_synchronized_workbook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "repertoire.xlsx"
    _create_repertoire(path)
    original = path.read_bytes()
    monkeypatch.setattr(local_repertoire, "is_synchronized_path", lambda _path: True)
    gateway = LocalWorkbookGateway(path)

    with pytest.raises(ConfigError, match="connexion Microsoft 365"):
        async with gateway.session():
            pytest.fail("Synchronized workbooks must use the cloud gateway")
    assert path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["Repertoire (non fusionne).xlsx", "Repertoire unmerged.xlsx"])
async def test_local_gateway_refuses_recovery_copy(tmp_path: Path, filename: str) -> None:
    path = tmp_path / filename
    _create_repertoire(path)
    original = path.read_bytes()
    gateway = LocalWorkbookGateway(path)

    with pytest.raises(ConfigError, match="copie de recuperation"):
        async with gateway.session():
            pytest.fail("Recovery copies must not become the active repertoire")
    assert path.read_bytes() == original
