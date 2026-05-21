from __future__ import annotations

from projectflow.app import _application_icon_path, _demo_mode_enabled


def test_demo_mode_enabled_by_cli_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)

    assert _demo_mode_enabled(["projectflow", "--demo"]) is True


def test_demo_mode_enabled_by_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PROJECTFLOW_DEMO_MODE", "1")

    assert _demo_mode_enabled(["projectflow"]) is True


def test_demo_mode_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)

    assert _demo_mode_enabled(["projectflow"]) is False


def test_application_icon_is_bundled() -> None:
    icon_path = _application_icon_path()

    assert icon_path is not None
    assert icon_path.name == "icon.png"
    assert icon_path.exists()
