from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence

import structlog
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from projectflow.auth.msal_client import MsalAuthClient
from projectflow.auth.onboarding_service import MicrosoftOnboardingService
from projectflow.config import AppConfig
from projectflow.demo import build_demo_environment
from projectflow.exceptions import AuthError
from projectflow.logging import configure_logging, get_logger
from projectflow.services import ServiceContainer
from projectflow.ui.controller import ProjectFlowController, ServiceProvider
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard


def run(argv: Sequence[str]) -> int:
    configure_logging()
    logger = get_logger("projectflow.app")

    qt_argv = [arg for arg in argv if arg != "--demo"]
    app = QApplication(qt_argv)
    app.setApplicationName("ProjectFlow Automator")
    app.setOrganizationName("Balz Metal Sa")

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
        onboarding_service = (
            None if smoke_delay_ms is not None else _build_onboarding_service(logger)
        )
        wizard = OnboardingWizard(config, microsoft_service=onboarding_service)
        if smoke_delay_ms is not None:
            logger.info("app.smoke_exit.onboarding_scheduled", delay_ms=smoke_delay_ms)
            QTimer.singleShot(smoke_delay_ms, wizard.reject)
        if wizard.exec() != wizard.DialogCode.Accepted:
            logger.info("app.onboarding.cancelled")
            return 0
        if wizard.demo_requested:
            config, services = build_demo_environment()
            save_config = None
            logger.info(
                "app.demo_mode.enabled_from_onboarding",
                root=str(config.paths.racine_projets),
            )
        else:
            config = wizard.config
            config.save()
        if onboarding_service is not None:
            onboarding_service.close()

    window = MainWindow(config)
    ProjectFlowController(
        window=window,
        config=config,
        services=services,
        save_config=save_config,
    )
    window.show()
    logger.info("app.started")
    _schedule_smoke_exit(app, logger, smoke_delay_ms)

    with event_loop:
        result = event_loop.run_forever()
        return result if isinstance(result, int) else 0


def _build_onboarding_service(
    logger: structlog.stdlib.BoundLogger,
) -> MicrosoftOnboardingService | None:
    try:
        return MicrosoftOnboardingService(MsalAuthClient())
    except AuthError as exc:
        logger.warning("app.onboarding.auth_unavailable", error=str(exc))
        return None


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
