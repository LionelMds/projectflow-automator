from __future__ import annotations

import ctypes
import os
import sys
import threading
import urllib.request
import webbrowser
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from importlib import import_module
from typing import Protocol, cast
from urllib.parse import urlencode

from projectflow.exceptions import AuthError
from projectflow.logging import get_logger

SUCCESS_PAGE = (
    "<html><body style='font-family:sans-serif'><h2>Connexion Microsoft reussie</h2>"
    "<p>Vous pouvez fermer cet onglet et revenir a ProjectFlow.</p></body></html>"
)
ERROR_PAGE = (
    "<html><body style='font-family:sans-serif'><h2>Connexion Microsoft interrompue</h2>"
    "<p>$error_description</p><p>Revenez a ProjectFlow pour recommencer.</p></body></html>"
)


class SignInPrompt(Protocol):
    """Tells the user a browser sign-in is waiting. Called from a worker thread."""

    def sign_in_started(self, sign_in_url: str, cancel: Callable[[], None]) -> None:
        """Show the pending sign-in with a way to reopen, copy or cancel it."""

    def sign_in_finished(self, sign_in_url: str) -> None:
        """Hide the pending sign-in."""


class AuthCodeReceiverProtocol(AbstractContextManager["AuthCodeReceiverProtocol"], Protocol):
    def get_port(self) -> int:
        """Return the local port receiving Microsoft's redirection."""

    def get_auth_response(self, **kwargs: object) -> dict[str, object] | None:
        """Wait for the redirection; None on timeout."""


class AuthCodeFlowApplication(Protocol):
    def initiate_auth_code_flow(self, scopes: list[str], **kwargs: object) -> dict[str, object]:
        """Prepare the sign-in URL."""

    def acquire_token_by_auth_code_flow(
        self,
        auth_code_flow: dict[str, object],
        auth_response: dict[str, object],
    ) -> object:
        """Exchange the received code for tokens."""


# Opening the browser must never delay the ProjectFlow window; after this delay
# the sign-in goes on and the user can use the window's buttons instead.
BROWSER_OPEN_WAIT_SECONDS = 5
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4

_prompt_lock = threading.Lock()
_prompt: SignInPrompt | None = None
_pending_cancels: dict[int, Callable[[], None]] = {}


def set_sign_in_prompt(prompt: SignInPrompt | None) -> None:
    global _prompt  # noqa: PLW0603
    with _prompt_lock:
        _prompt = prompt


def _current_prompt() -> SignInPrompt | None:
    with _prompt_lock:
        return _prompt


def cancel_pending_sign_ins() -> None:
    """Stop every browser sign-in still waiting, e.g. before signing in again."""
    with _prompt_lock:
        cancels = list(_pending_cancels.values())
    for cancel in cancels:
        cancel()


def open_sign_in_page(url: str) -> bool:
    """Open the page with the system handler, which also works when Edge runs hidden."""
    startfile = getattr(os, "startfile", None)
    if sys.platform == "win32" and callable(startfile):
        # ShellExecute requires COM on the calling thread; without it, some browser
        # handlers never return and the sign-in was stuck before any window appeared.
        with _com_initialized():
            try:
                startfile(url)
            except OSError:
                get_logger(__name__).warning("auth.browser.startfile_failed")
            else:
                return True
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        get_logger(__name__).warning("auth.browser.open_failed")
        return False


def open_sign_in_page_in_background(
    url: str,
    open_url: Callable[[str], bool] = open_sign_in_page,
    *,
    wait_seconds: float = BROWSER_OPEN_WAIT_SECONDS,
) -> bool | None:
    """Open the page without ever blocking the caller; None if still opening."""
    outcome: list[bool] = []
    thread = threading.Thread(
        target=lambda: outcome.append(open_url(url)),
        name="projectflow-open-sign-in",
        daemon=True,
    )
    thread.start()
    thread.join(wait_seconds)
    if thread.is_alive():
        get_logger(__name__).warning("auth.browser.open_slow", waited_s=wait_seconds)
        return None
    return outcome[0] if outcome else False


@contextmanager
def _com_initialized() -> Iterator[None]:
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        yield
        return
    ole32 = windll.ole32
    result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
    try:
        yield
    finally:
        if result >= 0:  # S_OK or S_FALSE must be balanced by CoUninitialize.
            ole32.CoUninitialize()


def run_browser_sign_in(
    app: AuthCodeFlowApplication,
    scopes: list[str],
    *,
    timeout: int | None,
    prompt: str | None,
    login_hint: str | None,
    receiver_factory: Callable[[], AuthCodeReceiverProtocol] | None = None,
    open_url: Callable[[str], bool] = open_sign_in_page,
) -> dict[str, object]:
    logger = get_logger(__name__)
    with (receiver_factory or _default_receiver)() as receiver:
        port = receiver.get_port()
        # MSAL's local receiver only accepts the redirection as an HTTP POST.
        flow_options: dict[str, object] = {
            "redirect_uri": f"http://localhost:{port}",
            "response_mode": "form_post",
        }
        if prompt:
            flow_options["prompt"] = prompt
        if login_hint:
            flow_options["login_hint"] = login_hint
        flow = app.initiate_auth_code_flow(scopes, **flow_options)
        sign_in_url = str(flow["auth_uri"])
        state = str(flow.get("state", ""))
        cancelled = threading.Event()

        def cancel() -> None:
            if not cancelled.is_set():
                cancelled.set()
                _abort_receiver(port, state)

        with _prompt_lock:
            _pending_cancels[id(cancel)] = cancel
        # Show ProjectFlow's window first: whatever the browser does, the user can
        # reopen the page, copy the link or cancel.
        ui = _current_prompt()
        if ui is not None:
            ui.sign_in_started(sign_in_url, cancel)
        try:
            opened = open_sign_in_page_in_background(
                sign_in_url,
                open_url,
                wait_seconds=BROWSER_OPEN_WAIT_SECONDS,
            )
            logger.info("auth.interactive.started", browser_opened=opened, prompt=ui is not None)
            response = receiver.get_auth_response(
                timeout=timeout,
                state=state,
                success_template=SUCCESS_PAGE,
                error_template=ERROR_PAGE,
            )
        finally:
            with _prompt_lock:
                _pending_cancels.pop(id(cancel), None)
            if ui is not None:
                ui.sign_in_finished(sign_in_url)

    if cancelled.is_set():
        logger.info("auth.interactive.cancelled")
        raise AuthError("Connexion Microsoft annulee.")
    if not response:
        logger.warning("auth.interactive.timeout")
        return {
            "error": "timeout",
            "error_description": "delai de connexion depasse dans le navigateur",
        }
    result = app.acquire_token_by_auth_code_flow(flow, response)
    if isinstance(result, Mapping):
        logger.info("auth.interactive.finished", error=result.get("error"))
        return dict(result)
    return {}


def _abort_receiver(port: int, state: str) -> None:
    # Same shape as Microsoft's form_post redirection, so the receiver stops waiting.
    body = urlencode(
        {
            "error": "access_denied",
            "error_description": "Connexion annulee dans ProjectFlow.",
            "state": state,
        },
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5):  # noqa: S310
            pass
    except OSError:
        get_logger(__name__).warning("auth.interactive.abort_failed")


def _default_receiver() -> AuthCodeReceiverProtocol:
    module = import_module("msal.oauth2cli.authcode")
    receiver_type = cast("Callable[..., AuthCodeReceiverProtocol]", module.AuthCodeReceiver)
    return receiver_type(port=0)
