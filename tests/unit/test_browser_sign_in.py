from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Self

import pytest

from projectflow.auth import browser_sign_in
from projectflow.auth.browser_sign_in import run_browser_sign_in, set_sign_in_prompt
from projectflow.exceptions import AuthError


class FakeReceiver:
    def __init__(self, response: dict[str, object] | None) -> None:
        self.response = response
        self.closed = False
        self.wait_options: dict[str, object] = {}
        self.on_wait: Callable[[], None] = lambda: None

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    def get_port(self) -> int:
        return 50123

    def get_auth_response(self, **kwargs: object) -> dict[str, object] | None:
        self.wait_options = kwargs
        self.on_wait()
        return self.response


class FakeApp:
    def __init__(self) -> None:
        self.flow_options: dict[str, object] = {}
        self.exchanged: list[dict[str, object]] = []

    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: object) -> dict[str, object]:
        self.flow_options = {"scopes": scopes, **kwargs}
        return {"auth_uri": "https://login.example/authorize", "state": "abc"}

    def acquire_token_by_auth_code_flow(
        self,
        auth_code_flow: dict[str, object],
        auth_response: dict[str, object],
    ) -> object:
        self.exchanged.append(auth_response)
        return {"access_token": "token"}


class RecordingPrompt:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.cancel: Callable[[], None] | None = None

    def sign_in_started(self, sign_in_url: str, cancel: Callable[[], None]) -> None:
        self.events.append(("started", sign_in_url))
        self.cancel = cancel

    def sign_in_finished(self, sign_in_url: str) -> None:
        self.events.append(("finished", sign_in_url))


@pytest.fixture
def prompt() -> RecordingPrompt:
    recorder = RecordingPrompt()
    set_sign_in_prompt(recorder)
    yield recorder  # type: ignore[misc]
    set_sign_in_prompt(None)


def test_sign_in_opens_page_shows_prompt_and_exchanges_code(prompt: RecordingPrompt) -> None:
    receiver = FakeReceiver({"code": "abc-code", "state": "abc"})
    app = FakeApp()
    opened: list[str] = []

    result = run_browser_sign_in(
        app,
        ["Files.ReadWrite.All"],
        timeout=300,
        prompt="select_account",
        login_hint=None,
        receiver_factory=lambda: receiver,
        open_url=lambda url: opened.append(url) or True,
    )

    assert result == {"access_token": "token"}
    assert opened == ["https://login.example/authorize"]
    assert app.flow_options == {
        "scopes": ["Files.ReadWrite.All"],
        "redirect_uri": "http://localhost:50123",
        "prompt": "select_account",
    }
    assert receiver.wait_options["timeout"] == 300
    assert receiver.wait_options["state"] == "abc"
    assert prompt.events == [
        ("started", "https://login.example/authorize"),
        ("finished", "https://login.example/authorize"),
    ]
    assert receiver.closed


def test_sign_in_timeout_is_reported_without_exchanging(prompt: RecordingPrompt) -> None:
    app = FakeApp()

    result = run_browser_sign_in(
        app,
        ["Files.ReadWrite.All"],
        timeout=1,
        prompt=None,
        login_hint="alice@example.com",
        receiver_factory=lambda: FakeReceiver(None),
        open_url=lambda _url: False,
    )

    assert result["error"] == "timeout"
    assert app.flow_options["login_hint"] == "alice@example.com"
    assert "prompt" not in app.flow_options
    assert not app.exchanged
    assert prompt.events[-1][0] == "finished"


def test_cancel_from_prompt_aborts_sign_in(
    prompt: RecordingPrompt,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aborted: list[tuple[int, str]] = []
    monkeypatch.setattr(
        browser_sign_in,
        "_abort_receiver",
        lambda port, state: aborted.append((port, state)),
    )
    receiver = FakeReceiver({"error": "access_denied", "state": "abc"})
    receiver.on_wait = lambda: prompt.cancel() if prompt.cancel else None
    app = FakeApp()

    with pytest.raises(AuthError, match="annulee"):
        run_browser_sign_in(
            app,
            ["Files.ReadWrite.All"],
            timeout=300,
            prompt="select_account",
            login_hint=None,
            receiver_factory=lambda: receiver,
            open_url=lambda _url: True,
        )

    assert aborted == [(50123, "abc")]
    assert not app.exchanged
