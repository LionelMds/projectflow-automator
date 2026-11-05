from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from projectflow.core.planning.copy import CopyReport, copy_planning
from projectflow.core.planning.local_store import LocalPlanningStore, has_documents
from projectflow.core.planning.store import StoreConflictError, StoreUnavailableError


def task(doc_id: str, title: str = "Pose") -> dict[str, Any]:
    return {"id": doc_id, "title": title}


async def _fill(store: LocalPlanningStore, *documents: tuple[str, str, dict[str, Any]]) -> None:
    for kind, doc_id, data in documents:
        await store.create(kind, doc_id, data)


async def _contents(store: LocalPlanningStore) -> dict[tuple[str, str], Any]:
    changes = await store.changes(None)
    return {(doc.kind, doc.doc_id): doc.data for doc in changes.documents}


@pytest.mark.asyncio
async def test_copy_adds_missing_documents_and_never_overwrites(tmp_path: Path) -> None:
    source = LocalPlanningStore(tmp_path / "local")
    target = LocalPlanningStore(tmp_path / "onedrive")
    await _fill(
        source,
        ("task", "a", task("a")),
        ("task", "b", task("b")),
        ("resource", "r1", {"id": "r1", "name": "Kevin"}),
        ("meta", "meta", {"id": "meta", "default_canton": "GE"}),
    )
    await _fill(
        target,
        ("task", "b", task("b", "Version OneDrive")),
        ("meta", "meta", {"id": "meta", "default_canton": "VD"}),
    )

    report = await copy_planning(source, target)

    assert report == CopyReport(created=2, skipped_existing=2, failed=0)
    contents = await _contents(target)
    assert contents["task", "a"] == task("a")
    assert contents["task", "b"] == task("b", "Version OneDrive")
    assert contents["resource", "r1"] == {"id": "r1", "name": "Kevin"}
    assert contents["meta", "meta"] == {"id": "meta", "default_canton": "VD"}

    again = await copy_planning(source, target)

    assert again == CopyReport(created=0, skipped_existing=4, failed=0)
    assert await _contents(target) == contents


@pytest.mark.asyncio
async def test_meta_is_copied_when_the_target_has_none(tmp_path: Path) -> None:
    source = LocalPlanningStore(tmp_path / "local")
    target = LocalPlanningStore(tmp_path / "onedrive")
    await _fill(source, ("meta", "meta", {"id": "meta"}), ("task", "a", task("a")))

    report = await copy_planning(source, target)

    assert report.created == 2
    assert ("meta", "meta") in await _contents(target)


@pytest.mark.asyncio
async def test_dry_run_counts_without_writing(tmp_path: Path) -> None:
    source = LocalPlanningStore(tmp_path / "local")
    target = LocalPlanningStore(tmp_path / "onedrive")
    await _fill(source, ("task", "a", task("a")), ("task", "b", task("b")))
    await _fill(target, ("task", "b", task("b")))

    report = await copy_planning(source, target, dry_run=True)

    assert report == CopyReport(created=1, skipped_existing=1, dry_run=True)
    assert "à copier" in report.summary()
    assert set(await _contents(target)) == {("task", "b")}


class FlakyTarget:
    """Target refusing some creations; empty otherwise."""

    def __init__(self, inner: LocalPlanningStore, refusals: dict[str, Exception]) -> None:
        self.inner = inner
        self.refusals = refusals

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def create(self, kind: str, doc_id: str, data: Any, *, project: str = "") -> Any:
        refusal = self.refusals.get(doc_id)
        if refusal is not None:
            raise refusal
        return await self.inner.create(kind, doc_id, data, project=project)


@pytest.mark.asyncio
async def test_conflicts_count_as_present_and_errors_as_failed(tmp_path: Path) -> None:
    source = LocalPlanningStore(tmp_path / "local")
    await _fill(
        source, ("task", "a", task("a")), ("task", "b", task("b")), ("task", "c", task("c"))
    )
    target = FlakyTarget(
        LocalPlanningStore(tmp_path / "onedrive"),
        {"a": StoreConflictError("créé ailleurs"), "b": ValueError("nom invalide")},
    )

    report = await copy_planning(source, target)  # type: ignore[arg-type]

    assert (report.created, report.skipped_existing, report.failed) == (1, 1, 1)
    assert report.errors == ("nom invalide",)


@pytest.mark.asyncio
async def test_unreachable_target_stops_the_copy(tmp_path: Path) -> None:
    source = LocalPlanningStore(tmp_path / "local")
    await _fill(source, ("task", "a", task("a")))
    target = FlakyTarget(
        LocalPlanningStore(tmp_path / "onedrive"),
        {"a": StoreUnavailableError("hors ligne")},
    )

    with pytest.raises(StoreUnavailableError):
        await copy_planning(source, target)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_has_documents_tells_an_empty_local_store(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path / "local")
    assert not has_documents(tmp_path / "local")
    assert not has_documents(tmp_path / "missing")

    await _fill(store, ("task", "a", task("a")))

    assert has_documents(tmp_path / "local")
