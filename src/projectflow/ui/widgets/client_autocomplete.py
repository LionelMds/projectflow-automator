from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit

from projectflow.core.client_directory import ClientDirectory


class ClientAutocomplete:
    def __init__(self, *, societe_edit: QLineEdit, contact_edit: QLineEdit) -> None:
        self._societe_edit = societe_edit
        self._contact_edit = contact_edit
        self._directory = ClientDirectory()
        self._societe_model = QStringListModel(self._societe_edit)
        self._contact_model = QStringListModel(self._contact_edit)
        self._societe_completer = _completer(self._societe_model, self._societe_edit)
        self._contact_completer = _completer(self._contact_model, self._contact_edit)
        self._societe_edit.setCompleter(self._societe_completer)
        self._contact_edit.setCompleter(self._contact_completer)
        self._societe_edit.textChanged.connect(self._refresh_contacts)
        self._societe_edit.editingFinished.connect(self._normalize_societe)
        self._contact_edit.editingFinished.connect(self._normalize_contact)

    def set_directory(self, directory: ClientDirectory) -> None:
        self._directory = directory
        self._societe_model.setStringList(list(directory.companies))
        self._refresh_contacts()
        self._show_active_completion(self._societe_edit, self._societe_completer)
        self._show_active_completion(self._contact_edit, self._contact_completer)

    def _refresh_contacts(self) -> None:
        contacts = self._directory.contacts_for_company(self._societe_edit.text())
        self._contact_model.setStringList(list(contacts))

    def _normalize_societe(self) -> None:
        canonical = self._directory.canonical_company(self._societe_edit.text())
        if canonical is not None:
            self._societe_edit.setText(canonical)

    def _normalize_contact(self) -> None:
        canonical = self._directory.canonical_contact(
            self._contact_edit.text(),
            societe=self._societe_edit.text(),
        )
        if canonical is not None:
            self._contact_edit.setText(canonical)

    @staticmethod
    def _show_active_completion(edit: QLineEdit, completer: QCompleter) -> None:
        if edit.hasFocus() and edit.text().strip():
            completer.complete()


def _completer(model: QStringListModel, parent: QLineEdit) -> QCompleter:
    completer = QCompleter(model, parent)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setMaxVisibleItems(12)
    completer.setWrapAround(False)
    return completer
