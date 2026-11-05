from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from projectflow.core.planning import local_store
from projectflow.core.planning.local_store import (
    JOURNAL_FILE_NAME,
    LocalPlanningStore,
)
from projectflow.core.planning.store import (
    PlanningStore,
    StoreChanges,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)

ETAG = re.compile(r"([0-9]+)-[0-9a-f]{8}")
CURSOR = re.compile(r"([0-9a-f]{16}):([0-9]+):([0-9a-f]{8})?")


def _journal_lines(directory: Path) -> list[dict[str, object]]:
    text = (directory / JOURNAL_FILE_NAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _seq(etag: str) -> int:
    match = ETAG.fullmatch(etag)
    assert match is not None, etag
    return int(match[1])


def _cursor(cursor: str) -> tuple[str, int]:
    match = CURSOR.fullmatch(cursor)
    assert match is not None, cursor
    return match[1], int(match[2])


def test_local_store_satisfies_the_protocol(tmp_path: Path) -> None:
    store: PlanningStore = LocalPlanningStore(tmp_path)
    assert isinstance(store, LocalPlanningStore)


@pytest.mark.asyncio
async def test_create_then_get_returns_the_document_with_its_first_etag(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path / "store")
    data = {"id": "t1", "title": "Soudure garde-corps é", "assignments": {"r1": {}}}

    created = await store.create("task", "t1", data, project="2026-5245")
    data["title"] = "changed after the call"

    assert created == StoredDocument(
        kind="task",
        doc_id="t1",
        etag=created.etag,
        data={"id": "t1", "title": "Soudure garde-corps é", "assignments": {"r1": {}}},
    )
    assert _seq(created.etag) == 1
    assert await store.get("task", "t1") == created
    assert await store.get("task", "missing") is None
    on_disk = json.loads((tmp_path / "store" / "task" / "t1.json").read_text(encoding="utf-8"))
    assert on_disk == {"etag": created.etag, "data": created.data}
    header, entry = _journal_lines(tmp_path / "store")
    assert set(header) == {"generation", "floor", "last"}
    assert (header["floor"], header["last"]) == (0, 0)
    assert entry == {
        "seq": 1,
        "kind": "task",
        "doc_id": "t1",
        "tag": created.etag.split("-")[1],
    }


@pytest.mark.asyncio
async def test_update_requires_the_current_etag(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)
    created = await store.create("task", "t1", {"title": "A"})

    etag = await store.update("task", "t1", created.etag, {"title": "B"})
    with pytest.raises(StoreConflictError):
        await store.update("task", "t1", created.etag, {"title": "C"})

    current = await store.get("task", "t1")
    assert _seq(etag) == 2
    assert current is not None
    assert current.etag == etag
    assert current.data == {"title": "B"}


@pytest.mark.asyncio
async def test_update_of_a_missing_document_and_duplicate_creation_conflict(
    tmp_path: Path,
) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("resource", "r1", {"name": "Marc"})

    with pytest.raises(StoreConflictError):
        await store.update("resource", "nobody", "1", {"name": "X"})
    with pytest.raises(StoreConflictError):
        await store.create("resource", "r1", {"name": "Marc bis"})

    current = await store.get("resource", "r1")
    assert current is not None
    assert current.data == {"name": "Marc"}


@pytest.mark.asyncio
async def test_delete_requires_the_current_etag_and_ignores_missing_documents(
    tmp_path: Path,
) -> None:
    store = LocalPlanningStore(tmp_path)
    created = await store.create("absence", "a1", {"reason": "vacances"})
    etag = await store.update("absence", "a1", created.etag, {"reason": "ecole"})

    with pytest.raises(StoreConflictError):
        await store.delete("absence", "a1", created.etag)
    await store.delete("absence", "a1", etag)
    await store.delete("absence", "a1", etag)

    assert await store.get("absence", "a1") is None
    assert not (tmp_path / "absence" / "a1.json").exists()
    last = _journal_lines(tmp_path)[-1]
    assert {key: value for key, value in last.items() if key != "tag"} == {
        "seq": 3,
        "kind": "absence",
        "doc_id": "a1",
        "deleted": True,
    }


@pytest.mark.asyncio
async def test_changes_enumerates_everything_then_only_what_changed(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)
    empty = await store.changes(None)
    task = await store.create("task", "t1", {"title": "A"})
    resource = await store.create("resource", "r1", {"name": "Marc"})
    await store.create("meta", "meta", {"default_canton": "GE"})

    full = await store.changes(None)
    task_etag = await store.update("task", "t1", task.etag, {"title": "B"})
    await store.delete("resource", "r1", resource.etag)
    since = await store.changes(full.cursor)
    unchanged = await store.changes(since.cursor)

    assert empty == StoreChanges(documents=(), cursor=":0:", full=True)
    assert full.full is True
    generation, seq = _cursor(full.cursor)
    assert seq == 3
    assert {(document.kind, document.doc_id) for document in full.documents} == {
        ("task", "t1"),
        ("resource", "r1"),
        ("meta", "meta"),
    }
    assert since.full is False
    assert _cursor(since.cursor) == (generation, 5)
    deletion = since.documents[1]
    assert since.documents == (
        StoredDocument(kind="task", doc_id="t1", etag=task_etag, data={"title": "B"}),
        StoredDocument(kind="resource", doc_id="r1", etag=deletion.etag, data=None, deleted=True),
    )
    assert _seq(deletion.etag) == 5
    assert unchanged.documents == ()
    assert unchanged.cursor == since.cursor
    assert unchanged.full is False


@pytest.mark.asyncio
async def test_changes_reports_a_document_once_with_its_latest_version(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("task", "t0", {"n": 0})
    start = await store.changes(None)
    created = await store.create("task", "t1", {"n": 1})
    etag = await store.update("task", "t1", created.etag, {"n": 2})
    etag = await store.update("task", "t1", etag, {"n": 3})
    await store.create("task", "t2", {"n": 1})

    changes = await store.changes(start.cursor)

    assert changes.full is False
    assert [(document.doc_id, document.data) for document in changes.documents] == [
        ("t1", {"n": 3}),
        ("t2", {"n": 1}),
    ]
    assert changes.documents[0].etag == etag


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    ["not-a-number", "", "99", "1", "²", "abc:²:", "0123456789abcdef:1:", ":1:"],
)
async def test_unusable_cursor_reads_everything(tmp_path: Path, cursor: str) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("task", "t1", {"n": 1})

    changes = await store.changes(cursor)

    assert changes.full is True
    assert [document.doc_id for document in changes.documents] == ["t1"]
    assert _cursor(changes.cursor)[1] == 1


@pytest.mark.asyncio
async def test_two_stores_on_one_directory_see_each_other(tmp_path: Path) -> None:
    poste_a = LocalPlanningStore(tmp_path)
    poste_b = LocalPlanningStore(tmp_path)
    cursor_b = (await poste_b.changes(None)).cursor

    created = await poste_a.create("task", "t1", {"title": "A"})
    seen_by_b = await poste_b.changes(cursor_b)
    etag_b = await poste_b.update("task", "t1", created.etag, {"title": "B"})
    with pytest.raises(StoreConflictError):
        await poste_a.update("task", "t1", created.etag, {"title": "A2"})
    current_a = await poste_a.get("task", "t1")
    seen_by_a = await poste_a.changes(seen_by_b.cursor)

    assert seen_by_b.documents == (created,)
    assert current_a == StoredDocument(kind="task", doc_id="t1", etag=etag_b, data={"title": "B"})
    assert seen_by_a.full is False
    assert seen_by_a.documents == (current_a,)


@pytest.mark.asyncio
async def test_concurrent_updates_from_two_stores_let_exactly_one_win(tmp_path: Path) -> None:
    poste_a = LocalPlanningStore(tmp_path)
    poste_b = LocalPlanningStore(tmp_path)
    created = await poste_a.create("task", "t1", {"title": "A"})

    results = await asyncio.gather(
        *(
            store.update("task", "t1", created.etag, {"title": f"from {index}"})
            for index, store in enumerate([poste_a, poste_b] * 4)
        ),
        return_exceptions=True,
    )

    winners = [result for result in results if isinstance(result, str)]
    conflicts = [result for result in results if isinstance(result, StoreConflictError)]
    assert len(winners) == 1
    assert len(conflicts) == len(results) - 1
    current = await poste_b.get("task", "t1")
    assert current is not None
    assert current.etag == winners[0]


@pytest.mark.asyncio
async def test_concurrent_creations_get_distinct_sequences(tmp_path: Path) -> None:
    poste_a = LocalPlanningStore(tmp_path)
    poste_b = LocalPlanningStore(tmp_path)

    created = await asyncio.gather(
        *(
            store.create("task", f"t{index}", {"n": index})
            for index, store in enumerate([poste_a, poste_b] * 10)
        ),
    )

    etags = sorted(_seq(document.etag) for document in created)
    assert etags == list(range(1, 21))
    entries = [line for line in _journal_lines(tmp_path) if "seq" in line]
    assert sorted(int(str(line["seq"])) for line in entries) == etags
    full = await poste_b.changes(None)
    assert len(full.documents) == 20
    assert _cursor(full.cursor)[1] == 20


@pytest.mark.asyncio
async def test_unparsable_documents_count_as_missing_everywhere(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("task", "good", {"n": 1})
    start = await store.changes(None)
    created = await store.create("task", "broken", {"n": 2})
    (tmp_path / "task" / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "task" / "other.json").write_text('{"etag": 3}', encoding="utf-8")
    (tmp_path / "task" / "ignored name.json").write_text("{}", encoding="utf-8")

    with capture_logs() as logs:
        full = await store.changes(None)
        since = await store.changes(start.cursor)
        missing = await store.get("task", "broken")

    assert [document.doc_id for document in full.documents] == ["good"]
    # A full read leaves it out, so an incremental read reports it gone as well.
    assert since.documents == (
        StoredDocument(kind="task", doc_id="broken", etag=created.etag, data=None, deleted=True),
    )
    assert missing is None
    skipped = [entry for entry in logs if entry["event"] == "planning.local.document_skipped"]
    assert {Path(str(entry["path"])).name for entry in skipped} == {"broken.json", "other.json"}
    assert all(entry["log_level"] == "warning" for entry in skipped)
    with pytest.raises(StoreConflictError):
        await store.update("task", "broken", created.etag, {"n": 3})
    repaired = await store.create("task", "broken", {"n": 3})
    assert await store.get("task", "broken") == repaired
    assert (await store.changes(since.cursor)).documents == (repaired,)


@pytest.mark.asyncio
async def test_locked_document_raises_and_keeps_the_change_for_the_next_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_store, "FILE_RETRY_SECONDS", 0)
    store = LocalPlanningStore(tmp_path)
    created = await store.create("task", "t1", {"title": "v1"})
    start = await store.changes(None)
    etag = await store.update("task", "t1", created.etag, {"title": "v2"})
    locked = tmp_path / "task" / "t1.json"
    read_bytes = Path.read_bytes

    def refuse_locked(path: Path) -> bytes:
        if path == locked:
            raise PermissionError(13, "locked", str(path))
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", refuse_locked)
    for operation in (
        lambda: store.changes(start.cursor),
        lambda: store.changes(None),
        lambda: store.get("task", "t1"),
        lambda: store.create("task", "t1", {"title": "overwrite"}),
        lambda: store.update("task", "t1", etag, {"title": "v3"}),
        lambda: store.delete("task", "t1", etag),
    ):
        with pytest.raises(StoreUnavailableError, match="verrouille"):
            await operation()
    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    changes = await store.changes(start.cursor)
    assert changes.documents == (
        StoredDocument(kind="task", doc_id="t1", etag=etag, data={"title": "v2"}),
    )


@pytest.mark.asyncio
async def test_unreadable_journal_lines_are_skipped_and_partial_line_is_closed(
    tmp_path: Path,
) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("task", "t0", {"n": 0})
    start = await store.changes(None)
    await store.create("task", "t1", {"n": 1})
    with (tmp_path / JOURNAL_FILE_NAME).open("ab") as journal:
        journal.write(b'garbage\n{"seq": "x"}\n{"seq": 2, "kind": "ta')

    with capture_logs() as logs:
        created = await store.create("task", "t2", {"n": 2})
        changes = await store.changes(start.cursor)

    assert changes.full is False
    assert [document.doc_id for document in changes.documents] == ["t1", "t2"]
    assert _cursor(changes.cursor) == (_cursor(start.cursor)[0], 3)
    content = (tmp_path / JOURNAL_FILE_NAME).read_bytes()
    tag = created.etag.split("-")[1]
    expected_end = f'"kind": "ta\n{{"seq":3,"kind":"task","doc_id":"t2","tag":"{tag}"}}\n'
    assert content.endswith(expected_end.encode())
    warnings = [entry for entry in logs if entry["event"] == "planning.local.journal_lines_skipped"]
    assert len(warnings) == 1
    assert warnings[0]["lines"] == 3


@pytest.mark.asyncio
async def test_journal_compaction_keeps_cursors_correct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(local_store, "JOURNAL_COMPACTION_LINES", 6)
    store = LocalPlanningStore(tmp_path)
    keep = await store.create("task", "keep", {"n": 0})
    gone = await store.create("task", "gone", {"n": 0})
    before_deletion = (await store.changes(None)).cursor
    await store.delete("task", "gone", gone.etag)
    after_deletion = (await store.changes(None)).cursor
    etag = keep.etag
    for value in range(1, 5):
        etag = await store.update("task", "keep", etag, {"n": value})

    generation = _cursor(before_deletion)[0]
    assert _journal_lines(tmp_path) == [
        {"generation": generation, "floor": 3, "last": 7},
        {"seq": 7, "kind": "task", "doc_id": "keep", "tag": etag.split("-")[1]},
    ]
    old = await store.changes(before_deletion)
    recent = await store.changes(after_deletion)
    assert old.full is True
    assert [document.doc_id for document in old.documents] == ["keep"]
    assert recent.full is False
    assert [(document.doc_id, document.etag) for document in recent.documents] == [("keep", etag)]
    assert _seq(await store.update("task", "keep", etag, {"n": 5})) == 8


@pytest.mark.asyncio
async def test_lost_journal_starts_a_new_generation(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)
    await store.create("task", "t1", {"n": 1})
    created = await store.create("task", "t2", {"n": 1})
    old_cursor = (await store.changes(None)).cursor
    (tmp_path / JOURNAL_FILE_NAME).unlink()

    before_write = await store.changes(old_cursor)
    etag = await store.update("task", "t2", created.etag, {"n": 2})
    changes = await store.changes(old_cursor)

    assert before_write.full is True
    assert _seq(etag) == 3
    assert changes.full is True
    assert sorted(document.doc_id for document in changes.documents) == ["t1", "t2"]
    header = _journal_lines(tmp_path)[0]
    assert (header["floor"], header["last"]) == (2, 2)
    assert header["generation"] != _cursor(old_cursor)[0]
    assert _cursor(changes.cursor)[0] == header["generation"]


@pytest.mark.asyncio
async def test_lost_journal_never_hides_writes_behind_reused_sequences(tmp_path: Path) -> None:
    poste_a = LocalPlanningStore(tmp_path)
    t1 = await poste_a.create("task", "t1", {"n": 1})
    t2 = await poste_a.create("task", "t2", {"n": 1})
    await poste_a.delete("task", "t2", t2.etag)
    cursor_b = (await poste_a.changes(None)).cursor
    (tmp_path / JOURNAL_FILE_NAME).unlink()

    await poste_a.create("task", "t3", {"n": 1})
    await poste_a.update("task", "t1", t1.etag, {"n": 2})
    seen = await LocalPlanningStore(tmp_path).changes(cursor_b)

    assert _cursor(cursor_b)[1] == 3
    assert seen.full is True
    assert sorted((document.doc_id, document.data) for document in seen.documents) == [
        ("t1", {"n": 2}),
        ("t3", {"n": 1}),
    ]


@pytest.mark.asyncio
async def test_restored_folder_never_reuses_an_etag_or_a_cursor(tmp_path: Path) -> None:
    directory = tmp_path / "store"
    backup = tmp_path / "backup"
    store = LocalPlanningStore(directory)
    created = await store.create("task", "t1", {"title": "v1"})
    shutil.copytree(directory, backup)
    etag = await store.update("task", "t1", created.etag, {"title": "v2"})
    etag_b = await store.update("task", "t1", etag, {"title": "v3"})
    cursor_b = (await store.changes(None)).cursor  # poste B holds v3
    shutil.rmtree(directory)
    shutil.copytree(backup, directory)

    restored = LocalPlanningStore(directory)
    etag_a = await restored.update("task", "t1", created.etag, {"title": "A edits v1"})
    etag_a = await restored.update("task", "t1", etag_a, {"title": "A edits again"})
    seen = await restored.changes(cursor_b)

    assert _cursor(seen.cursor) == _cursor(cursor_b)
    assert _seq(etag_a) == _seq(etag_b)
    assert etag_a != etag_b
    assert seen.full is True
    assert seen.documents == (
        StoredDocument(kind="task", doc_id="t1", etag=etag_a, data={"title": "A edits again"}),
    )
    with pytest.raises(StoreConflictError):
        await restored.update("task", "t1", etag_b, {"title": "B edit based on v3"})


@pytest.mark.asyncio
async def test_cursor_of_another_directory_reads_everything(tmp_path: Path) -> None:
    store_a = LocalPlanningStore(tmp_path / "a")
    store_b = LocalPlanningStore(tmp_path / "b")
    await store_a.create("task", "x1", {"n": 1})
    await store_a.create("task", "x2", {"n": 1})
    cursor = (await store_a.changes(None)).cursor
    for index in range(3):
        await store_b.create("task", f"y{index}", {"n": index})

    seen = await store_b.changes(cursor)

    assert seen.full is True
    assert [document.doc_id for document in seen.documents] == ["y0", "y1", "y2"]


@pytest.mark.asyncio
async def test_odd_etag_on_disk_does_not_break_a_store_without_journal(tmp_path: Path) -> None:
    (tmp_path / "task").mkdir()
    (tmp_path / "task" / "t1.json").write_text(
        json.dumps({"etag": "²", "data": {}}),
        encoding="utf-8",
    )
    (tmp_path / "task" / "t2.json").write_text(
        json.dumps({"etag": "4", "data": {}}),
        encoding="utf-8",
    )
    store = LocalPlanningStore(tmp_path)

    created = await store.create("task", "t3", {})
    etag = await store.update("task", "t2", "4", {"n": 1})

    assert _seq(created.etag) == 5
    assert _seq(etag) == 6


@pytest.mark.asyncio
async def test_invalid_names_and_data_are_rejected(tmp_path: Path) -> None:
    store = LocalPlanningStore(tmp_path)

    with pytest.raises(ValueError, match="Identifiant"):
        await store.create("task", "../escape", {})
    with pytest.raises(ValueError, match="Type"):
        await store.get("task/x", "t1")
    with pytest.raises(ValueError, match="JSON"):
        await store.create("task", "t1", {"value": object()})
    with pytest.raises(ValueError, match="JSON"):
        await store.create("task", "t1", {"value": float("nan")})

    assert not (tmp_path / "task").exists()
    await store.aclose()
