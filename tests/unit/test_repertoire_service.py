from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import pytest

from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_queue import RepertoireTransactionStore
from projectflow.core.repertoire_service import RepertoireService
from projectflow.exceptions import ProjectCreationError

TODAY = date(2026, 5, 11)


class FakeWorkbook:
    def __init__(
        self,
        rows: list[list[Any]],
    ) -> None:
        self.rows = rows
        self.updated_ranges: list[tuple[str, str, list[list[Any]]]] = []
        self.inserted_rows: list[tuple[str, int, int | None, int]] = []
        self.session_count = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        self.session_count += 1
        yield

    async def worksheet_exists(self, worksheet_name: str) -> bool:
        return worksheet_name == "2026"

    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        return self.rows

    async def update_range_values(
        self,
        worksheet_name: str,
        address: str,
        values: list[list[Any]],
    ) -> None:
        self.updated_ranges.append((worksheet_name, address, values))

    async def insert_blank_row(
        self,
        worksheet_name: str,
        row_index: int,
        *,
        copy_format_from_row_index: int | None = None,
        format_width: int = 12,
    ) -> None:
        self.inserted_rows.append((
            worksheet_name,
            row_index,
            copy_format_from_row_index,
            format_width,
        ))


class CountingTransactionStore(RepertoireTransactionStore):
    def __init__(self, directory: Any) -> None:
        super().__init__(directory)
        self.list_count = 0

    def list_pending(self) -> list[Any]:
        self.list_count += 1
        return super().list_pending()


class UnavailableWorkbook(FakeWorkbook):
    async def used_range_values(self, worksheet_name: str) -> list[list[Any]]:
        raise PermissionError("locked")


@pytest.mark.asyncio
async def test_next_available_returns_first_main_project_with_empty_info_columns() -> None:
    workbook = FakeWorkbook([
        ["2026-4995", "", "", "", "Occupe"],
        ["2026-4996", "Balz", "", "", ""],
        ["2026-4997", "", "Lionel", "", ""],
        ["2026-4998", "", "", "Zurich", ""],
        ["2026-4996", "", "", "", ""],
        ["2026-4996-1", "", "", "", ""],
    ])

    result = await RepertoireService(workbook).next_available(year=2026)

    assert result is not None
    assert str(result.number) == "2026-4996"
    assert result.row_index == 4
    assert workbook.session_count == 1


@pytest.mark.asyncio
async def test_upsert_project_updates_existing_empty_row() -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", "", "Ne pas toucher"]])
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="LM",
    )

    await RepertoireService(workbook, today=lambda: TODAY).upsert_project(project)

    assert workbook.updated_ranges == [
        ("2026", "A1:E1", [["2026-4995", TODAY, "Balz", "Lionel", "Escalier"]]),
    ]


@pytest.mark.asyncio
async def test_upsert_project_rejects_filled_description_without_force() -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", "Occupe", ""]])
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    with pytest.raises(ProjectCreationError):
        await RepertoireService(workbook).upsert_project(project)


@pytest.mark.asyncio
async def test_upsert_project_rejects_filled_client_columns_without_force() -> None:
    workbook = FakeWorkbook([["2026-4995", "", "Contact existant", "", "", ""]])
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    with pytest.raises(ProjectCreationError, match="colonnes B a E"):
        await RepertoireService(workbook).upsert_project(project)


@pytest.mark.asyncio
async def test_upsert_subproject_inserts_blank_row_before_writing_only_a_to_e() -> None:
    workbook = FakeWorkbook([
        ["2026-4995", "Balz", "", "", "Escalier", "LM"],
        ["2026-5000", "", "", "", "", ""],
    ])
    project = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await RepertoireService(workbook, today=lambda: TODAY).upsert_project(project)

    assert workbook.inserted_rows == [("2026", 1, 1, 12)]
    assert workbook.updated_ranges == [
        ("2026", "A2:E2", [["2026-4995-2", TODAY, "", "", "Variante"]]),
    ]


