from __future__ import annotations

import base64
import io
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import structlog

from projectflow.logging import configure_logging, get_logger


@pytest.fixture
def isolated_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[io.StringIO]:
    root = logging.getLogger()
    previous_config = structlog.get_config().copy()
    monkeypatch.setattr(root, "handlers", [])
    console = io.StringIO()
    monkeypatch.setattr(sys, "stderr", console)
    previous_levels = {
        logger: logger.level
        for logger in (root, logging.getLogger("httpx"), logging.getLogger("httpcore"))
    }
    try:
        configure_logging(log_dir=tmp_path, debug=True)
        yield console
    finally:
        for handler in root.handlers:
            handler.close()
        for logger, level in previous_levels.items():
            logger.setLevel(level)
        structlog.configure(**previous_config)


@pytest.mark.parametrize(
    "sharing_url",
    [
        "https://company.sharepoint.com/:x:/s/site/private-sharing-key?e=secret",
        "https://1drv.ms/x/s/private-sharing-key?e=secret",
        "https://onedrive.live.com/redir?resid=private&authkey=secret",
    ],
)
def test_logs_hide_sharing_links_in_events_and_exceptions(
    tmp_path: Path,
    isolated_logging: io.StringIO,
    sharing_url: str,
) -> None:
    sharing_token = "u!" + base64.urlsafe_b64encode(sharing_url.encode()).decode().rstrip("=")
    graph_url = f"https://graph.microsoft.com/v1.0/shares/{sharing_token}/driveItem"
    get_logger("test.privacy").info(
        "repertoire.backend",
        backend="cloud",
        path=sharing_url,
        nested={"request": graph_url},
    )
    standard_logger = logging.getLogger("test.privacy.standard")
    standard_logger.warning("Graph returned: %s", graph_url.replace("u!", "u%21"))
    try:
        raise RuntimeError(f"Microsoft refused {sharing_url}; request {sharing_token}")  # noqa: TRY301
    except RuntimeError:
        standard_logger.exception("Cannot open repertoire")

    file_output = (tmp_path / "projectflow.jsonl").read_text(encoding="utf-8")
    console_output = isolated_logging.getvalue()
    for output in (file_output, console_output):
        assert sharing_url not in output
        assert "private-sharing-key" not in output
        assert sharing_token not in output
        assert sharing_token.removeprefix("u!") not in output
        assert "[lien Microsoft masque]" in output
        assert "[lien de partage masque]" in output
        assert "RuntimeError" in output
        assert "Cannot open repertoire" in output
    event = json.loads(file_output.splitlines()[0])
    assert event["event"] == "repertoire.backend"
    assert event["backend"] == "cloud"


@pytest.mark.usefixtures("isolated_logging")
def test_http_request_details_are_quiet_even_when_app_debug_is_enabled(
    tmp_path: Path,
) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True}))
    with httpx.Client(transport=transport) as client:
        response = client.get("https://graph.microsoft.com/v1.0/shares/u!private/driveItem")
    assert response.status_code == 200
    logging.getLogger("httpcore").debug("response headers include sensitive data")
    logging.getLogger("httpx").warning("Request failed: status 503")
    get_logger("test.privacy.debug").debug("app.debug.visible")

    output = (tmp_path / "projectflow.jsonl").read_text(encoding="utf-8")
    assert "HTTP Request" not in output
    assert "u!private" not in output
    assert "sensitive data" not in output
    assert "Request failed: status 503" in output
    assert "app.debug.visible" in output
