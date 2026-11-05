"""Live synchronisation of the planning with the shared store.

``SyncEngine.run`` loops until ``stop``: it sends the outbox, then reads the changes of
the other postes. The cadence follows what the user is looking at (``set_mode``); a local
change is saved to the cache and sent at once, then followed by an extra read one second
later. Errors never leave the loop: they become a ``SyncStatus``, also announced to the
service's subscribers as a ``status`` event when its state or message changes. A write
the server refuses is retried on its own schedule, so the reads keep their cadence, and
given up after ``MAX_WRITE_ATTEMPTS``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from projectflow.core.planning.service import (
    DocumentKey,
    PendingWrite,
    PlanningEvent,
    PlanningService,
)
from projectflow.core.planning.store import (
    PlanningStore,
    StoreConflictError,
    StoreUnavailableError,
)
from projectflow.exceptions import AuthError, GraphError
from projectflow.logging import get_logger

SyncState = Literal["ok", "sending", "offline", "auth_required", "forbidden", "error"]
SyncMode = Literal["visible", "background", "hidden", "idle"]
_Problem = Literal["auth", "forbidden"]

MODE_INTERVALS: Final[Mapping[SyncMode, float]] = {
    "visible": 2.0,
    "background": 10.0,
    "hidden": 60.0,
    "idle": 120.0,
}
DEFAULT_MODE: Final[SyncMode] = "background"
WRITE_FOLLOW_UP_SECONDS: Final = 1.0
OFFLINE_PROBE_SECONDS: Final = 15.0
AUTH_RETRY_SECONDS: Final = 60.0
FORBIDDEN_RETRY_SECONDS: Final = 300.0
BACKOFF_FIRST_SECONDS: Final = 2.0
BACKOFF_MAX_SECONDS: Final = 60.0
STOP_FLUSH_SECONDS: Final = 5.0
MAX_CONFLICT_ATTEMPTS: Final = 3
# Refusals of one document, retried after 2, 4, 8 and 16 s, before its change is given up.
MAX_WRITE_ATTEMPTS: Final = 5
# A poste that keeps editing must not hold one flush forever.
MAX_WRITES_PER_FLUSH: Final = 500
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
READ_ONLY_STATES: Final = frozenset({"offline", "auth_required", "forbidden"})

SENDING_MESSAGE: Final = "Envoi des modifications du planning…"
OFFLINE_MESSAGE: Final = (
    "Planning hors ligne : consultation seule. Nouvel essai toutes les 15 secondes."
)
AUTH_MESSAGE: Final = "Connexion Microsoft requise pour synchroniser le planning."
FORBIDDEN_MESSAGE: Final = (
    "Accès au planning refusé : autorisation de l'administrateur Microsoft requise. "
    "Consultation seule."
)
REJECTED_MESSAGE: Final = "Le serveur refuse une modification du planning"

Sleep = Callable[[float], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class SyncStatus:
    state: SyncState = "ok"
    last_success: float | None = None
    """``monotonic()`` time of the last complete exchange with the store."""
    pending: int = 0
    message: str = ""

    @property
    def read_only(self) -> bool:
        """The UI blocks edits: they could not be sent."""
        return self.state in READ_ONLY_STATES


@dataclass(frozen=True, slots=True)
class _Rejection:
    """A document whose write the server refused: retried at ``retry_at``."""

    revision: int
    attempts: int
    retry_at: float
    reason: str


class _WriteRejectedError(Exception):
    def __init__(self, write: PendingWrite, error: Exception) -> None:
        super().__init__(str(error))
        self.write = write
        self.error = error


class SyncEngine:
    """Runs once: after ``stop`` a new engine is needed."""

    def __init__(
        self,
        service: PlanningService,
        store: PlanningStore,
        *,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        flush_timeout: float = STOP_FLUSH_SECONDS,
    ) -> None:
        self._service = service
        self._store = store
        self._sleep = sleep
        self._monotonic = monotonic
        self._flush_timeout = flush_timeout
        self._mode: SyncMode = DEFAULT_MODE
        self._status = SyncStatus(pending=service.pending)
        self._backoff = 0.0
        self._rejections: dict[DocumentKey, _Rejection] = {}
        self._wake = asyncio.Event()
        self._save_wanted = asyncio.Event()
        self._finished = asyncio.Event()
        self._running = False
        self._stopping = False
        self._unsubscribe = service.subscribe(self._on_event)

    @property
    def status(self) -> SyncStatus:
        return self._status

    @property
    def read_only(self) -> bool:
        return self._status.read_only

    @property
    def mode(self) -> SyncMode:
        return self._mode

    @property
    def running(self) -> bool:
        return self._running

    def set_mode(self, mode: SyncMode) -> None:
        """Cadence: Planning tab shown, app in front, app behind, nobody at the poste."""
        faster = MODE_INTERVALS[mode] < MODE_INTERVALS[self._mode]
        self._mode = mode
        if faster:
            self.wake()

    def wake(self) -> None:
        """Synchronise now (Planning tab shown, « Réessayer », after a new sign-in)."""
        self._wake.set()

    async def run(self) -> None:
        if self._running:
            return
        self._running = True
        self._finished.clear()
        saver = asyncio.ensure_future(self._autosave())
        try:
            while not self._stopping:
                delay = await self.sync_once()
                if not self._stopping:
                    await self._pause(delay)
            await self._final_flush()
        finally:
            saver.cancel()
            await asyncio.gather(saver, return_exceptions=True)
            self._running = False
            self._finished.set()

    async def stop(self) -> None:
        """End ``run`` after sending the outbox, waiting at most ``flush_timeout`` seconds.

        The outbox is saved first, so a store that does not answer loses nothing: what
        is not sent goes out at the next start.
        """
        self._stopping = True
        self._wake.set()
        await self._service.save()
        if not self._running:
            await self._final_flush()
            return
        try:
            async with asyncio.timeout(self._flush_timeout):
                await self._finished.wait()
        except TimeoutError:
            get_logger(__name__).warning(
                "planning.sync.stop_timeout", pending=self._service.pending
            )

    async def sync_once(self) -> float:
        """Send the outbox, read the changes, save the cache; return the seconds to wait."""
        try:
            wrote = await self._flush()
            await self._service.pull()
        except Exception as exc:  # noqa: BLE001 - the loop never stops on an error
            _log_failure("planning.sync.failed", exc)
            delay = self._failed(exc)
        else:
            delay = self._succeeded(wrote=wrote)
        await self._service.save()
        return delay

    # --------------------------------------------------------- writes ----

    async def _flush(self) -> bool:
        """Send every pending write that is due; True when one reached the server.

        A document the server refuses waits for its own retry and does not stop the others.
        """
        wrote = False
        skip: set[DocumentKey] = set()
        for _ in range(MAX_WRITES_PER_FLUSH):
            write = self._service.next_write(skip=skip)
            if write is None:
                break
            if self._waiting(write):
                skip.add(write.key)
                continue
            self._show_sending()
            try:
                settled = await self._send(write)
            except _WriteRejectedError as rejected:
                get_logger(__name__).error(
                    "planning.sync.write_failed",
                    kind=write.kind,
                    doc_id=write.doc_id,
                    exc_info=rejected.error,
                )
                self._reject(rejected.write, rejected.error)
                skip.add(write.key)
                continue
            self._rejections.pop(write.key, None)
            if settled:
                wrote = True
            else:
                skip.add(write.key)
        self._rejections = {
            key: rejection
            for key, rejection in self._rejections.items()
            if self._service.is_pending(*key)
        }
        return wrote

    async def _send(self, write: PendingWrite) -> bool:
        """Write, merging after each conflict; False when it still conflicts."""
        current: PendingWrite | None = write
        try:
            for _ in range(MAX_CONFLICT_ATTEMPTS):
                if current is None:
                    return True
                try:
                    etag = await self._write(current)
                except StoreConflictError:
                    server = await self._store.get(current.kind, current.doc_id)
                    current = self._service.write_conflicted(current, server)
                else:
                    self._service.write_succeeded(current, etag)
                    return True
        except Exception as exc:
            if _is_expected(exc) or current is None:
                raise
            raise _WriteRejectedError(current, exc) from exc
        return current is None

    async def _write(self, write: PendingWrite) -> str | None:
        if write.after is None:
            if write.etag is not None:
                await self._store.delete(write.kind, write.doc_id, write.etag)
            return None
        data = write.after.to_json()
        if write.etag is None:
            stored = await self._store.create(
                write.kind, write.doc_id, data, project=write.project or ""
            )
            return stored.etag
        return await self._store.update(
            write.kind, write.doc_id, write.etag, data, project=write.project
        )

    def _waiting(self, write: PendingWrite) -> bool:
        """Refused before and not due yet; a new change of the document is tried at once."""
        rejection = self._rejections.get(write.key)
        if rejection is None or rejection.revision != write.revision:
            return False
        return self._monotonic() < rejection.retry_at

    def _reject(self, write: PendingWrite, error: Exception) -> None:
        previous = self._rejections.get(write.key)
        attempts = previous.attempts + 1 if previous is not None else 1
        if attempts >= MAX_WRITE_ATTEMPTS:
            self._rejections.pop(write.key, None)
            get_logger(__name__).warning(
                "planning.sync.write_given_up", kind=write.kind, doc_id=write.doc_id
            )
            self._service.write_rejected(write, str(error))
            return
        delay = min(BACKOFF_FIRST_SECONDS * 2 ** (attempts - 1), BACKOFF_MAX_SECONDS)
        self._rejections[write.key] = _Rejection(
            write.revision, attempts, self._monotonic() + delay, str(error)
        )

    async def _final_flush(self) -> None:
        self._unsubscribe()
        try:
            async with asyncio.timeout(self._flush_timeout):
                await self._flush()
        except TimeoutError:
            get_logger(__name__).warning(
                "planning.sync.stop_timeout", pending=self._service.pending
            )
        except Exception as exc:  # noqa: BLE001 - kept in the cache, sent at the next start
            _log_failure("planning.sync.stop_failed", exc, pending=self._service.pending)
        await self._service.save()

    # --------------------------------------------------------- status ----

    def _succeeded(self, *, wrote: bool) -> float:
        self._backoff = 0.0
        now = self._monotonic()
        pending = self._service.pending
        interval = MODE_INTERVALS[self._mode]
        delay = min(interval, WRITE_FOLLOW_UP_SECONDS) if wrote else interval
        # The read may have settled a refused document (deleted elsewhere meanwhile).
        waiting = [
            rejection
            for key, rejection in self._rejections.items()
            if self._service.is_pending(*key)
        ]
        if not waiting:
            self._set_status(SyncStatus("ok", now, pending))
            return delay
        first = min(waiting, key=lambda rejection: rejection.retry_at)
        message = f"{REJECTED_MESSAGE} ({first.reason}) : nouvel essai automatique."
        self._set_status(SyncStatus("error", now, pending, message))
        return max(min(delay, first.retry_at - now), 0.0)

    def _failed(self, error: Exception) -> float:
        problem = _problem(error)
        if problem == "auth":
            state: SyncState = "auth_required"
            message = AUTH_MESSAGE
            delay = AUTH_RETRY_SECONDS
        elif problem == "forbidden":
            state = "forbidden"
            message = FORBIDDEN_MESSAGE
            delay = FORBIDDEN_RETRY_SECONDS
        elif isinstance(error, StoreUnavailableError):
            state = "offline"
            message = OFFLINE_MESSAGE
            delay = OFFLINE_PROBE_SECONDS
        else:
            state = "error"
            delay = self._backoff = min(
                self._backoff * 2 if self._backoff else BACKOFF_FIRST_SECONDS,
                BACKOFF_MAX_SECONDS,
            )
            message = (
                f"Erreur de synchronisation du planning ({error}). "
                f"Nouvel essai dans {delay:.0f} secondes."
            )
        status = SyncStatus(state, self._status.last_success, self._service.pending, message)
        self._set_status(status)
        return delay

    def _show_sending(self) -> None:
        if self._status.state in {"ok", "sending"}:
            pending = self._service.pending
            last_success = self._status.last_success
            self._set_status(SyncStatus("sending", last_success, pending, SENDING_MESSAGE))

    def _set_status(self, status: SyncStatus) -> None:
        previous = self._status
        self._status = status
        if (status.state, status.message) != (previous.state, previous.message):
            self._service.notify(PlanningEvent(frozenset(), "status", status.message))

    # ---------------------------------------------------------- waits ----

    def _on_event(self, event: PlanningEvent) -> None:
        if event.origin == "local":
            self._save_wanted.set()
            self._wake.set()

    async def _autosave(self) -> None:
        """Put each local change on disk at once, even while a cycle waits on the store."""
        while True:
            await self._save_wanted.wait()
            self._save_wanted.clear()
            try:
                await self._service.save()
            except Exception:  # noqa: BLE001 - the next cycle saves again
                get_logger(__name__).exception("planning.sync.autosave_failed")

    async def _pause(self, delay: float) -> None:
        if not self._wake.is_set():
            sleeper = asyncio.ensure_future(self._sleep(delay))
            waker = asyncio.ensure_future(self._wake.wait())
            try:
                await asyncio.wait((sleeper, waker), return_when=asyncio.FIRST_COMPLETED)
            finally:
                sleeper.cancel()
                waker.cancel()
                await asyncio.gather(sleeper, waker, return_exceptions=True)
        self._wake.clear()


def _log_failure(event: str, error: Exception, **fields: object) -> None:
    if _is_expected(error):
        get_logger(__name__).info(event, error=str(error), **fields)
    else:
        get_logger(__name__).error(event, exc_info=error, **fields)


def _is_expected(error: BaseException) -> bool:
    """Network, sign-in and permission problems: reported as a state, not as a failure."""
    return isinstance(error, StoreUnavailableError) or _problem(error) is not None


def _problem(error: BaseException) -> _Problem | None:
    """Find a sign-in or permission problem anywhere in the chain of causes."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AuthError):
            return "auth"
        if isinstance(current, GraphError):
            if current.status_code == HTTP_UNAUTHORIZED:
                return "auth"
            if current.status_code == HTTP_FORBIDDEN:
                return "forbidden"
        current = current.__cause__ or current.__context__
    return None
