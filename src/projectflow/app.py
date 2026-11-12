from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path

import structlog
from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from projectflow import __version__
from projectflow.auth.browser_sign_in import set_sign_in_prompt
from projectflow.config import AppConfig
from projectflow.core.planning.poste_config import PlanningPosteConfig
from projectflow.demo import build_demo_environment
from projectflow.exceptions import ProjectFlowError
from projectflow.logging import configure_logging, get_logger
from projectflow.platform.single_instance import SingleInstanceGuard
from projectflow.reset import reset_local_data
from projectflow.services import ServiceContainer
from projectflow.ui.controller import ProjectFlowController, ServiceProvider
from projectflow.ui.dialogs.microsoft_sign_in import MicrosoftSignInPrompt
from projectflow.ui.main_window import MainWindow
from projectflow.ui.onboarding.wizard import OnboardingWizard
from projectflow.ui.planning_controller import PlanningStoreFactory
from projectflow.ui.theme import apply_theme
from projectflow.ui.tray import ProjectFlowTray
from projectflow.updates import (
    AFTER_UPDATE_ARGUMENT,
    BACKGROUND_ARGUMENT,
    UPDATE_FAILED_ARGUMENT,
    RelaunchMode,
    StagedUpdate,
    begin_staged_install,
    discard_staged_update,
    install_in_progress,
    launch_install_plan,
    load_staged_update,
    silent_updates_supported,
    verify_staged_installer,
)

_OWN_ARGUMENTS = frozenset(
    {"--demo", AFTER_UPDATE_ARGUMENT, BACKGROUND_ARGUMENT, UPDATE_FAILED_ARGUMENT},
)
# The balloon of a tray icon shown a moment ago is dropped by Windows: wait a little.
_UPDATE_NOTICE_DELAY_MS = 1500


def run(argv: Sequence[str]) -> int:
    configure_logging()
    logger = get_logger("projectflow.app")

    qt_argv = [arg for arg in argv if arg not in _OWN_ARGUMENTS]
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

    if _install_staged_update(argv, logger):
        # The installer starts the new version: nothing of this one is shown.
        single_instance.release()
        return 0

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)
    # Browser sign-ins run in worker threads; this shows them in a ProjectFlow window
    # because the browser may open behind other windows or not at all.
    sign_in_prompt = MicrosoftSignInPrompt()
    set_sign_in_prompt(sign_in_prompt)

    smoke_delay_ms = _smoke_exit_delay_ms(logger)
    demo_mode = _demo_mode_enabled(argv)
    services: ServiceProvider
    save_config = None
    planning_store_factory: PlanningStoreFactory
    save_planning_config: Callable[[PlanningPosteConfig], None]
    if demo_mode:
        config, demo_services = build_demo_environment()
        services = demo_services
        planning_store_factory = demo_services.create_planning_store
        save_planning_config = demo_services.save_planning_poste_config
        planning_config = demo_services.planning_poste_config()
        logger.info("app.demo_mode.enabled", root=str(config.paths.racine_projets))
    else:
        config = AppConfig.load()
        container = ServiceContainer(config, interactive_sign_in=False)
        services = container
        planning_store_factory = container.create_planning_store
        save_config = config.save
        save_planning_config = _save_planning_config
        planning_config = PlanningPosteConfig.load()
    apply_theme(app, config.appearance)

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
        container = ServiceContainer(config, interactive_sign_in=False)
        services = container
        planning_store_factory = container.create_planning_store
        save_config = config.save

    window = MainWindow(config)
    controller = ProjectFlowController(
        window=window,
        config=config,
        services=services,
        save_config=save_config,
        planning_config=planning_config,
        planning_store_factory=planning_store_factory,
        save_planning_config=save_planning_config,
        outlook_calendar_factory=None if demo_mode else container.open_outlook_calendar,
    )
    tray = _configure_tray(app, window, controller, app_icon)
    if tray is not None:
        logger.info("app.tray.enabled")
    single_instance.activation_requested.connect(lambda _command: controller.show_window())
    _show_at_start(window, controller, argv, has_tray=tray is not None)
    logger.info("app.started", version=__version__)
    # After the first paint: the planning reads its cache and its store in the background.
    QTimer.singleShot(0, controller.start_planning)
    if not demo_mode:
        # Know at once whether Microsoft works, without opening any browser.
        QTimer.singleShot(0, controller.request_connection_check)
        QTimer.singleShot(0, controller.start_update_checks)
    _schedule_smoke_exit(app, logger, smoke_delay_ms)

    with event_loop:
        try:
            result = event_loop.run_forever()
        finally:
            try:
                event_loop.run_until_complete(controller.aclose())
            finally:
                single_instance.release()
    _after_exit(controller, logger)
    return result if isinstance(result, int) else 0