@pytest.mark.asyncio
async def test_upsert_subproject_does_not_write_unrelated_sixth_column() -> None:
    workbook = FakeWorkbook([["2026-4995", "Balz", "", "", "Escalier", "Code interne"]])
    project = ProjectInput(
        number=parse_project_number("2026-4995-2"),
        designation="Variante",
        gere_par="LM",
    )

    await RepertoireService(workbook, today=lambda: TODAY).upsert_project(project)

    assert workbook.updated_ranges == [
        ("2026", "A2:E2", [["2026-4995-2", TODAY, "", "", "Variante"]]),
    ]


@pytest.mark.asyncio
async def test_upsert_existing_subproject_updates_existing_row_without_inserting() -> None:
    workbook = FakeWorkbook([
        ["2026-4995", "Balz", "", "", "Escalier", "Code interne"],
        ["2026-4995-2", "Balz", "", "", "Ancienne variante", "Sous-code"],
        ["2026-5000", "", "", "", "", ""],
    ])
    project = ProjectInput(number=parse_project_number("2026-4995-2"), designation="Variante")

    await RepertoireService(workbook, today=lambda: TODAY).upsert_project(project)

    assert workbook.inserted_rows == []
    assert workbook.updated_ranges == [
        ("2026", "A2:E2", [["2026-4995-2", TODAY, "", "", "Variante"]]),
    ]


@pytest.mark.asyncio
async def test_upsert_with_transaction_keeps_pending_when_verification_fails(
    tmp_path: Any,
) -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", ""]])
    store = RepertoireTransactionStore(tmp_path)
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
    )

    with pytest.raises(ProjectCreationError, match="conservee en attente"):
        await RepertoireService(
            workbook,
            today=lambda: TODAY,
            transaction_store=store,
        ).upsert_project(project)

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].project == project


@pytest.mark.asyncio
async def test_upsert_project_does_not_replay_pending_transactions(
    tmp_path: Any,
) -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", ""]])
    store = CountingTransactionStore(tmp_path)
    store.create(
        ProjectInput(number=parse_project_number("2026-4994"), designation="Ancien"),
        force_overwrite=False,
        repertoire_date=TODAY,
    )
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Nouveau")

    with pytest.raises(ProjectCreationError, match="conservee en attente"):
        await RepertoireService(
            workbook,
            today=lambda: TODAY,
            transaction_store=store,
        ).upsert_project(project)

    assert store.list_count == 0


@pytest.mark.asyncio
async def test_upsert_with_transaction_removes_pending_for_business_rejection(
    tmp_path: Any,
) -> None:
    workbook = FakeWorkbook([["2026-4995", "", "", "", "Occupe"]])
    store = RepertoireTransactionStore(tmp_path)
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    with pytest.raises(ProjectCreationError, match="colonnes B a E"):
        await RepertoireService(
            workbook,
            today=lambda: TODAY,
            transaction_store=store,
        ).upsert_project(project)

    assert store.list_pending() == []


@pytest.mark.asyncio
async def test_upsert_with_transaction_keeps_pending_when_workbook_unavailable(
    tmp_path: Any,
) -> None:
    workbook = UnavailableWorkbook([["2026-4995", "", "", "", ""]])
    store = RepertoireTransactionStore(tmp_path)
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")

    with pytest.raises(ProjectCreationError, match="conservee en attente"):
        await RepertoireService(
            workbook,
            today=lambda: TODAY,
            transaction_store=store,
        ).upsert_project(project)

    assert len(store.list_pending()) == 1


@pytest.mark.asyncio
async def test_sync_pending_defers_recent_verified_transactions(tmp_path: Any) -> None:
    workbook = FakeWorkbook([["2026-4995", TODAY, "", "", "Escalier"]])
    store = RepertoireTransactionStore(tmp_path)
    project = ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier")
    store.create(project, force_overwrite=False, repertoire_date=TODAY)
    service = RepertoireService(
        workbook,
        today=lambda: TODAY,
        transaction_store=store,
    )

    result = await service.sync_pending(minimum_age_seconds=3600.0)

    assert result.deferred == 1
    assert result.already_verified == 1
    assert len(store.list_pending()) == 1
