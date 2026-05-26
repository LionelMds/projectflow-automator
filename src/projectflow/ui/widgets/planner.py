from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class PlannerBucketOption:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class PlannerMemberOption:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class PlannerTaskFormData:
    enabled: bool = False
    bucket_id: str = ""
    bucket_name: str = ""
    assignee_ids: tuple[str, ...] = ()
    assignee_labels: tuple[str, ...] = ()
    due_enabled: bool = False
    due_days: int = 7


class PlannerSelectionWidget(QGroupBox):
    options_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Microsoft Planner", parent)
        self._planner_available = False
        self._default_bucket = PlannerBucketOption(id="", name="")
        self._default_due_days = 7
        self._members: dict[str, PlannerMemberOption] = {}
        self._selected_member_ids: tuple[str, ...] = ()
        self._build_ui()
        self.set_config_defaults(
            enabled=False,
            bucket_id="",
            bucket_name="",
            due_days=7,
        )

    def data(self) -> PlannerTaskFormData:
        bucket_id = _selected_combo_id(self.bucket_combo)
        bucket_name = self.bucket_combo.currentText().strip() if bucket_id else ""
        return PlannerTaskFormData(
            enabled=self.enabled_checkbox.isEnabled() and self.enabled_checkbox.isChecked(),
            bucket_id=bucket_id,
            bucket_name=bucket_name,
            assignee_ids=self._selected_member_ids,
            assignee_labels=tuple(
                self._members[member_id].label
                for member_id in self._selected_member_ids
                if member_id in self._members
            ),
            due_enabled=self.due_checkbox.isChecked(),
            due_days=self.due_days_spin.value(),
        )

    def set_data(self, data: PlannerTaskFormData) -> None:
        self._select_bucket(data.bucket_id, data.bucket_name)
        self._selected_member_ids = data.assignee_ids
        for member_id, label in zip(data.assignee_ids, data.assignee_labels, strict=False):
            if member_id and member_id not in self._members:
                self._members[member_id] = PlannerMemberOption(id=member_id, label=label)
        self.enabled_checkbox.setChecked(data.enabled and self.enabled_checkbox.isEnabled())
        self.due_checkbox.setChecked(data.due_enabled)
        self.due_days_spin.setValue(data.due_days)
        self._refresh_member_label()
        self._refresh_enabled_state()

    def reset_fields(self) -> None:
        self.enabled_checkbox.setChecked(False)
        self._select_bucket(self._default_bucket.id, self._default_bucket.name)
        self._selected_member_ids = ()
        self.due_checkbox.setChecked(False)
        self.due_days_spin.setValue(self._default_due_days)
        self._refresh_member_label()
        self._refresh_enabled_state()

    def set_config_defaults(
        self,
        *,
        enabled: bool,
        bucket_id: str,
        bucket_name: str,
        due_days: int,
    ) -> None:
        self._planner_available = enabled
        self._default_bucket = PlannerBucketOption(id=bucket_id.strip(), name=bucket_name.strip())
        self._default_due_days = due_days if due_days > 0 else 7
        self.enabled_checkbox.setEnabled(enabled)
        self.load_options_button.setEnabled(enabled)
        self._seed_default_bucket()
        self.reset_fields()

    def set_options(
        self,
        *,
        buckets: Sequence[PlannerBucketOption],
        members: Sequence[PlannerMemberOption],
    ) -> None:
        current_bucket = _selected_combo_id(self.bucket_combo)
        self.bucket_combo.clear()
        for bucket in buckets:
            self.bucket_combo.addItem(bucket.name, bucket.id)
        if self._default_bucket.id and self.bucket_combo.findData(self._default_bucket.id) < 0:
            self.bucket_combo.addItem(
                self._default_bucket.name or self._default_bucket.id,
                self._default_bucket.id,
            )
        self._select_bucket(current_bucket or self._default_bucket.id, self._default_bucket.name)

        self._members = {member.id: member for member in members if member.id}
        self._selected_member_ids = tuple(
            member_id for member_id in self._selected_member_ids if member_id in self._members
        )
        self._refresh_member_label()

    def _build_ui(self) -> None:
        layout = QFormLayout(self)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(8)

        self.enabled_checkbox = QCheckBox("Creer une tache Planner")
        self.enabled_checkbox.toggled.connect(self._refresh_enabled_state)

        self.bucket_combo = QComboBox()
        self.bucket_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.load_options_button = QPushButton("Charger")
        self.load_options_button.clicked.connect(self.options_requested.emit)

        self.members_label = QLabel("Utilisateur connecte")
        self.members_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.members_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.members_button = QPushButton("Choisir")
        self.members_button.clicked.connect(self._select_members)

        self.due_checkbox = QCheckBox("Activer")
        self.due_checkbox.toggled.connect(self._refresh_enabled_state)
        self.due_days_spin = QSpinBox()
        self.due_days_spin.setRange(1, 365)
        self.due_days_spin.setSuffix(" jours")
        self.due_days_spin.setValue(self._default_due_days)

        layout.addRow("", self.enabled_checkbox)
        layout.addRow("Colonne", self._bucket_row())
        layout.addRow("Membres", self._members_row())
        layout.addRow("Echeance", self._due_row())

    def _bucket_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.bucket_combo, 1)
        layout.addWidget(self.load_options_button)
        return widget

    def _members_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.members_label, 1)
        layout.addWidget(self.members_button)
        return widget

    def _due_row(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.due_checkbox)
        layout.addWidget(self.due_days_spin, 1)
        return widget

    def _seed_default_bucket(self) -> None:
        self.bucket_combo.clear()
        if self._default_bucket.id:
            self.bucket_combo.addItem(
                self._default_bucket.name or self._default_bucket.id,
                self._default_bucket.id,
            )

    def _select_bucket(self, bucket_id: str, bucket_name: str = "") -> None:
        normalized_id = bucket_id.strip()
        if not normalized_id:
            self.bucket_combo.setCurrentIndex(0 if self.bucket_combo.count() else -1)
            return
        index = self.bucket_combo.findData(normalized_id)
        if index < 0:
            self.bucket_combo.addItem(bucket_name.strip() or normalized_id, normalized_id)
            index = self.bucket_combo.findData(normalized_id)
        if index >= 0:
            self.bucket_combo.setCurrentIndex(index)

    def _select_members(self) -> None:
        if not self._members:
            QMessageBox.information(
                self,
                "Planner",
                "Chargez d'abord les options Planner pour choisir les membres.",
            )
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Membres Planner")
        layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        list_widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        selected = set(self._selected_member_ids)
        for member in self._members.values():
            item = QListWidgetItem(member.label)
            item.setData(Qt.ItemDataRole.UserRole, member.id)
            item.setSelected(member.id in selected)
            list_widget.addItem(item)
        layout.addWidget(list_widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        selected_ids: list[str] = []
        for row in range(list_widget.count()):
            item = list_widget.item(row)
            if item is None or not item.isSelected():
                continue
            member_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(member_id, str):
                selected_ids.append(member_id)
        self._selected_member_ids = tuple(selected_ids)
        self._refresh_member_label()

    def _refresh_member_label(self) -> None:
        if not self._selected_member_ids:
            self.members_label.setText("Utilisateur connecte")
            return
        labels = [
            self._members[member_id].label
            for member_id in self._selected_member_ids
            if member_id in self._members
        ]
        self.members_label.setText(", ".join(labels) if labels else "Membres selectionnes")

    def _refresh_enabled_state(self) -> None:
        active = self._planner_available and self.enabled_checkbox.isChecked()
        self.bucket_combo.setEnabled(active)
        self.members_button.setEnabled(active)
        self.members_label.setEnabled(active)
        self.due_checkbox.setEnabled(active)
        self.due_days_spin.setEnabled(active and self.due_checkbox.isChecked())


def _selected_combo_id(combo: QComboBox) -> str:
    index = combo.currentIndex()
    if index < 0:
        return ""
    data = combo.currentData()
    if isinstance(data, str):
        current_text = combo.currentText().strip()
        if current_text == combo.itemText(index):
            return data.strip()
    return combo.currentText().strip()