def _show_at_start(
    window: MainWindow,
    controller: ProjectFlowController,
    argv: Sequence[str],
    *,
    has_tray: bool,
) -> None:
    # An update installed while the window was hidden comes back hidden too.
    if BACKGROUND_ARGUMENT not in argv or not has_tray:
        window.show()
    if AFTER_UPDATE_ARGUMENT in argv:
        QTimer.singleShot(_UPDATE_NOTICE_DELAY_MS, controller.announce_update_installed)


def _after_exit(controller: ProjectFlowController, logger: structlog.stdlib.BoundLogger) -> None:
    """Do what must wait until everything is saved and closed: the reset, or the update."""
    if controller.reset_requested:
        # Erased only now: nothing running can write the settings or tokens back.
        failures = reset_local_data()
        logger.info("app.reset.finished", failures=failures)
        _restart_application()
        return
    install_plan = controller.install_plan
    if install_plan is None:
        return
    try:
        launch_install_plan(install_plan)
    except OSError:
        logger.exception("app.update.launch_failed")
    else:
        logger.info("app.update.installing")


def _install_staged_update(argv: Sequence[str], logger: structlog.stdlib.BoundLogger) -> bool:
    """Install the release downloaded by an earlier session; True when the app must exit.

    The installer runs without a window and starts the new version itself.
    """
    if _demo_mode_enabled(argv) or not silent_updates_supported():
        return False
    # Read even right after an update: this deletes the installer that has just run.
    staged = load_staged_update(current_version=__version__)
    # Started again by the macOS script that just failed (its log says why): the running
    # app tries once more later, then asks.
    if staged is None or AFTER_UPDATE_ARGUMENT in argv or UPDATE_FAILED_ARGUMENT in argv:
        return False
    if install_in_progress(staged):
        logger.info("app.update.already_installing", version=staged.version)
        return True
    # Failed twice without a window: the app starts and offers it with a question.
    if staged.exhausted or not _staged_installer_intact(staged, logger):
        return False
    relaunch: RelaunchMode = "background" if BACKGROUND_ARGUMENT in argv else "window"
    try:
        launch_install_plan(begin_staged_install(staged, relaunch=relaunch))
    except (ProjectFlowError, OSError):
        logger.exception("app.update.launch_failed", version=staged.version)
        return False
    logger.info("app.update.installing", version=staged.version)
    return True


def _staged_installer_intact(staged: StagedUpdate, logger: structlog.stdlib.BoundLogger) -> bool:
    if verify_staged_installer(staged):
        return True
    # Downloaded again by the next check.
    logger.warning("app.update.staged_invalid", version=staged.version)
    discard_staged_update()
    return False


def _restart_application() -> None:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command += ["-m", "projectflow"]
    # A one-file build must unpack itself again instead of reusing this process's files.
    environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(command, env=environment, creationflags=creationflags, close_fds=True)


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
    tray.open_repertoire_requested.connect(controller.open_repertoire)
    tray.open_planning_requested.connect(controller.show_planning)
    tray.update_check_requested.connect(controller.request_update_check)
    tray.reset_requested.connect(controller.request_reset)
    tray.quit_requested.connect(lambda: _quit_from_tray(app, window))
    controller.set_notifier(tray.show_message)
    window.hidden_to_background.connect(
        lambda: tray.show_message(
            "ProjectFlow reste actif",
            "Utilisez l'icone ProjectFlow pour rouvrir l'application.",
        ),
    )
    tray.show()
    return tray


def _save_planning_config(poste_config: PlanningPosteConfig) -> None:
    poste_config.save()


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
