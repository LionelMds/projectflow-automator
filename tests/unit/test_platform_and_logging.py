from __future__ import annotations

from pathlib import Path

from projectflow.logging import configure_logging, get_logger
from projectflow.platform.paths import expand_user_path


def test_configure_logging_creates_log_file(tmp_path: Path) -> None:
    configure_logging(log_dir=tmp_path)
    logger = get_logger("test")

    logger.info("test.event")

    assert (tmp_path / "projectflow.jsonl").exists()


def test_expand_user_path_expands_home() -> None:
    assert str(expand_user_path("~/ProjectFlow")).startswith(str(Path.home()))
