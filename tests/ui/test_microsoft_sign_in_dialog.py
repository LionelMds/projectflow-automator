from __future__ import annotations

from PySide6.QtWidgets import QApplication, QDialog

from projectflow.ui.dialogs.microsoft_sign_in import MicrosoftSignInDialog, MicrosoftSignInPrompt

URL = "https://login.example/authorize"


def test_dialog_reopens_and_copies_sign_in_link(qtbot) -> None:  # type: ignore[no-untyped-def]
    opened: list[str] = []
    cancelled: list[bool] = []
    dialog = MicrosoftSignInDialog(
        URL,
        lambda: cancelled.append(True),
        open_url=lambda url: opened.append(url) or True,
    )
    qtbot.addWidget(dialog)

    dialog.open_button.click()
    dialog.copy_button.click()

    assert opened == [URL]
    assert QApplication.clipboard().text() == URL
    assert not cancelled


def test_cancel_button_cancels_pending_sign_in_once(qtbot) -> None:  # type: ignore[no-untyped-def]
    cancelled: list[bool] = []
    dialog = MicrosoftSignInDialog(URL, lambda: cancelled.append(True), open_url=lambda _url: True)
    qtbot.addWidget(dialog)

    dialog.cancel_button.click()
    dialog.reject()

    assert cancelled == [True]


def test_prompt_stays_usable_above_an_open_modal_window(qtbot) -> None:  # type: ignore[no-untyped-def]
    settings = QDialog()
    qtbot.addWidget(settings)
    settings.setModal(True)
    settings.show()
    qtbot.waitUntil(lambda: QApplication.activeModalWidget() is settings)
    prompt = MicrosoftSignInPrompt()

    prompt.sign_in_started(URL, lambda: None)
    qtbot.waitUntil(lambda: len(prompt.open_dialogs()) == 1)

    assert prompt.open_dialogs()[0].parent() is settings
    prompt.sign_in_finished(URL)
    qtbot.waitUntil(lambda: not prompt.open_dialogs())


def test_prompt_shows_dialog_and_closes_it_after_sign_in(qtbot) -> None:  # type: ignore[no-untyped-def]
    cancelled: list[bool] = []
    prompt = MicrosoftSignInPrompt()

    prompt.sign_in_started(URL, lambda: cancelled.append(True))
    qtbot.waitUntil(lambda: len(prompt.open_dialogs()) == 1)
    dialog = prompt.open_dialogs()[0]
    assert dialog.isVisible()

    prompt.sign_in_finished(URL)
    qtbot.waitUntil(lambda: not prompt.open_dialogs())

    assert not cancelled
