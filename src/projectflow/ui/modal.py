"""Open modal dialogs without blocking a running asyncio task.

A modal dialog (``QMessageBox.critical``, ``QDialog.exec``...) runs a nested Qt
event loop. Opened inside an asyncio task step under qasync, that nested loop
resumes the other tasks while the first one is still executing: asyncio refuses
with ``RuntimeError: Cannot enter into task ...`` and those tasks never finish.
These helpers open the dialog from a plain loop callback instead, outside any
task step, where the nested event loop can resume every task normally.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

Answer = TypeVar("Answer")


def _never() -> bool:
    return False


def show_outside_task(
    show: Callable[[], object],
    *,
    skip: Callable[[], bool] = _never,
) -> None:
    """Open a dialog now, or right after the running task step when called from one.

    ``skip`` is checked when the dialog is about to open: a deferred dialog must
    not appear once its window is closing.
    """

    def run() -> None:
        if not skip():
            show()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        run()
        return
    if asyncio.current_task(loop) is None:
        run()
    else:
        loop.call_soon(run)


async def ask_outside_task(
    ask: Callable[[], Answer],
    *,
    skip: Callable[[], bool] = _never,
) -> Answer:
    """Open a dialog outside the waiting task step and return its answer.

    Exceptions raised by ``ask`` are raised again in the waiting task. When
    ``skip`` is true as the dialog is about to open, nobody can answer it any
    more: the dialog is not shown and the waiting task is cancelled.
    """
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[Answer] = loop.create_future()

    def run() -> None:
        if answer.done():
            return  # The waiting task was cancelled before the dialog opened.
        if skip():
            answer.cancel()
            return
        try:
            result = ask()
        except Exception as exc:  # noqa: BLE001 - raised again in the waiting task.
            if not answer.done():
                answer.set_exception(exc)
            return
        if not answer.done():
            answer.set_result(result)

    loop.call_soon(run)
    return await answer
