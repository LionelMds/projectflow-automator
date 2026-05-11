from __future__ import annotations

from projectflow.app import _auto_sync_enabled, _demo_mode_enabled


def test_demo_mode_enabled_by_cli_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)

    assert _demo_mode_enabled(["projectflow", "--demo"]) is True


def test_demo_mode_enabled_by_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PROJECTFLOW_DEMO_MODE", "1")

    assert _demo_mode_enabled(["projectflow"]) is True


def test_demo_mode_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PROJECTFLOW_DEMO_MODE", raising=False)

    assert _demo_mode_enabled(["projectflow"]) is False


def test_auto_sync_disabled_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PROJECTFLOW_AUTO_SYNC", raising=False)

    assert _auto_sync_enabled() is False


def test_auto_sync_enabled_by_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PROJECTFLOW_AUTO_SYNC", "1")

    assert _auto_sync_enabled() is True
