"""Planning store in JSON files, for the demo mode, the tests and a poste without Microsoft.

Layout of the store directory::

    <kind>/<doc_id>.json   {"etag": "<seq>-<tag>", "data": {...}}, replaced atomically
    journal.jsonl          a header {"generation": g, "floor": f, "last": l}, then one line
                           per write: {"seq": n, "kind": ..., "doc_id": ..., "tag": t}
    .lock                  locked by the OS while one store reads or writes

The document files are the truth; the journal only tells which documents changed after a
sequence number. A write journals its sequence before replacing the file, so a crash in
between costs a reader a useless re-read, never a missed change. Every operation holds the
lock, which serialises the stores of one process (thread lock) and of other processes (OS
lock on ``.lock``), so a reader never takes a cursor past a write still in progress.

Sequence numbers can come back: a lost journal starts again from the documents, a folder
restored from a backup goes back in time. So every write also draws a random tag, part of
the document's eTag, and a cursor ``<generation>:<seq>:<tag>`` names the journal and the
write it stopped at: a cursor of another journal, or whose write was replaced by another
one with the same number, reads everything again, and an eTag is never given twice.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from operator import attrgetter
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, BinaryIO, Final, TypeGuard, TypeVar

from projectflow.core.planning.store import (
    StoreChanges,
    StoreConflictError,
    StoredDocument,
    StoreUnavailableError,
)
from projectflow.logging import get_logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

JOURNAL_FILE_NAME: Final = "journal.jsonl"
LOCK_FILE_NAME: Final = ".lock"
DOCUMENT_SUFFIX: Final = ".json"
# The journal is rewritten (last line per document, deletions dropped) beyond this many
# lines and beyond twice the number of documents it lists.
JOURNAL_COMPACTION_LINES: Final = 2000
LOCK_TIMEOUT_SECONDS: Final = 10.0
LOCK_RETRY_SECONDS: Final = 0.02
# Antivirus and sync clients briefly open files on Windows: retry before giving up.
FILE_ATTEMPTS: Final = 5
FILE_RETRY_SECONDS: Final = 0.05
GENERATION_BYTES: Final = 8
TAG_BYTES: Final = 4
_SAFE_NAME: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
_HEX: Final = re.compile(r"[0-9a-f]{1,64}")
# ASCII digits only: str.isdigit() also accepts "²", which int() refuses.
_CURSOR: Final = re.compile(r"([0-9a-f]*):([0-9]+):([0-9a-f]*)")
_ETAG: Final = re.compile(r"([0-9]+)(?:-[0-9a-f]+)?")

CONFLICT_MESSAGE: Final = (
    "Cet element du planning a ete modifie sur un autre poste entre-temps. "
    "Actualisez le planning puis recommencez la modification."
)
MISSING_MESSAGE: Final = "Cet element du planning a ete supprime sur un autre poste entre-temps."
EXISTS_MESSAGE: Final = "Cet element du planning existe deja: il a ete cree sur un autre poste."

_Key = tuple[str, str]
_Position = tuple[str, int, str]
"""A parsed cursor: generation, sequence, tag of the write at that sequence."""
_Result = TypeVar("_Result")

_DIRECTORY_LOCKS: dict[str, threading.Lock] = {}
_DIRECTORY_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class _Entry:
    seq: int
    kind: str
    doc_id: str
    tag: str = ""
    """Random part of the eTag; empty in lines written before tags existed."""
    deleted: bool = False

    @property
    def key(self) -> _Key:
        return (self.kind, self.doc_id)

    @property
    def etag(self) -> str:
        return f"{self.seq}-{self.tag}" if self.tag else str(self.seq)

    def line(self) -> bytes:
        payload: dict[str, Any] = {"seq": self.seq, "kind": self.kind, "doc_id": self.doc_id}
        if self.tag:
            payload["tag"] = self.tag
        if self.deleted:
            payload["deleted"] = True
        return _json_line(payload)


@dataclass(frozen=True, slots=True)
class _Header:
    floor: int
    last: int
    generation: str


@dataclass(frozen=True, slots=True)
class _Journal:
    entries: tuple[_Entry, ...]
    last: int
    """Highest sequence ever given; the next write takes ``last + 1``."""
    floor: int
    """Compaction dropped deletions up to this sequence: older cursors read everything."""
    generation: str
    """Identity of this journal; fresh (not yet written) when ``needs_header``."""
    needs_newline: bool
    """The file ends with a partial line (crash during a write)."""
    needs_header: bool
    """No generation on disk (new store, lost journal): the next write records one."""

    @property
    def cursor(self) -> str:
        generation = "" if self.needs_header else self.generation
        return f"{generation}:{self.last}:{self.tag_at(self.last) or ''}"

    def continues(self, position: _Position) -> bool:
        """Whether a cursor of this journal can be read from incrementally."""
        generation, seq, tag = position
        if generation != ("" if self.needs_header else self.generation):
            return False
        if seq < self.floor or seq > self.last:
            return False
        known = self.tag_at(seq)
        # No line at that sequence: compaction kept a later write of the same document.
        return known is None or known == tag

    def tag_at(self, seq: int) -> str | None:
        return next((entry.tag for entry in self.entries if entry.seq == seq), None)


def has_documents(directory: Path) -> bool:
    """Whether a local planning folder holds at least one document file (no parsing)."""
    try:
        return any(
            entry.is_dir() and any(entry.glob(f"*{DOCUMENT_SUFFIX}"))
            for entry in directory.expanduser().iterdir()
        )
    except OSError:
        return False


class LocalPlanningStore:
    """``PlanningStore`` over a directory; several stores may share one directory.

    File I/O runs in ``asyncio.to_thread``, not ``run_file_io``: that lock is shared with
    project creation, which can hold it for minutes. A document file whose content cannot
    be parsed counts as missing everywhere (``create`` repairs it); a file that cannot be
    opened (locked, no rights) raises ``StoreUnavailableError`` so that nothing is skipped.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory.expanduser()
        self._journal_path = self._directory / JOURNAL_FILE_NAME
        self._lock_path = self._directory / LOCK_FILE_NAME
        self._thread_lock = _directory_lock(self._directory)
        self._reported_bad_lines = 0

    @property
    def directory(self) -> Path:
        return self._directory

    async def changes(self, cursor: str | None) -> StoreChanges:
        return await asyncio.to_thread(self._changes, cursor)

    async def get(self, kind: str, doc_id: str) -> StoredDocument | None:
        path = self._document_path(kind, doc_id)
        return await asyncio.to_thread(self._get, path, kind, doc_id)

    async def create(
        self,
        kind: str,
        doc_id: str,
        data: Mapping[str, Any],
        *,
        project: str = "",  # noqa: ARG002 - the project number is inside the data
    ) -> StoredDocument:
        path = self._document_path(kind, doc_id)
        copied = _json_copy(data)
        return await asyncio.to_thread(self._create, path, kind, doc_id, copied)

    async def update(
        self,
        kind: str,
        doc_id: str,
        etag: str,
        data: Mapping[str, Any],
        *,
        project: str | None = None,  # noqa: ARG002 - the project number is inside the data
    ) -> str:
        path = self._document_path(kind, doc_id)
        copied = _json_copy(data)
        return await asyncio.to_thread(self._update, path, kind, doc_id, etag, copied)

    async def delete(self, kind: str, doc_id: str, etag: str) -> None:
        path = self._document_path(kind, doc_id)
        await asyncio.to_thread(self._delete, path, kind, doc_id, etag)

    async def aclose(self) -> None:
        """Nothing to release: files are only open during an operation."""

    # ------------------------------------------------------------ blocking ----

    def _changes(self, cursor: str | None) -> StoreChanges:
        with self._locked():
            journal = self._read_journal()
            position = _parse_cursor(cursor)
            if position is None or not journal.continues(position):
                return StoreChanges(
                    documents=self._all_documents(),
                    cursor=journal.cursor,
                    full=True,
                )
            since = position[1]
            latest: dict[_Key, _Entry] = {}
            for entry in journal.entries:
                previous = latest.get(entry.key)
                if entry.seq > since and (previous is None or entry.seq > previous.seq):
                    latest[entry.key] = entry
            documents: list[StoredDocument] = []
            for entry in sorted(latest.values(), key=attrgetter("seq")):
                path = self._document_path(entry.kind, entry.doc_id)
                current = self._read_document(path, entry.kind, entry.doc_id)
                documents.append(
                    current
                    or StoredDocument(
                        kind=entry.kind,
                        doc_id=entry.doc_id,
                        etag=entry.etag,
                        data=None,
                        deleted=True,
                    ),
                )
            return StoreChanges(documents=tuple(documents), cursor=journal.cursor)

    def _get(self, path: Path, kind: str, doc_id: str) -> StoredDocument | None:
        with self._locked():
            return self._read_document(path, kind, doc_id)

    def _create(
        self,
        path: Path,
        kind: str,
        doc_id: str,
        data: dict[str, Any],
    ) -> StoredDocument:
        with self._locked():
            journal = self._read_journal()
            if self._read_document(path, kind, doc_id) is not None:
                raise StoreConflictError(EXISTS_MESSAGE)
            entry = self._append(journal, kind, doc_id)
            self._write_document(path, entry.etag, data)
            self._compact_if_needed(journal, entry)
        return StoredDocument(kind=kind, doc_id=doc_id, etag=entry.etag, data=data)

    def _update(
        self,
        path: Path,
        kind: str,
        doc_id: str,
        etag: str,
        data: dict[str, Any],
    ) -> str:
        with self._locked():
            journal = self._read_journal()
            current = self._read_document(path, kind, doc_id)
            if current is None:
                raise StoreConflictError(MISSING_MESSAGE)
            if current.etag != etag:
                raise StoreConflictError(CONFLICT_MESSAGE)
            entry = self._append(journal, kind, doc_id)
            self._write_document(path, entry.etag, data)
            self._compact_if_needed(journal, entry)
        return entry.etag

    def _delete(self, path: Path, kind: str, doc_id: str, etag: str) -> None:
        with self._locked():
            journal = self._read_journal()
            current = self._read_document(path, kind, doc_id)
            if current is None:
                return
            if current.etag != etag:
                raise StoreConflictError(CONFLICT_MESSAGE)
            entry = self._append(journal, kind, doc_id, deleted=True)
            try:
                _retry_file_operation(lambda: path.unlink(missing_ok=True))
            except OSError as exc:
                raise StoreUnavailableError(self._write_failure()) from exc
            self._compact_if_needed(journal, entry)

    # ------------------------------------------------------------- locking ----

    @contextmanager
    def _locked(self) -> Iterator[None]:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        if not self._thread_lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise StoreUnavailableError(self._lock_failure())
        try:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                lock_file = self._lock_path.open("a+b")
            except OSError as exc:
                raise StoreUnavailableError(self._access_failure()) from exc
            with lock_file:
                self._lock_file(lock_file, deadline)
                try:
                    yield
                finally:
                    # Closing the file releases the lock too, even if unlocking fails.
                    with suppress(OSError):
                        _unlock_file(lock_file)
        finally:
            self._thread_lock.release()

    def _lock_file(self, lock_file: BinaryIO, deadline: float) -> None:
        # Never delete the lock file: a waiting process would lock a file nobody else sees.
        if lock_file.seek(0, os.SEEK_END) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        while True:
            try:
                _try_lock_file(lock_file)
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StoreUnavailableError(self._lock_failure()) from exc
                time.sleep(LOCK_RETRY_SECONDS)
            else:
                return

    # ------------------------------------------------------------- journal ----

    def _read_journal(self) -> _Journal:
        try:
            raw = _retry_file_operation(self._journal_path.read_bytes)
        except FileNotFoundError:
            raw = b""
        except OSError as exc:
            raise StoreUnavailableError(self._access_failure()) from exc
        entries: list[_Entry] = []
        last = floor = 0
        generation = ""
        bad_lines = 0
        for line in raw.splitlines():
            if not line.strip():
                continue
            parsed = _parse_journal_line(line)
            if isinstance(parsed, _Entry):
                entries.append(parsed)
                last = max(last, parsed.seq)
            elif isinstance(parsed, _Header):
                floor = max(floor, parsed.floor)
                last = max(last, parsed.last)
                generation = parsed.generation or generation
            else:
                bad_lines += 1
        if bad_lines != self._reported_bad_lines:
            self._reported_bad_lines = bad_lines
            if bad_lines:
                get_logger(__name__).warning(
                    "planning.local.journal_lines_skipped",
                    directory=str(self._directory),
                    lines=bad_lines,
                )
        if not entries and last == 0:
            # A store without journal (new, or journal lost): carry on above every eTag.
            # Its new generation makes every older cursor read everything.
            last = floor = self._highest_etag()
        return _Journal(
            entries=tuple(entries),
            last=last,
            floor=floor,
            generation=generation or secrets.token_hex(GENERATION_BYTES),
            needs_newline=bool(raw) and not raw.endswith(b"\n"),
            needs_header=not generation,
        )

    def _append(
        self,
        journal: _Journal,
        kind: str,
        doc_id: str,
        *,
        deleted: bool = False,
    ) -> _Entry:
        entry = _Entry(
            seq=journal.last + 1,
            kind=kind,
            doc_id=doc_id,
            tag=secrets.token_hex(TAG_BYTES),
            deleted=deleted,
        )
        content = b"\n" if journal.needs_newline else b""
        if journal.needs_header:
            content += _header_line(journal.generation, journal.floor, journal.last)
        content += entry.line()
        try:
            with self._journal_path.open("ab") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StoreUnavailableError(self._write_failure()) from exc
        return entry

    def _compact_if_needed(self, journal: _Journal, appended: _Entry) -> None:
        latest: dict[_Key, _Entry] = {}
        for entry in (*journal.entries, appended):
            previous = latest.get(entry.key)
            if previous is None or entry.seq > previous.seq:
                latest[entry.key] = entry
        lines = len(journal.entries) + 1
        if lines <= max(JOURNAL_COMPACTION_LINES, 2 * len(latest)):
            return
        kept: list[_Entry] = []
        floor = journal.floor
        for entry in latest.values():
            if self._document_path(entry.kind, entry.doc_id).exists():
                kept.append(
                    _Entry(seq=entry.seq, kind=entry.kind, doc_id=entry.doc_id, tag=entry.tag)
                )
            else:
                floor = max(floor, entry.seq)
        kept.sort(key=attrgetter("seq"))
        header = _header_line(journal.generation, floor, max(journal.last, appended.seq))
        try:
            _write_atomically(self._journal_path, b"".join([header, *(e.line() for e in kept)]))
        except OSError as exc:
            # The write itself succeeded; a longer journal only costs reading time.
            get_logger(__name__).warning(
                "planning.local.journal_compaction_failed",
                directory=str(self._directory),
                error=type(exc).__name__,
            )

    # ----------------------------------------------------------- documents ----

    def _document_path(self, kind: str, doc_id: str) -> Path:
        if not _SAFE_NAME.fullmatch(kind):
            raise ValueError(f"Type de document de planning invalide: {kind!r}")
        if not _SAFE_NAME.fullmatch(doc_id):
            raise ValueError(f"Identifiant de document de planning invalide: {doc_id!r}")
        return self._directory / kind / f"{doc_id}{DOCUMENT_SUFFIX}"

    def _read_document(self, path: Path, kind: str, doc_id: str) -> StoredDocument | None:
        """Return the current version; None when missing or unparsable (logged)."""
        try:
            raw = _retry_file_operation(path.read_bytes)
        except FileNotFoundError:
            return None
        except OSError as exc:
            # Skipping it would report it as deleted (full read) or lose the change.
            raise StoreUnavailableError(self._read_failure(path)) from exc
        try:
            payload = json.loads(raw)
        except ValueError:
            self._log_unreadable(path, "json")
            return None
        etag = payload.get("etag") if isinstance(payload, dict) else None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(etag, str) or not etag or not isinstance(data, dict):
            self._log_unreadable(path, "format")
            return None
        return StoredDocument(kind=kind, doc_id=doc_id, etag=etag, data=data)

    def _write_document(self, path: Path, etag: str, data: dict[str, Any]) -> None:
        content = json.dumps({"etag": etag, "data": data}, ensure_ascii=False, indent=2)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_atomically(path, content.encode("utf-8"))
        except OSError as exc:
            raise StoreUnavailableError(self._write_failure()) from exc

    def _all_documents(self) -> tuple[StoredDocument, ...]:
        return tuple(
            document
            for kind, doc_id, path in self._document_files()
            if (document := self._read_document(path, kind, doc_id)) is not None
        )

    def _document_files(self) -> Iterator[tuple[str, str, Path]]:
        try:
            kind_directories = sorted(
                entry
                for entry in self._directory.iterdir()
                if entry.is_dir() and _SAFE_NAME.fullmatch(entry.name)
            )
        except OSError as exc:
            raise StoreUnavailableError(self._access_failure()) from exc
        for kind_directory in kind_directories:
            try:
                files = sorted(kind_directory.glob(f"*{DOCUMENT_SUFFIX}"))
            except OSError as exc:
                raise StoreUnavailableError(self._access_failure()) from exc
            for path in files:
                if _SAFE_NAME.fullmatch(path.stem):
                    yield kind_directory.name, path.stem, path

    def _highest_etag(self) -> int:
        highest = 0
        for kind, doc_id, path in self._document_files():
            document = self._read_document(path, kind, doc_id)
            match = _ETAG.fullmatch(document.etag) if document is not None else None
            if match is not None:
                highest = max(highest, int(match[1]))
        return highest

    # ------------------------------------------------------------ messages ----

    def _log_unreadable(self, path: Path, reason: str) -> None:
        get_logger(__name__).warning(
            "planning.local.document_skipped",
            path=str(path),
            reason=reason,
        )

    def _lock_failure(self) -> str:
        return (
            "Le planning local est occupe par un autre poste ou une autre fenetre "
            f"de ProjectFlow ({self._directory}). Reessayez dans un instant."
        )

    def _access_failure(self) -> str:
        return (
            f"Le dossier du planning local est inaccessible: {self._directory}. "
            "Verifiez qu'il existe et que vous avez les droits de lecture."
        )

    def _read_failure(self, path: Path) -> str:
        return (
            f"Un fichier du planning local est verrouille ou illisible: {path}. "
            "Reessayez dans un instant; si cela dure, verifiez les droits du dossier."
        )

    def _write_failure(self) -> str:
        return (
            f"Impossible d'enregistrer le planning local dans {self._directory}. "
            "Verifiez l'espace disque et les droits d'ecriture du dossier."
        )


