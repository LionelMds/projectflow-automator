"""« Notifications » page of the settings: the alerts of the planning on this poste.

On or off and for whom, the notification centre of the system and the sound, the hours
of silence, the two summaries with their times, the alert given to a new task by task
type, and starting ProjectFlow with the session so that the alerts ring without opening
it. Everything here belongs to the poste (``PlanningPosteConfig``), nothing is shared.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from projectflow.core.planning.alerts import BEFORE_PRESETS, AlertAudience, lead_text
from projectflow.core.planning.poste_config import PlanningPosteConfig, clock_minutes
from projectflow.core.planning.walltime import MINUTES_PER_HOUR
from projectflow.ui.dialogs.planning_team import SegmentedChoice
from projectflow.ui.widgets.industry import field, label

AUDIENCES: tuple[tuple[AlertAudience, str], ...] = (
    ("mine", "Mes tâches"),
    ("all", "Toutes les tâches"),
)
NO_DEFAULT = -1
NO_DEFAULT_TEXT = "Aucune"
TIME_WIDTH = 86
DEFAULT_WIDTH = 200
SYSTEM_NAME = "macOS" if sys.platform == "darwin" else "Windows"
AUTOSTART_TEXT = "Lancer ProjectFlow à l'ouverture de session, réduit dans la zone de notification"
AUTOSTART_UNAVAILABLE = (
    "Disponible avec ProjectFlow installé sur Windows ou macOS (pas depuis les sources)."
)
AUDIENCE_HELP = (
    "« Mes tâches » : celles de la personne de ce poste (initiales du Planning), et les "
    "tâches sans personne que ce poste a créées."
)


def default_text(minutes: int) -> str:
    return "Au début de la tâche" if minutes == 0 else f"{lead_text(minutes)} avant"


def _time_edit(text: str) -> QTimeEdit:
    minutes = clock_minutes(text)
    edit = QTimeEdit(QTime(minutes // MINUTES_PER_HOUR, minutes % MINUTES_PER_HOUR))
    edit.setDisplayFormat("HH:mm")
    edit.setFixedWidth(TIME_WIDTH)
    return edit


def _row(*widgets: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return row


class AlertSettingsPage(QGroupBox):
    """The page; ``apply_to`` writes its choices into a poste config."""

    def __init__(
        self,
        config: PlanningPosteConfig | None,
        *,
        task_types: Sequence[tuple[str, str]] = (),
        autostart: bool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """``autostart``: whether the session starts ProjectFlow; None when it cannot."""
        super().__init__("Notifications", parent)
        current = config if config is not None else PlanningPosteConfig()
        self._autostart_available = autostart is not None
        column = QVBoxLayout(self)
        column.setSpacing(14)
        if config is None:
            column.addWidget(
                label("Le planning n'est pas disponible sur ce poste.", "muted", wrap=True)
            )
        controls = QWidget()
        body = QVBoxLayout(controls)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)

        body.addWidget(label("Alertes du planning", "h5"))
        body.addWidget(
            label(
                "Une tâche prévient à l'heure choisie dans son « Alerte notification » "
                "(Détail de la tâche). Ces réglages sont propres à ce poste.",
                "muted",
                wrap=True,
            )
        )
        self.enabled_check = QCheckBox("Activer les alertes sur ce poste")
        self.enabled_check.setChecked(current.alerts_enabled)
        body.addWidget(self.enabled_check)
        self.audience_choice = SegmentedChoice(AUDIENCES, current.alerts_audience)
        body.addWidget(field("Prévenir pour", self.audience_choice, help_text=AUDIENCE_HELP))
        self.system_check = QCheckBox(
            f"Afficher aussi dans le centre de notifications de {SYSTEM_NAME}"
        )
        self.system_check.setChecked(current.alerts_system)
        body.addWidget(self.system_check)
        self.sound_check = QCheckBox("Signal sonore")
        self.sound_check.setChecked(current.alerts_sound)
        body.addWidget(self.sound_check)

        body.addWidget(label("Heures de silence", "h5"))
        self.quiet_check = QCheckBox("Ne rien afficher de")
        self.quiet_check.setChecked(current.alerts_quiet)
        self.quiet_from_edit = _time_edit(current.alerts_quiet_from)
        self.quiet_to_edit = _time_edit(current.alerts_quiet_to)
        body.addWidget(_row(self.quiet_check, self.quiet_from_edit, label("à"), self.quiet_to_edit))
        self.quiet_weekend_check = QCheckBox("Ni le samedi et le dimanche")
        self.quiet_weekend_check.setChecked(current.alerts_quiet_weekend)
        body.addWidget(self.quiet_weekend_check)
        body.addWidget(
            label(
                "Une alerte échue pendant le silence attend dans l'historique de la cloche, "
                "non lue.",
                "small",
                wrap=True,
            )
        )

        body.addWidget(label("Résumés", "h5"))
        self.morning_check = QCheckBox("« Ma journée » : les tâches du jour, à")
        self.morning_check.setChecked(current.alerts_morning)
        self.morning_edit = _time_edit(current.alerts_morning_time)
        body.addWidget(_row(self.morning_check, self.morning_edit))
        self.evening_check = QCheckBox("« À venir » : les tâches du prochain jour travaillé, à")
        self.evening_check.setChecked(current.alerts_evening)
        self.evening_edit = _time_edit(current.alerts_evening_time)
        body.addWidget(_row(self.evening_check, self.evening_edit))

        body.addWidget(label("Alerte des nouvelles tâches", "h5"))
        body.addWidget(
            label(
                "Donnée d'office à une tâche créée sur ce poste, selon son type ; elle se "
                "modifie ensuite dans le Détail.",
                "muted",
                wrap=True,
            )
        )
        self.default_combos: dict[str, QComboBox] = {}
        self._unknown_defaults = dict(current.alert_defaults)
        defaults = QFormLayout()
        defaults.setContentsMargins(0, 0, 0, 0)
        defaults.setVerticalSpacing(6)
        for key, name in task_types:
            combo = QComboBox()
            combo.setFixedWidth(DEFAULT_WIDTH)
            combo.addItem(NO_DEFAULT_TEXT, NO_DEFAULT)
            chosen = self._unknown_defaults.pop(key, NO_DEFAULT)
            choices = sorted({*BEFORE_PRESETS, chosen} - {NO_DEFAULT})
            for minutes in choices:
                combo.addItem(default_text(minutes), minutes)
            combo.setCurrentIndex(max(combo.findData(chosen), 0))
            combo.setAccessibleName(f"Alerte par défaut : {name}")
            self.default_combos[key] = combo
            defaults.addRow(name, combo)
        if task_types:
            body.addLayout(defaults)
        else:
            body.addWidget(
                label("Les types de tâche s'affichent une fois le planning chargé.", "small")
            )

        body.addWidget(label("Démarrage", "h5"))
        self.autostart_check = QCheckBox(AUTOSTART_TEXT)
        self.autostart_check.setChecked(bool(autostart))
        self.autostart_check.setEnabled(self._autostart_available)
        body.addWidget(self.autostart_check)
        body.addWidget(
            label(
                "Les alertes ne sonnent que si ProjectFlow tourne, fenêtre ouverte ou non."
                if self._autostart_available
                else AUTOSTART_UNAVAILABLE,
                "small",
                wrap=True,
            )
        )

        column.addWidget(controls)
        controls.setEnabled(config is not None)
        self._dependents = (
            self.audience_choice,
            self.system_check,
            self.sound_check,
            self.quiet_check,
            self.quiet_weekend_check,
            self.morning_check,
            self.evening_check,
        )
        for check in (
            self.enabled_check,
            self.quiet_check,
            self.morning_check,
            self.evening_check,
        ):
            check.toggled.connect(self._refresh)
        self._refresh()

    # -- contract ---------------------------------------------------------------

    def apply_to(self, config: PlanningPosteConfig) -> None:
        config.alerts_enabled = self.enabled_check.isChecked()
        audience = self.audience_choice.current()
        config.alerts_audience = "all" if audience == "all" else "mine"
        config.alerts_system = self.system_check.isChecked()
        config.alerts_sound = self.sound_check.isChecked()
        config.alerts_quiet = self.quiet_check.isChecked()
        config.alerts_quiet_from = self.quiet_from_edit.time().toString("HH:mm")
        config.alerts_quiet_to = self.quiet_to_edit.time().toString("HH:mm")
        config.alerts_quiet_weekend = self.quiet_weekend_check.isChecked()
        config.alerts_morning = self.morning_check.isChecked()
        config.alerts_morning_time = self.morning_edit.time().toString("HH:mm")
        config.alerts_evening = self.evening_check.isChecked()
        config.alerts_evening_time = self.evening_edit.time().toString("HH:mm")
        # The defaults of types this page did not show (planning not loaded) are kept.
        chosen = dict(self._unknown_defaults)
        for key, combo in self.default_combos.items():
            minutes = combo.currentData()
            if isinstance(minutes, int) and minutes != NO_DEFAULT:
                chosen[key] = minutes
        config.alert_defaults = chosen

    def autostart_wanted(self) -> bool | None:
        """Whether the session should start ProjectFlow; None when it cannot be set here."""
        return self.autostart_check.isChecked() if self._autostart_available else None

    def summary(self) -> str:
        """One word under the name of the page in the list of the settings."""
        if not self.enabled_check.isChecked():
            return "Coupées"
        return "Silence programmé" if self.quiet_check.isChecked() else "Actives"

    # -- internals --------------------------------------------------------------

    def _refresh(self) -> None:
        on = self.enabled_check.isChecked()
        for widget in self._dependents:
            widget.setEnabled(on)
        quiet = on and self.quiet_check.isChecked()
        self.quiet_from_edit.setEnabled(quiet)
        self.quiet_to_edit.setEnabled(quiet)
        self.quiet_weekend_check.setEnabled(quiet)
        self.morning_edit.setEnabled(on and self.morning_check.isChecked())
        self.evening_edit.setEnabled(on and self.evening_check.isChecked())
