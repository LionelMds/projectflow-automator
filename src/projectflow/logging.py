from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

import structlog

from projectflow.platform.paths import logs_dir

_CLOUD_LINK = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:sharepoint\.com|1drv\.ms|onedrive\.live\.com)"
    r"(?::[0-9]+)?(?:[/?#][^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
_SHARING_TOKEN = re.compile(r"(?<![\w-])u(?:!|%21)[a-z0-9_-]+", re.IGNORECASE)


def redact_sensitive_links(text: str) -> str:
    # Graph /shares/u!... embeds the complete link in reversible base64url.
    redacted = _CLOUD_LINK.sub("[lien Microsoft masque]", text)
    return _SHARING_TOKEN.sub("[lien de partage masque]", redacted)


class _SensitiveLinkFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Interpolate first so nested JSON and third-party log arguments are
        # covered. Keep the record safe even if a handler later fails to emit it.
        record.msg = redact_sensitive_links(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = redact_sensitive_links(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_sensitive_links(record.stack_info)
        return True


def configure_logging(*, debug: bool = False, log_dir: Path | None = None) -> None:
    resolved_log_dir = log_dir or logs_dir()
    resolved_log_dir.mkdir(parents=True, exist_ok=True)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        RotatingFileHandler(
            resolved_log_dir / "projectflow.jsonl",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        ),
    ]
    for handler in handlers:
        handler.addFilter(_SensitiveLinkFilter())
        handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        handlers=handlers,
    )
    # Request/debug logs may contain sharing URLs or response headers. Keep
    # warnings visible, protected by the same filter as application events.
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))
