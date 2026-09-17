from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import ParamSpec, TypeVar
from weakref import WeakValueDictionary

_PARAMETERS = ParamSpec("_PARAMETERS")
_RESULT = TypeVar("_RESULT")
_FILE_IO_LOCKS: WeakValueDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakValueDictionary()


def file_io_lock() -> asyncio.Lock:
    """Serialize local file operations across services sharing an event loop."""
    loop = asyncio.get_running_loop()
    lock = _FILE_IO_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _FILE_IO_LOCKS[loop] = lock
    return lock


async def run_file_io(
    operation: Callable[_PARAMETERS, _RESULT],
    /,
    *args: _PARAMETERS.args,
    **kwargs: _PARAMETERS.kwargs,
) -> _RESULT:
    """Run a file operation off the event loop, draining writes before cancellation."""
    async with file_io_lock():
        worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                # A running thread cannot be stopped. Keep the file gate closed
                # until its operation ends, including after repeated cancellation.
                cancelled = cancelled or exc
            except Exception:
                if cancelled is None:
                    raise
        if cancelled is not None:
            try:
                worker.result()
            except Exception as exc:
                raise cancelled from exc
            raise cancelled
        return worker.result()
