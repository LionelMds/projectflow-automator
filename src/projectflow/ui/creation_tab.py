from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from projectflow.config import CadConfig, PlannerConfig
from projectflow.core.client_directory import ClientDirectory
from projectflow.ui.theme import current_theme, theme_notifier
from projectflow.ui.widgets.cad import CadOptionsWidget
from projectflow.ui.widgets.client_autocomplete import ClientAutocomplete
from projectflow.ui.widgets.industry import (
    BlueprintFrame,
    ElidedLabel,
    PrimaryButton,
    StatusBanner,
    button,
    label,
    rule,
    section_heading,
)
from projectflow.ui.widgets.industry import (
    field as caption_field,
)
from projectflow.ui.widgets.planner import PlannerSelectionWidget, PlannerTaskFormData


@dataclass(frozen=True, slots=True)
class CreationFormData:
    year: str
    project_id: str
    subproject_id: str
    designation: str
    societe: str
    contact: str
    localisation: str
    gere_par: str
    planner: PlannerTaskFormData = field(default_factory=PlannerTaskFormData)
    add_solidworks: bool = False
    add_autocad: bool = False


class CreationTab(QWidget):
    create_requested = Signal()
    update_requested = Signal()
    load_requested = Signal()
    open_folder_requested = Signal()
    open_fiche_requested = Signal()
    print_fiche_requested = Signal()
    open_repertoire_requested = Signal()
    next_available_requested = Signal()
    settings_requested = Signal()
    planner_options_requested = Signal()
    client_suggestions_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._log_entries: list[tuple[str, str]] = []
        self._build_ui()
        theme_notifier().changed.connect(self._render_logs)

    def set_user_initials(self, initials: str) -> None:
        self.user_initials_edit.setText(initials)
        self.user_initials_tag.setText(initials or "—")

    def data(self) -> CreationFormData:
        add_solidworks, add_autocad = self.cad_options.values()
        return CreationFormData(
            year=self.year_combo.currentText().strip(),
            project_id=self.project_id_edit.text().strip(),
            subproject_id=self.subproject_edit.text().strip(),
            designation=self.designation_edit.text().strip(),
            societe=self.societe_edit.text().strip(),
            contact=self.contact_edit.text().strip(),
            localisation=self.localisation_edit.text().strip(),
            gere_par=self.gere_par_edit.text().strip(),
            planner=self.planner_widget.data(),
            add_solidworks=add_solidworks,
            add_autocad=add_autocad,
        )

    def set_project_identity(self, *, year: str, project_id: str, subproject_id: str = "") -> None:
        index = self.year_combo.findText(year)
        if index >= 0:
            self.year_combo.setCurrentIndex(index)
        else:
            self.year_combo.addItem(year)
            self.year_combo.setCurrentText(year)
        self.project_id_edit.setText(project_id)
        self.subproject_edit.setText(subproject_id)

    def set_form_data(self, data: CreationFormData) -> None:
        self.set_project_identity(
            year=data.year,
            project_id=data.project_id,
            subproject_id=data.subproject_id,
        )
        self.designation_edit.setText(data.designation)
        self.societe_edit.setText(data.societe)
        self.contact_edit.setText(data.contact)
        self.localisation_edit.setText(data.localisation)
        self.gere_par_edit.setText(data.gere_par)
        self.planner_widget.set_data(data.planner)
        self.cad_options.set_values(solidworks=data.add_solidworks, autocad=data.add_autocad)

    def append_log(self, message: str) -> None:
        self._log_entries.append((datetime.now(tz=UTC).astimezone().strftime("%H:%M:%S"), message))
        self._render_logs()

    def reset_form_fields(self) -> None:
        for edit in [
            self.project_id_edit,
            self.subproject_edit,
            self.designation_edit,
            self.societe_edit,
            self.contact_edit,
            self.localisation_edit,
            self.gere_par_edit,
        ]:
            edit.clear()
        self.planner_widget.reset_fields()
        self.cad_options.reset()
        self.status_banner.clear()

    def reset_cad_options(self) -> None:
        self.cad_options.reset()

    def apply_cad_config(self, cad: CadConfig) -> None:
        self.cad_options.apply_config(cad)

    def apply_planner_config(self, planner: PlannerConfig) -> None:
        self.planner_widget.set_config_defaults(
            enabled=planner.enabled,
            bucket_id=planner.bucket_id,
            bucket_name=planner.bucket_name,
            due_days=planner.due_days,
        )

    def set_outlook_summary(self, *, enabled: bool, detail: str) -> None:
        self.outlook_title.setEnabled(enabled)
        self.outlook_detail.setText(detail if enabled else "Désactivé dans les paramètres")

    def set_client_directory(self, directory: ClientDirectory) -> None:
        self._client_autocomplete.set_directory(directory)

    # Operation feedback shown above the form.

    def show_operation_progress(self, message: str) -> None:
        self.status_banner.show_progress(
            message,
            "Les actions reprennent à la fin de l'opération",
        )

    def show_operation_success(self, message: str) -> None:
        self.status_banner.show_success(message)

    def show_operation_error(self, message: str) -> None:
        self.status_banner.show_error(message)

    def clear_operation_progress(self) -> None:
        if self.status_banner.kind == "progress":
            self.status_banner.clear()

    # UI

    def _build_ui(self) -> None:
        self.setMinimumWidth(760)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(20, 16, 20, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnMinimumWidth(1, 352)

        grid.addLayout(self._build_title(), 0, 0, 1, 2)
        self.status_banner = StatusBanner()
        self._banner_open_folder = self.status_banner.add_action("Ouvrir dossier", ("success",))
        self._banner_open_fiche = self.status_banner.add_action("Ouvrir fiche", ("success",))
        self._banner_retry = self.status_banner.add_action("Réessayer", ("error",))
        self._banner_open_folder.clicked.connect(self.open_folder_requested.emit)
        self._banner_open_fiche.clicked.connect(self.open_fiche_requested.emit)
        self._banner_retry.clicked.connect(self.create_requested.emit)
        grid.addWidget(self.status_banner, 1, 0, 1, 2)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self._build_identity_section())
        left.addWidget(self._build_client_section())
        left.addWidget(self._build_options_section())
        left.addStretch(1)
        grid.addLayout(left, 2, 0)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(10)
        self.config_frame = self._build_paths_section()
        right.addWidget(self.config_frame)
        right.addWidget(self._build_journal_section(), 1)
        right_widget = QWidget()
        right_widget.setLayout(right)
        right_widget.setFixedWidth(352)
        grid.addWidget(right_widget, 2, 1)
        grid.setRowStretch(2, 1)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        root.addWidget(rule())
        root.addWidget(self._build_footer())

        for edit in (self.project_id_edit, self.subproject_edit, self.designation_edit):
            edit.textChanged.connect(self._refresh_headline)
        self.year_combo.currentTextChanged.connect(self._refresh_headline)
        self._refresh_headline()
        self._render_logs()

    def _build_title(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 4)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Création projet", "kicker"))
        self.headline_label = ElidedLabel("Nouveau projet", Qt.TextElideMode.ElideRight)
        self.headline_label.setProperty("role", "h2")
        titles.addWidget(self.headline_label)
        row.addLayout(titles, 1)
        shortcuts = label("Ctrl+L Charger · Ctrl+S Mettre à jour · Ctrl+P Imprimer", "muted")
        row.addWidget(shortcuts, 0, Qt.AlignmentFlag.AlignBottom)
        return row

    def _build_identity_section(self) -> BlueprintFrame:
        section = BlueprintFrame(spacing=14)
        section.box.addWidget(section_heading("01", "Identification"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.year_combo = QComboBox()
        self.year_combo.setEditable(True)
        self.year_combo.setFixedWidth(110)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setPlaceholderText("4995")
        self.project_id_edit.setMinimumWidth(140)
        self.subproject_edit = QLineEdit()
        self.subproject_edit.setPlaceholderText("Optionnel")
        self.next_button = button("Suivant disponible")
        self.next_button.clicked.connect(self.next_available_requested.emit)
        row.addWidget(caption_field("Année", self.year_combo))
        row.addWidget(caption_field("Numéro", self.project_id_edit), 1)
        subproject = caption_field("Sous-projet", self.subproject_edit)
        subproject.setFixedWidth(140)
        row.addWidget(subproject)
        row.addWidget(self.next_button, 0, Qt.AlignmentFlag.AlignBottom)
        section.box.addLayout(row)
        self.designation_edit = QLineEdit()
        self.designation_edit.setPlaceholderText("Ouvrage, bâtiment, lot…")
        section.box.addWidget(caption_field("Désignation", self.designation_edit))
        return section

    def _build_client_section(self) -> BlueprintFrame:
        section = BlueprintFrame(spacing=14)
        section.box.addWidget(
            section_heading(
                "02",
                "Client",
                trailing=label("Suggestions depuis le répertoire", "small"),
            ),
        )
        self.societe_edit = QLineEdit()
        self.contact_edit = QLineEdit()
        self._client_autocomplete = ClientAutocomplete(
            societe_edit=self.societe_edit,
            contact_edit=self.contact_edit,
        )
        self.societe_edit.textEdited.connect(
            lambda _text: self.client_suggestions_requested.emit(),
        )
        self.contact_edit.textEdited.connect(
            lambda _text: self.client_suggestions_requested.emit(),
        )
        self.localisation_edit = QLineEdit()
        self.gere_par_edit = QLineEdit()
        # Holds the initials written in C9; shown as a tag, edited in the settings.
        self.user_initials_edit = QLineEdit()
        self.user_initials_edit.setReadOnly(True)
        self.user_initials_edit.hide()
        self.user_initials_tag = label("—", "initials")
        self.user_initials_tag.setToolTip(
            "Initiales de l'utilisateur définies dans les paramètres."
        )
        settings_link = button("Modifier dans les paramètres", "ghost", size="sm")
        settings_link.clicked.connect(self.settings_requested.emit)
        initials_row = QHBoxLayout()
        initials_row.setSpacing(10)
        initials_row.addWidget(self.user_initials_tag)
        initials_row.addWidget(settings_link)
        initials_row.addStretch(1)
        initials_row.addWidget(self.user_initials_edit)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        grid.addWidget(caption_field("Société", self.societe_edit), 0, 0)
        grid.addWidget(caption_field("Contact", self.contact_edit), 0, 1)
        grid.addWidget(caption_field("Localisation", self.localisation_edit), 1, 0, 1, 2)
        grid.addWidget(caption_field("Géré par · C6", self.gere_par_edit), 2, 0)
        grid.addWidget(caption_field("Initiales utilisateur · C9", initials_row), 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        section.box.addLayout(grid)
        return section

    def _build_options_section(self) -> BlueprintFrame:
        section = BlueprintFrame(padding=(0, 0, 0, 0), layout="h", spacing=0)

        planner_column = QWidget()
        planner_layout = QVBoxLayout(planner_column)
        planner_layout.setContentsMargins(20, 18, 20, 20)
        planner_layout.setSpacing(12)
        planner_layout.addWidget(section_heading("03", "Microsoft Planner"))
        self.planner_widget = PlannerSelectionWidget()
        self.planner_widget.options_requested.connect(self.planner_options_requested.emit)
        planner_layout.addWidget(self.planner_widget)
        planner_layout.addStretch(1)

        self.cad_frame = QWidget()
        cad_layout = QVBoxLayout(self.cad_frame)
        cad_layout.setContentsMargins(20, 18, 20, 20)
        cad_layout.setSpacing(14)
        cad_layout.addWidget(section_heading("04", "Fichiers CAO"))
        self.cad_options = CadOptionsWidget()
        cad_layout.addWidget(self.cad_options)
        cad_layout.addWidget(rule())
        self.outlook_title = QLabel("Dossier Outlook")
        self.outlook_detail = label("", "small", wrap=True)
        outlook = QVBoxLayout()
        outlook.setSpacing(0)
        outlook.addWidget(self.outlook_title)
        outlook.addWidget(self.outlook_detail)
        cad_layout.addLayout(outlook)
        cad_layout.addStretch(1)

        section.box.addWidget(planner_column, 1)
        section.box.addWidget(rule(vertical=True))
        section.box.addWidget(self.cad_frame, 1)
        return section

    def _build_paths_section(self) -> BlueprintFrame:
        section = BlueprintFrame(padding=(18, 14, 18, 16), spacing=10)
        settings_button = button("Paramètres", "ghost", size="sm")
        settings_button.clicked.connect(self.settings_requested.emit)
        heading = QHBoxLayout()
        heading.addWidget(label("Chemins", "h5"))
        heading.addStretch(1)
        heading.addWidget(settings_button)
        section.box.addLayout(heading)
        self.racine_label = ElidedPathLabel("Non configuré")
        self.reference_label = ElidedPathLabel("Non configuré")
        self.repertoire_label = ElidedPathLabel("Non configuré")
        for caption, value in (
            ("Racine projets", self.racine_label),
            ("Dossier de référence", self.reference_label),
            ("Répertoire chantier", self.repertoire_label),
        ):
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(label(caption, "caps"))
            value.setProperty("role", "path")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(value)
            section.box.addLayout(column)
        return section

    def _build_journal_section(self) -> BlueprintFrame:
        section = BlueprintFrame(padding=(18, 14, 18, 16), spacing=10)
        section.setMinimumHeight(220)
        heading = QHBoxLayout()
        heading.addWidget(label("Journal", "h5"))
        heading.addStretch(1)
        self.journal_count_label = label("", "small")
        heading.addWidget(self.journal_count_label)
        section.box.addLayout(heading)
        self.journal_empty_label = label(
            "Aucune opération. Chaque étape de la création s'affichera ici.",
            "muted",
            wrap=True,
        )
        section.box.addWidget(self.journal_empty_label)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setProperty("role", "journal")
        self.logs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.logs.document().setDocumentMargin(0)
        section.box.addWidget(self.logs, 1)
        return section

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setProperty("role", "footer")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(26, 6, 20, 6)
        actions.setSpacing(8)
        self.reset_button = button("Réinitialiser", "ghost")
        self.load_button = button("Charger")
        self.open_folder_button = button("Ouvrir dossier")
        self.open_button = QToolButton()
        self.open_button.setText("Ouvrir fiche")
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        open_menu = QMenu(self.open_button)
        self.print_fiche_action = open_menu.addAction("Imprimer fiche")
        self.open_button.setMenu(open_menu)
        self.open_repertoire_button = button("Ouvrir répertoire")
        self.create_button = PrimaryButton("Créer")
        self.update_button = button("Mettre à jour")
        self.create_button.setDefault(True)
        self.reset_button.clicked.connect(self.reset_form_fields)
        self.load_button.clicked.connect(self.load_requested.emit)
        self.open_folder_button.clicked.connect(self.open_folder_requested.emit)
        self.open_button.clicked.connect(self.open_fiche_requested.emit)
        self.print_fiche_action.triggered.connect(self.print_fiche_requested.emit)
        self.open_repertoire_button.clicked.connect(self.open_repertoire_requested.emit)
        self.create_button.clicked.connect(self.create_requested.emit)
        self.update_button.clicked.connect(self.update_requested.emit)
        for widget in (
            self.reset_button,
            self.load_button,
            self.open_folder_button,
            self.open_button,
            self.open_repertoire_button,
        ):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.print_after_save_checkbox = QCheckBox("Imprimer fiche")
        self.print_after_save_checkbox.setToolTip(
            "Après Créer ou Mettre à jour, propose le choix de l'imprimante "
            "et imprime la fiche en A4.",
        )
        actions.addWidget(self.print_after_save_checkbox)
        actions.addSpacing(6)
        actions.addWidget(self.update_button)
        actions.addWidget(self.create_button)
        # Match the neighbouring push buttons, which are taller than a tool button.
        self.open_button.setFixedHeight(self.update_button.sizeHint().height())
        return footer

    def _refresh_headline(self) -> None:
        project_id = self.project_id_edit.text().strip()
        if not project_id:
            self.headline_label.setText("Nouveau projet")
            return
        number = f"{self.year_combo.currentText().strip()}-{project_id}"
        subproject = self.subproject_edit.text().strip()
        if subproject:
            number = f"{number}-{subproject}"
        designation = self.designation_edit.text().strip() or "Sans désignation"
        self.headline_label.setText(f"{number} · {designation}")

    def _render_logs(self) -> None:
        theme = current_theme()
        rows = []
        for time_text, message in self._log_entries:
            if message.startswith("!"):
                color = theme.tokens["err"]
            elif message.startswith("->"):
                color = theme.tokens["neutral-800"]
            else:
                color = theme.tokens["text"]
            rows.append(
                f'<tr><td style="color:{theme.tokens["neutral-700"]};padding:0 10px 6px 0">'
                f"{time_text}</td>"
                f'<td style="color:{color};padding:0 0 6px 0">{html.escape(message)}</td></tr>',
            )
        self.logs.setHtml(f'<table cellspacing="0" cellpadding="0">{"".join(rows)}</table>')
        scrollbar = self.logs.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        count = len(self._log_entries)
        self.journal_count_label.setText(
            f"{count} entrée{'s' if count > 1 else ''}" if count else "",
        )
        self.journal_empty_label.setVisible(count == 0)
        self.logs.setVisible(count > 0)


class ElidedPathLabel(ElidedLabel):
    """A path elided at its start, so the project-specific end stays readable."""

    def __init__(self, text: str = "") -> None:
        super().__init__(text, Qt.TextElideMode.ElideLeft)
