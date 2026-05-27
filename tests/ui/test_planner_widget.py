from __future__ import annotations

from typing import Any

from projectflow.ui.widgets.planner import (
    PlannerBucketOption,
    PlannerMemberOption,
    PlannerSelectionWidget,
    PlannerTaskFormData,
)


def test_planner_widget_autoloads_when_enabled(qtbot: Any) -> None:
    widget = PlannerSelectionWidget()
    qtbot.addWidget(widget)
    emitted: list[str] = []
    widget.options_requested.connect(lambda: emitted.append("load"))
    widget.set_config_defaults(enabled=True, bucket_id="", bucket_name="", due_days=7)

    widget.enabled_checkbox.click()

    assert emitted == ["load"]
    assert widget.members_label.text() == "Chargement Planner..."
    assert widget.load_options_button.isHidden()


def test_planner_widget_does_not_autoload_when_data_is_set_programmatically(qtbot: Any) -> None:
    widget = PlannerSelectionWidget()
    qtbot.addWidget(widget)
    widget.set_config_defaults(
        enabled=True,
        bucket_id="bucket-a",
        bucket_name="A faire",
        due_days=7,
    )
    emitted: list[str] = []
    widget.options_requested.connect(lambda: emitted.append("load"))

    widget.set_data(PlannerTaskFormData(enabled=True, bucket_id="bucket-a", bucket_name="A faire"))

    assert emitted == []


def test_planner_widget_autoloads_when_bucket_popup_opens(qtbot: Any) -> None:
    widget = PlannerSelectionWidget()
    qtbot.addWidget(widget)
    emitted: list[str] = []
    widget.options_requested.connect(lambda: emitted.append("load"))
    widget.set_config_defaults(enabled=True, bucket_id="", bucket_name="", due_days=7)
    widget.enabled_checkbox.setChecked(True)
    emitted.clear()
    widget.set_options_error()

    widget.bucket_combo.showPopup()

    assert emitted == ["load"]


def test_planner_widget_accepts_options_after_background_load(qtbot: Any) -> None:
    widget = PlannerSelectionWidget()
    qtbot.addWidget(widget)
    widget.set_config_defaults(enabled=True, bucket_id="", bucket_name="", due_days=7)
    widget.enabled_checkbox.click()

    widget.set_options(
        buckets=[PlannerBucketOption(id="bucket-a", name="A faire")],
        members=[PlannerMemberOption(id="user-a", label="Alice Balz <alice@example.test>")],
    )

    assert widget.bucket_combo.currentText() == "A faire"
    assert widget.members_label.text() == "Utilisateur connecte"
    assert widget.members_button.isEnabled()
