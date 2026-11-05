"""Local copy of the shared planning, for an instant start and reading without network.

One JSON file holds every document with its eTag, the change cursor and the writes not
sent yet, so that a change made just before ProjectFlow closes goes out at the next start.
The file is replaced atomically; an unreadable file only costs a full reload.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from pydantic import TypeAdapter, ValidationError

from projectflow.logging import get_logger

CACHE_FILE_NAME: Final = "planning-cache.json"
CACHE_FORMAT_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class CachedDocument:
    etag: str
    """Empty while the server has not stored the document yet."""
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CachedWrite:
    """One pending write: ``before`` is the server version it is based on."""

    kind: str
    doc_id: str
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    etag: str = ""
    """eTag of ``before``: a purged document is no longer among the cached documents."""


@dataclass(frozen=True, slots=True)
class CacheContent:
    documents: Mapping[str, Mapping[str, CachedDocument]] = field(default_factory=dict)
    """``{kind: {doc_id: CachedDocument}}``."""
    cursor: str | None = None
    outbox: tuple[CachedWrite, ...] = ()
    version: int = CACHE_FORMAT_VERSION


_CONTENT_ADAPTER: Final = TypeAdapter(CacheContent)


class PlanningCache:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._write_lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._directory / CACHE_FILE_NAME

    async def load(self) -> CacheContent:
        """Read the saved content; empty when the file is missing, unreadable or outdated."""
        return await asyncio.to_thread(self._read)

    async def save(self, content: CacheContent) -> None:
        await asyncio.to_thread(self._write, content)

    async def clear(self) -> None:
        await asyncio.to_thread(self._remove)

    def _read(self) -> CacheContent:
        try:
            payload = self.path.read_bytes()
        except FileNotFoundError:
            return CacheContent()
        except OSError as exc:
            get_logger(__name__).warning("planning.cache.unreadable", error=str(exc))
            return CacheContent()
        try:
            content = _CONTENT_ADAPTER.validate_json(payload)
        except ValidationError as exc:
            get_logger(__name__).warning("planning.cache.corrupt", errors=exc.error_count())
            return CacheContent()
        if content.version != CACHE_FORMAT_VERSION:
            get_logger(__name__).warning("planning.cache.other_version", version=content.version)
            return CacheContent()
        return content

    def _write(self, content: CacheContent) -> None:
        # Saves leave the loop one by one, but a cancelled save keeps running in its thread.
        with self._write_lock:
            payload = _CONTENT_ADAPTER.dump_json(content)
            self._directory.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(
                prefix=".planning-cache-", suffix=".tmp", dir=self._directory
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def _remove(self) -> None:
        with self._write_lock:
            self.path.unlink(missing_ok=True)
