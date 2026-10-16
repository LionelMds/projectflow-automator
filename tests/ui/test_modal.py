from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QEventLoop as NestedEventLoop
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from qasync import QEventLoop

from projectflow.config import AppConfig
from projectflow.core.fiche_service import FicheService
from projectflow.core.numero import parse_project_number
from projectflow.core.repertoire_service import (
    NextAvailableProject,
    RepertoireRow,
    RepertoireSnapshot,
)
from projectflow.exceptions import AuthError
from projectflow.ui.controller import ProjectFlowController
from projectflow.ui.main_window import MainWindow
from projectflow.ui.modal import ask_outside_task, show_outside_task

MODAL_OPEN_MS = 300
# qasync 0.28 still calls asyncio.iscoroutinefunction, deprecated since Python 3.14.
pytestmark = pytest.mark.filterwarnings(
    "ignore:'asyncio.iscoroutinefunction' is deprecated:DeprecationWarning",
)


def _run_modal_loop() -> None:
    """Spin a nested Qt event loop, as a real modal dialog does until it is closed."""
    nested = NestedEventLoop()
    timer = QTimer(nested)
    timer.setSingleShot(True)
    timer.timeout.connect(nested.quit)
    timer.start(MODAL_OPEN_MS)
    nested.exec()


@pytest.fixture
def qasync_loop(qapp: Any) -> Iterator[QEventLoop]:
    """Run asyncio callbacks from Qt events, like the application's event loop."""
    loop = QEventLoop(qapp)
    yield loop
    loop.close()


class SignInFailedRepertoire:
    """2025 fails like a refused Microsoft sign-in; 2026 waits until released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def read_snapshot(self, *, year: int) -> RepertoireSnapshot:
        if year == 2025:
            raise AuthError("Connexion Microsoft refusee.")
        await self.release.wait()
        return RepertoireSnapshot(
            year=year,
            rows=(RepertoireRow(row_index=0, values=(f"{year}-4994", "", "Soc", "", "Projet")),),
            next_available=NextAvailableProject(
                number=parse_project_number(f"{year}-4995"),
                row_index=1,
            ),
        )


class Services:
    def __init__(self, repertoire: SignInFailedRepertoire) -> None:
        self.repertoire_service = repertoire

    def fiche(self) -> FicheService:
        return FicheService()

    def repertoire(self) -> SignInFailedRepertoire:
        return self.repertoire_service

    def project(self) -> object:
        return object()


def test_error_box_from_a_task_lets_the_pending_tasks_finish(
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    qasync_loop: QEventLoop,
) -> None:
    config = AppConfig()
    window = MainWindow(config)
    qtbot.addWidget(window)
    window.creation_tab.set_project_identity(year="2025", project_id="4994")
    window.repertoire_tab.set_year(2026)
    repertoire = SignInFailedRepertoire()
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=Services(repertoire),  # type: ignore[arg-type]
    )
    boxes: list[tuple[str, str]] = []

    def critical(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        boxes.append((text, window.creation_tab.logs.toPlainText()))
        # The pending repertoire read completes while the box is open.
        repertoire.release.set()
        _run_modal_loop()
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", critical)

    async def scenario() -> None:
        controller._schedule_task(controller.load_repertoire())  # noqa: SLF001
        controller._schedule_task(controller.next_available())  # noqa: SLF001
        tasks = set(controller._background_tasks)  # noqa: SLF001
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)

    with caplog.at_level(logging.ERROR):
        qasync_loop.run_until_complete(scenario())

    assert [text for text, _journal in boxes] == ["Connexion Microsoft refusee."]
    # The journal entry was written before the box opened.
    assert "! Connexion Microsoft refusee." in boxes[0][1]
    assert [row.number for row in window.repertoire_tab.original_rows()] == ["2026-4994"]
    assert not controller._background_tasks  # noqa: SLF001
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_question_awaited_by_a_task_lets_the_pending_tasks_finish(
    caplog: pytest.LogCaptureFixture,
    qasync_loop: QEventLoop,
) -> None:
    async def scenario() -> list[object]:
        release = asyncio.Event()

        async def pending() -> str:
            await release.wait()
            return "lecture terminee"

        def question() -> QMessageBox.StandardButton:
            release.set()
            _run_modal_loop()
            return QMessageBox.StandardButton.Yes

        other = asyncio.create_task(pending())
        await asyncio.sleep(0)
        return await asyncio.wait_for(
            asyncio.gather(ask_outside_task(question), other),
            timeout=3,
        )

    with caplog.at_level(logging.ERROR):
        answers = qasync_loop.run_until_complete(scenario())

    assert answers == [QMessageBox.StandardButton.Yes, "lecture terminee"]
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_dialog_outside_a_task_opens_at_once() -> None:
    shown: list[str] = []

    show_outside_task(lambda: shown.append("now"))
    show_outside_task(lambda: shown.append("skipped"), skip=lambda: True)

    assert shown == ["now"]


@pytest.mark.asyncio
async def test_dialog_inside_a_task_opens_after_the_task_step() -> None:
    shown: list[str] = []
    closing = False

    show_outside_task(lambda: shown.append("later"))
    show_outside_task(lambda: shown.append("closing"), skip=lambda: closing)
    closing = True
    assert shown == []
    await asyncio.sleep(0)

    assert shown == ["later"]


@pytest.mark.asyncio
async def test_awaited_dialog_returns_its_answer_or_error() -> None:
    def refuse() -> str:
        raise ValueError("Selection de fiche annulee.")

    assert await ask_outside_task(lambda: "oui") == "oui"
    with pytest.raises(ValueError, match="annulee"):
        await ask_outside_task(refuse)
    with pytest.raises(asyncio.CancelledError):
        await ask_outside_task(lambda: "jamais", skip=lambda: True)
