from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from projectflow.config import AppConfig
from projectflow.platform.paths import detect_onedrive_balz_root, native_path_text
from projectflow.ui.widgets.industry import (
    IconLabel,
    LogoLabel,
    PrimaryButton,
    button,
    label,
    rule,
)

_STEPS = (
    ("Bienvenue", "Ce que fait ProjectFlow"),
    ("Chemins", "Racine, référence, répertoire"),
    ("Vérification", "Accès et connexion cloud"),
)


class OnboardingWizard(QDialog):
    """First-run assistant: welcome, the three required paths, then a check."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config.model_copy(deep=True)
        self.setWindowTitle("Bienvenue dans ProjectFlow")
        self.resize(1180, 760)
        self.setMinimumSize(980, 640)
        self.welcome_page = WelcomePage()
        self.paths_page = PathsPage(self.config)
        self.verify_page = VerifyPage(self.paths_page)
        self._pages: list[QWidget] = [self.welcome_page, self.paths_page, self.verify_page]
        self._build_ui()
        self._go_to(0)

    def page(self, index: int) -> QWidget | None:
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def current_index(self) -> int:
        return self._stack.currentIndex()

    def accept(self) -> None:
        self.paths_page.apply_to_config()
        super().accept()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        aside = QWidget()
        aside.setProperty("role", "onb-aside")
        aside.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        aside.setFixedWidth(380)
        aside_layout = QVBoxLayout(aside)
        aside_layout.setContentsMargins(36, 40, 36, 40)
        aside_layout.setSpacing(30)
        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo_tile = QWidget()
        logo_tile.setProperty("role", "onb-logo")
        logo_tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        logo_tile.setFixedSize(40, 40)
        tile_layout = QVBoxLayout(logo_tile)
        tile_layout.setContentsMargins(3, 3, 3, 3)
        tile_layout.addWidget(LogoLabel(34))
        brand.addWidget(logo_tile)
        brand.addWidget(label("ProjectFlow", "onb-brand"))
        brand.addStretch(1)
        aside_layout.addLayout(brand)

        steps = QVBoxLayout()
        steps.setSpacing(0)
        steps.addWidget(rule())
        self._step_buttons = QButtonGroup(self)
        for index, (title, detail) in enumerate(_STEPS):
            step = QPushButton()
            step.setProperty("variant", "onb-step")
            step.setCheckable(True)
            step.setCursor(Qt.CursorShape.PointingHandCursor)
            step.setMinimumHeight(78)
            row = QHBoxLayout(step)
            row.setContentsMargins(0, 14, 0, 14)
            row.setSpacing(14)
            number = label(f"{index + 1:02d}", "onb-num")
            number.setFixedWidth(44)
            row.addWidget(number, 0, Qt.AlignmentFlag.AlignTop)
            texts = QVBoxLayout()
            texts.setSpacing(2)
            texts.addWidget(label(title, "onb-title"))
            texts.addWidget(label(detail, "onb-detail"))
            row.addLayout(texts, 1)
            self._step_buttons.addButton(step, index)
            steps.addWidget(step)
            steps.addWidget(rule())
        self._step_buttons.idClicked.connect(self._go_to)
        aside_layout.addLayout(steps)
        aside_layout.addStretch(1)
        aside_layout.addWidget(label("Balz Métal SA · première configuration", "onb-detail"))
        root.addWidget(aside)

        main = QVBoxLayout()
        main.setContentsMargins(64, 56, 64, 36)
        main.setSpacing(22)
        self._stack = QStackedWidget()
        for page in self._pages:
            self._stack.addWidget(page)
        main.addWidget(self._stack, 1)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.back_button = button("Précédent", "ghost")
        self.back_button.clicked.connect(lambda: self._go_to(self._stack.currentIndex() - 1))
        self.next_button = PrimaryButton("Suivant")
        self.next_button.setProperty("size", "lg")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.next_button)
        main.addLayout(footer)
        root.addLayout(main, 1)

    def _next(self) -> None:
        index = self._stack.currentIndex()
        if index >= len(self._pages) - 1:
            self.accept()
            return
        self._go_to(index + 1)

    def _go_to(self, index: int) -> None:
        index = max(0, min(index, len(self._pages) - 1))
        self._stack.setCurrentIndex(index)
        if self._pages[index] is self.verify_page:
            self.verify_page.refresh()
        for step_index, step in enumerate(self._step_buttons.buttons()):
            step.setChecked(step_index == index)
            state = "current" if step_index == index else "done" if step_index < index else "todo"
            step.setProperty("state", state)
            for child in step.findChildren(QLabel):
                child.setProperty("state", state)
                child.style().unpolish(child)
                child.style().polish(child)
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Terminer" if index == len(self._pages) - 1 else "Suivant")


class WelcomePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(label("Bienvenue", "kicker"))
        headline = label(
            "Un seul formulaire pour les dossiers, les fiches et le répertoire chantier.",
            "h1",
            wrap=True,
        )
        headline.setMaximumWidth(720)
        layout.addWidget(headline)
        cells = QGridLayout()
        cells.setSpacing(0)
        for column, (number, title, text) in enumerate(
            (
                (
                    "01",
                    "Dossier et fiche",
                    "Copie du dossier de référence et fiche Excel renseignée.",
                ),
                (
                    "02",
                    "Répertoire chantier",
                    "Ligne écrite directement dans le classeur OneDrive.",
                ),
                ("03", "Intégrations", "Outlook, Planner et modèles CAO en option."),
            ),
        ):
            cell = QWidget()
            cell.setProperty("role", "cell")
            cell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(20, 18, 20, 18)
            cell_layout.setSpacing(6)
            cell_layout.addWidget(label(number, "num-xl"))
            cell_layout.addWidget(label(title, "h5"))
            cell_layout.addWidget(label(text, "muted", wrap=True))
            cell_layout.addStretch(1)
            cells.addWidget(cell, 0, column)
            cells.setColumnStretch(column, 1)
        layout.addSpacing(12)
        layout.addLayout(cells)
        layout.addWidget(
            label(
                "Trois chemins suffisent pour commencer. Outlook, Planner et les modèles CAO "
                "se configurent ensuite dans les paramètres.",
                "muted",
                wrap=True,
            ),
        )
        layout.addStretch(1)


class PathsPage(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        onedrive_root = detect_onedrive_balz_root()
        default_clients = onedrive_root / "Clients" if onedrive_root else Path.home()
        default_reference = (
            onedrive_root / "Modeles" / "10-Racine" if onedrive_root else Path.home()
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(label("Étape 2 sur 3", "kicker"))
        layout.addWidget(label("Configuration des chemins", "display"))
        if onedrive_root is not None:
            notice = QWidget()
            notice.setProperty("role", "notice")
            notice.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            notice_layout = QHBoxLayout(notice)
            notice_layout.setContentsMargins(14, 10, 14, 10)
            notice_layout.setSpacing(10)
            notice_layout.addWidget(IconLabel("check-circle", "accent-700", 16))
            notice_layout.addWidget(
                label(
                    f"OneDrive « {onedrive_root.name} » détecté — racine et dossier de "
                    "référence pré-remplis.",
                    tone="success",
                ),
                1,
            )
            layout.addWidget(notice)

        self.racine_edit = QLineEdit(
            native_path_text(config.paths.racine_projets or default_clients),
        )
        self.reference_edit = QLineEdit(
            native_path_text(config.paths.dossier_reference or default_reference),
        )
        self.repertoire_edit = QLineEdit(
            native_path_text(config.paths.repertoire_chantier.display_path),
        )
        self.repertoire_edit.setPlaceholderText("Chemin Excel ou lien OneDrive / SharePoint")
        rows = (
            (
                "Racine projets",
                self.racine_edit,
                True,
                "Les dossiers projet sont créés sous Racine\\Année.",
            ),
            (
                "Dossier de référence",
                self.reference_edit,
                True,
                "Copié sans rien écraser à chaque création.",
            ),
            (
                "Répertoire chantier",
                self.repertoire_edit,
                False,
                "Chemin Excel ou lien OneDrive / SharePoint — utilisé pour les écritures.",
            ),
        )
        for index, (caption, edit, directory, help_text) in enumerate(rows):
            layout.addWidget(rule())
            row = QHBoxLayout()
            row.setSpacing(14)
            number = label(f"{index + 1:02d}", "num-lg")
            number.setFixedWidth(40)
            row.addWidget(number, 0, Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(5)
            column.addWidget(label(caption, "field-lg"))
            column.addWidget(_browse_row(edit, directory=directory))
            column.addWidget(label(help_text, "small"))
            row.addLayout(column, 1)
            layout.addLayout(row)
        layout.addStretch(1)

    def apply_to_config(self) -> None:
        self._config.paths.racine_projets = Path(self.racine_edit.text()).expanduser()
        self._config.paths.dossier_reference = Path(self.reference_edit.text()).expanduser()
        self._config.paths.repertoire_chantier.display_path = native_path_text(
            self.repertoire_edit.text(),
        )
        self._config.paths.repertoire_chantier.drive_id = ""
        self._config.paths.repertoire_chantier.item_id = ""


class VerifyPage(QWidget):
    """Check what can be checked locally before the first creation."""

    def __init__(self, paths_page: PathsPage) -> None:
        super().__init__()
        self._paths_page = paths_page
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(22)
        layout.addWidget(label("Étape 3 sur 3", "kicker"))
        layout.addWidget(label("Vérification", "display"))
        self._checks = QVBoxLayout()
        self._checks.setSpacing(0)
        layout.addLayout(self._checks)
        layout.addWidget(
            label(
                "ProjectFlow reste disponible dans la zone de notification ; fermer la fenêtre "
                "ne quitte pas l'application.",
                "muted",
                wrap=True,
            ),
        )
        layout.addStretch(1)

    def refresh(self) -> None:
        while self._checks.count():
            item = self._checks.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._checks.addWidget(rule())
        for ok, title, detail in _path_checks(self._paths_page):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 14, 0, 14)
            row_layout.setSpacing(14)
            mark = QWidget()
            mark.setProperty("role", "check-box" if ok else "check-box-warn")
            mark.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            mark.setFixedSize(22, 22)
            mark_layout = QVBoxLayout(mark)
            mark_layout.setContentsMargins(4, 4, 4, 4)
            mark_layout.addWidget(
                IconLabel("check" if ok else "alert", "accent-800" if ok else "err", 13),
            )
            row_layout.addWidget(mark)
            row_layout.addWidget(label(title, "check-title"), 1)
            row_layout.addWidget(label(detail, "muted"))
            self._checks.addWidget(row)
            self._checks.addWidget(rule())


def _path_checks(page: PathsPage) -> list[tuple[bool, str, str]]:
    racine = Path(page.racine_edit.text().strip()).expanduser()
    reference = Path(page.reference_edit.text().strip()).expanduser()
    repertoire = page.repertoire_edit.text().strip()
    checks: list[tuple[bool, str, str]] = []
    racine_ok = bool(page.racine_edit.text().strip()) and racine.is_dir()
    checks.append(
        (
            racine_ok,
            "Racine projets accessible" if racine_ok else "Racine projets introuvable",
            racine.name if racine_ok else "Vérifiez le chemin à l'étape 2",
        ),
    )
    reference_ok = bool(page.reference_edit.text().strip()) and reference.is_dir()
    items = len(list(reference.iterdir())) if reference_ok else 0
    checks.append(
        (
            reference_ok,
            "Dossier de référence trouvé" if reference_ok else "Dossier de référence introuvable",
            f"{reference.name} · {items} élément{'s' if items > 1 else ''}"
            if reference_ok
            else "Vérifiez le chemin à l'étape 2",
        ),
    )
    cloud = "://" in repertoire
    repertoire_ok = cloud or (bool(repertoire) and Path(repertoire).expanduser().is_file())
    if cloud:
        detail = "OneDrive · connexion au premier chargement"
    elif repertoire_ok:
        detail = Path(repertoire).name
    else:
        detail = "Renseignez un classeur Excel ou un lien OneDrive"
    checks.append(
        (
            repertoire_ok,
            "Répertoire chantier renseigné" if repertoire_ok else "Répertoire chantier manquant",
            detail,
        ),
    )
    return checks


def _browse_row(edit: QLineEdit, *, directory: bool) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    browse_button = button("Parcourir")

    def browse() -> None:
        if directory:
            selected = QFileDialog.getExistingDirectory(widget, "Sélectionner")
        else:
            selected, _ = QFileDialog.getOpenFileName(
                widget,
                "Sélectionner",
                filter="Excel (*.xlsx)",
            )
        if selected:
            edit.setText(native_path_text(selected))

    browse_button.clicked.connect(browse)
    layout.addWidget(edit, 1)
    layout.addWidget(browse_button)
    return widget
