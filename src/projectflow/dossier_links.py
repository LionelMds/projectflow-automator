"""Follow a « projectflow://dossier/2024-4493 » link: open that folder on this poste.

Windows starts the application with the link as its argument (see the installer): the
folder opens and the process ends, without a window nor a hand-off to a running
ProjectFlow. macOS hands the link to the running application as an event instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFileOpenEvent

from projectflow.config import AppConfig
from projectflow.core.dossier_link import LINK_SCHEME, dossier_dir, parse_dossier_link
from projectflow.exceptions import ProjectFlowError
from projectflow.logging import configure_logging, get_logger
from projectflow.platform.filemanager import open_path


def open_dossier_link(
    link: str,
    root: Path | None,
    *,
    open_folder: Callable[[Path], None] = open_path,
) -> Path:
    """Open the folder a link names under ``root`` and return it."""
    folder = dossier_dir(root, parse_dossier_link(link))
    open_folder(folder)
    return folder


def run_dossier_link(
    link: str,
    *,
    load_config: Callable[[], AppConfig] = AppConfig.load,
    open_folder: Callable[[Path], None] = open_path,
    show_error: Callable[[str], None] | None = None,
) -> int:
    """Serve a link received on the command line; a failure is told in a message box."""
    configure_logging()
    logger = get_logger("projectflow.dossier_links")
    try:
        folder = open_dossier_link(
            link,
            load_config().paths.racine_projets,
            open_folder=open_folder,
        )
    except (ProjectFlowError, OSError) as exc:
        logger.warning("dossier_link.failed", error=type(exc).__name__)
        (show_error or _show_error)(str(exc))
        return 1
    logger.info("dossier_link.opened", folder=folder.name)
    return 0


class DossierLinkEvents(QObject):
    """Receive the links macOS sends to the application, from its very first event.

    Links that arrive before anything can open them (the onboarding is still shown)
    wait for ``deliver_to``.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._handler: Callable[[str], None] | None = None
        self._waiting: list[str] = []

    def deliver_to(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        waiting, self._waiting = self._waiting, []
        for link in waiting:
            handler(link)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt name
        if event.type() != QEvent.Type.FileOpen:
            return super().eventFilter(watched, event)
        url = cast("QFileOpenEvent", event).url()
        if url.scheme().lower() != LINK_SCHEME:
            return super().eventFilter(watched, event)
        self.receive(url.toString())
        return True

    def receive(self, link: str) -> None:
        if self._handler is None:
            self._waiting.append(link)
        else:
            self._handler(link)


def _show_error(message: str) -> None:
    # The packaged application has no console: a window is the only way to tell.
    from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: PLC0415 - failure only

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ProjectFlow Automator")
    # A box of our own: with no window and no event loop yet, the static
    # QMessageBox.warning() crashed the process (PySide 6.11, Windows).
    box = QMessageBox(QMessageBox.Icon.Warning, "ProjectFlow", message)
    box.exec()