def _directory_lock(directory: Path) -> threading.Lock:
    key = os.path.normcase(str(directory.resolve()))
    with _DIRECTORY_LOCKS_GUARD:
        return _DIRECTORY_LOCKS.setdefault(key, threading.Lock())


def _parse_cursor(cursor: str | None) -> _Position | None:
    if cursor is None:
        return None
    match = _CURSOR.fullmatch(cursor.strip())
    return (match[1], int(match[2]), match[3]) if match is not None else None


def _parse_journal_line(line: bytes) -> _Entry | _Header | None:
    """Return a change entry, a header or None (unreadable line)."""
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    seq, kind, doc_id = payload.get("seq"), payload.get("kind"), payload.get("doc_id")
    if (
        _is_sequence(seq)
        and isinstance(kind, str)
        and isinstance(doc_id, str)
        and _SAFE_NAME.fullmatch(kind)
        and _SAFE_NAME.fullmatch(doc_id)
    ):
        return _Entry(
            seq=seq,
            kind=kind,
            doc_id=doc_id,
            tag=_hex_or_empty(payload.get("tag")),
            deleted=payload.get("deleted") is True,
        )
    floor, last = payload.get("floor"), payload.get("last")
    if _is_sequence(floor) and _is_sequence(last):
        return _Header(floor=floor, last=last, generation=_hex_or_empty(payload.get("generation")))
    return None


def _hex_or_empty(value: object) -> str:
    return value if isinstance(value, str) and _HEX.fullmatch(value) else ""


def _is_sequence(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _header_line(generation: str, floor: int, last: int) -> bytes:
    return _json_line({"generation": generation, "floor": floor, "last": last})


def _json_copy(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached copy of ``data`` as it will read back; ValueError if not JSON."""
    try:
        copied = json.loads(json.dumps(dict(data), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Document de planning impossible a enregistrer en JSON.") from exc
    if not isinstance(copied, dict):
        raise ValueError("Document de planning impossible a enregistrer en JSON.")
    return copied


def _json_line(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _retry_file_operation(operation: Callable[[], _Result]) -> _Result:
    for _attempt in range(FILE_ATTEMPTS - 1):
        try:
            return operation()
        except PermissionError:
            time.sleep(FILE_RETRY_SECONDS)
    return operation()


def _write_atomically(path: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.stem}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        source = temporary_path
        _retry_file_operation(lambda: source.replace(path))
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _try_lock_file(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file: BinaryIO) -> None:
    if sys.platform == "win32":
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
