from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

import structlog
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from projectflow.config import AppConfig
from projectflow.demo import build_demo_environment
from projectflow.logging import configure_logging, get_logger
from projectflow.platform.single_instance import SingleInstanceGuard
from projectflow.services import ServiceContainer
from projectflow.ui.controller import ProjectFlowController, ServiceProvider
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard
from projectflow.ui.tray import ProjectFlowTray


def run(argv: Sequence[str]) -> int:
    configure_logging()
    logger = get_logger("projectflow.app")

    qt_argv = [arg for arg in argv if arg != "--demo"]
    app = QApplication(qt_argv)
    app.setApplicationName("ProjectFlow Automator")
    app.setOrganizationName("Balz Metal Sa")
    app_icon = _application_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)

    single_instance = SingleInstanceGuard(parent=app)
    if not single_instance.listen():
        logger.info("app.single_instance.forwarded")
        return 0

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    smoke_delay_ms = _smoke_exit_delay_ms(logger)
    demo_mode = _demo_mode_enabled(argv)
    services: ServiceProvider
    save_config = None
    if demo_mode:
        config, services = build_demo_environment()
        logger.info("app.demo_mode.enabled", root=str(config.paths.racine_projets))
    else:
        config = AppConfig.load()
        services = ServiceContainer(config)
        save_config = config.save

    if not config.is_onboarded:
        wizard = OnboardingWizard(config)
        if smoke_delay_ms is not None:
            logger.info("app.smoke_exit.onboarding_scheduled", delay_ms=smoke_delay_ms)
            QTimer.singleShot(smoke_delay_ms, wizard.reject)
        if wizard.exec() != wizard.DialogCode.Accepted:
            logger.info("app.onboarding.cancelled")
            single_instance.release()
            return 0
        config = wizard.config
        config.save()
        services = ServiceContainer(config)
        save_config = config.save

    window = MainWindow(config)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,
        save_config=save_config,
    )
    tray = _configure_tray(app, window, controller, app_icon)
    if tray is not None:
        logger.info("app.tray.enabled")
    single_instance.activation_requested.connect(lambda _command: controller.show_window())
    window.show()
    logger.info("app.started")
    if not demo_mode:
        QTimer.singleShot(
            0,
            lambda: controller.request_update_check(show_no_update=False),
        )
    _schedule_smoke_exit(app, logger, smoke_delay_ms)

    with event_loop:
        try:
            result = event_loop.run_forever()
            return result if isinstance(result, int) else 0
        finally:
            try:
                event_loop.run_until_complete(controller.aclose())
            finally:
                single_instance.release()


def _configure_tray(
    app: QApplication,
    window: MainWindow,
    controller: ProjectFlowController,
    icon: QIcon | None,
) -> ProjectFlowTray | None:
    if icon is None:
        return None
    tray = ProjectFlowTray(icon=icon, parent=app)
    if not tray.is_available:
        return None

    app.setQuitOnLastWindowClosed(False)
    window.set_background_mode_enabled(enabled=True)
    tray.show_requested.connect(controller.show_window)
    tray.quick_create_requested.connect(controller.show_quick_create)
    tray.open_repertoire_requested.connect(controller.open_repertoire)
    tray.update_check_requested.connect(controller.request_update_check)
    tray.quit_requested.connect(lambda: _quit_from_tray(app, window))
    window.hidden_to_background.connect(
        lambda: tray.show_message(
            "ProjectFlow reste actif",
            "Utilisez l'icone ProjectFlow pour rouvrir l'application.",
        ),
    )
    tray.show()
    return tray


def _quit_from_tray(app: QApplication, window: MainWindow) -> None:
    window.request_quit()
    app.quit()


def _schedule_smoke_exit(
    app: QApplication,
    logger: structlog.stdlib.BoundLogger,
    delay_ms: int | None,
) -> None:
    if delay_ms is None:
        return
    logger.info("app.smoke_exit.scheduled", delay_ms=delay_ms)
    QTimer.singleShot(delay_ms, app.quit)


def _smoke_exit_delay_ms(logger: structlog.stdlib.BoundLogger) -> int | None:
    raw_delay = os.environ.get("PROJECTFLOW_SMOKE_EXIT_MS")
    if raw_delay is None:
        return None
    try:
        return max(0, int(raw_delay))
    except ValueError:
        logger.warning("app.smoke_exit.invalid_delay", value=raw_delay)
        return None


def _demo_mode_enabled(argv: Sequence[str]) -> bool:
    return "--demo" in argv or os.environ.get("PROJECTFLOW_DEMO_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _application_icon() -> QIcon | None:
    icon_path = _application_icon_path()
    if icon_path is None:
        return None
    icon = QIcon(str(icon_path))
    if not icon.isNull():
        return icon
    return None


def _application_icon_path() -> Path | None:
    try:
        icon = resources.files("projectflow.ui.resources").joinpath("icon.png")
    except ModuleNotFoundError:
        return None
    if not icon.is_file():
        return None
    return Path(str(icon))
