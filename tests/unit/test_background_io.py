from __future__ import annotations

import asyncio
from threading import Event

import pytest

from projectflow.core.background_io import file_io_lock, run_file_io


@pytest.mark.asyncio
async def test_file_io_forwards_arguments_without_blocking_event_loop() -> None:
    loop = asyncio.get_running_loop()
    heartbeat = Event()

    def operation(value: str, *, suffix: str) -> str:
        loop.call_soon_threadsafe(heartbeat.set)
        assert heartbeat.wait(timeout=2)
        return value + suffix

    assert await run_file_io(operation, "file", suffix=".xlsx") == "file.xlsx"


@pytest.mark.parametrize("worker_fails", [False, True])
@pytest.mark.asyncio
async def test_cancellation_drains_worker_before_releasing_file_gate(*, worker_fails: bool) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = Event()
    effects: list[str] = []

    def first_operation() -> None:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5)
        effects.append("first finished")
        if worker_fails:
            raise OSError("Disk unavailable")

    def second_operation() -> None:
        effects.append("second finished")

    first = asyncio.create_task(run_file_io(first_operation))
    second: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        first.cancel()
        await asyncio.sleep(0)
        second = asyncio.create_task(run_file_io(second_operation))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)

        assert not first.done()
        assert not second.done()
        assert effects == []

        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await first
        if worker_fails:
            assert isinstance(error.value.__cause__, OSError)
        await second
        assert effects == ["first finished", "second finished"]
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True
        )


@pytest.mark.asyncio
async def test_cancelling_queued_file_operation_prevents_it_from_starting() -> None:
    effects: list[str] = []
    async with file_io_lock():
        pending = asyncio.create_task(run_file_io(effects.append, "should not run"))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    await run_file_io(effects.append, "next operation")
    assert effects == ["next operation"]


@pytest.mark.asyncio
async def test_worker_failure_reaches_caller_and_releases_file_gate() -> None:
    failure = OSError("File is locked")

    def failing_operation() -> None:
        raise failure

    with pytest.raises(OSError, match="File is locked") as error:
        await run_file_io(failing_operation)
    assert error.value is failure
    assert await run_file_io(lambda: "next operation") == "next operation"
