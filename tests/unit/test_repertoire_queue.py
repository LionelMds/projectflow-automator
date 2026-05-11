from __future__ import annotations

from datetime import date
from pathlib import Path

from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_queue import RepertoireTransactionStore


def test_repertoire_transaction_store_roundtrips_project(tmp_path: Path) -> None:
    store = RepertoireTransactionStore(tmp_path)
    project = ProjectInput(
        number=parse_project_number("2026-4995"),
        designation="Escalier",
        societe="Balz",
        contact="Lionel",
        localisation="Zurich",
        gere_par="LM",
    )

    transaction = store.create(
        project,
        force_overwrite=True,
        repertoire_date=date(2026, 5, 11),
    )

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].id == transaction.id
    assert pending[0].project == project
    assert pending[0].force_overwrite is True
    assert pending[0].repertoire_date == date(2026, 5, 11)

    store.delete(pending[0])

    assert store.list_pending() == []


def test_repertoire_transaction_store_uses_workbook_sidecar_folder(tmp_path: Path) -> None:
    workbook_path = tmp_path / "Repertoire chantier.xlsx"

    store = RepertoireTransactionStore.for_workbook(workbook_path)
    store.create(
        ProjectInput(number=parse_project_number("2026-4995")),
        force_overwrite=False,
        repertoire_date=date(2026, 5, 11),
    )

    assert (tmp_path / "ProjectFlow pending repertoire").exists()
