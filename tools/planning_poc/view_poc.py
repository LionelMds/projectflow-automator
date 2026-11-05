"""Planning grid prototype (Lot 0): CLI, screenshot and benchmark.

Architecture under test (MVP plan): the body is one QGraphicsView/QGraphicsScene
whose bars are hand-painted QGraphicsObjects (no nested widgets); the time header
and the row column are two light QWidgets painted by hand and kept in sync with
the body's scroll bars; one pure-Python ``TimeScale`` serves all three.

    timescale.py   working calendar (GE/VD holidays, no weekends), snapping, lanes
    model.py       rows, tasks and the seeded demo generator
    grid.py        scene, bars, gesture state machine, view
    window.py      header, row column, toolbar window, A3 PDF export
    view_poc.py    this CLI

    py tools/planning_poc/view_poc.py [--view day|week|month] [--rows 40] [--days 92]
        [--bench] [--screenshot PATH] [--dark] [--start AAAA-MM-JJ] [--goto AAAA-MM-JJ]

Keys: J/1 day, S/2 week, M/3 month, T today, Left/Right previous/next period,
Ctrl+wheel zoom, Shift+wheel horizontal scroll, Shift while dragging = 30 min snap
in month view, Escape cancels a drag. No sign-in and no network call.
"""

# ruff: noqa: E402
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parents[1] / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import argparse
import json
import math
import os
import platform
import random
import statistics
import tempfile
import time as clock
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

from grid import (
    BarItem,
    PlanningScene,
    PlanningView,
    ViewKind,
)
from model import Plan, generate_plan
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import (
    QEvent,
    QPointF,
    Qt,
    qVersion,
)
from PySide6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
)
from PySide6.QtWidgets import QApplication, QWidget
from timescale import (
    SNAP_MINUTES,
    WorkCalendar,
    assign_lanes,
)
from window import (
    PlanningWindow,
    export_week_pdf,
)

from projectflow.config import AppearanceConfig
from projectflow.ui.theme import apply_theme

# ------------------------------------------------------------------ cli ----


def _application(*, dark: bool) -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv[:1])
    apply_theme(app, AppearanceConfig(mode="dark" if dark else "light", palette="acier"))
    return app


def local_now() -> datetime:
    """Wall-clock time of this workstation, naive like the planning data."""
    return datetime.now(UTC).astimezone().replace(tzinfo=None, second=0, microsecond=0)


def default_start(today: date) -> date:
    """Monday on or before the first day of the previous month."""
    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    return first - timedelta(days=first.weekday())


def build_window(
    plan: Plan,
    calendar: WorkCalendar,
    now: datetime,
    kind: ViewKind,
    goto: date | None = None,
) -> tuple[PlanningWindow, float]:
    started = clock.perf_counter()
    scene = PlanningScene(plan, calendar, now)
    build_ms = (clock.perf_counter() - started) * 1000
    window = PlanningWindow(scene)
    window.resize(1440, 900)
    window.show()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    window.set_view(kind, calendar.index_at_or_after(goto or now.date()))
    return window, build_ms


def _stats(samples: Sequence[float], unit: str = "ms") -> dict[str, float]:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    return {
        f"mean_{unit}": round(statistics.fmean(ordered), 3),
        f"p95_{unit}": round(p95, 3),
        f"max_{unit}": round(ordered[-1], 3),
    }


def _timed(action: Callable[[], object]) -> float:
    started = clock.perf_counter()
    action()
    return (clock.perf_counter() - started) * 1000


def _render_samples(widget: QWidget, count: int, warmup: int = 3) -> list[float]:
    """Milliseconds per full repaint of ``widget`` into an offscreen image."""
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    for _ in range(warmup):
        widget.render(image)
    samples: list[float] = []
    for _ in range(count):
        started = clock.perf_counter()
        widget.render(image)
        samples.append((clock.perf_counter() - started) * 1000)
    return samples


def _bench_repaint(window: PlanningWindow, app: QApplication) -> dict[str, object]:
    results: dict[str, object] = {}
    today = window.calendar.index_at_or_after(window.scene.now.date())
    for kind in ViewKind:
        started = clock.perf_counter()
        window.set_view(kind, today)
        switch_ms = (clock.perf_counter() - started) * 1000
        app.processEvents()
        viewport = window.view.viewport()
        samples = _render_samples(viewport, 50)
        header_ms = _render_samples(window.header, 20)
        column_ms = _render_samples(window.column, 20)
        results[kind.value] = {
            "viewport": [viewport.width(), viewport.height()],
            "day_width_px": round(window.scene.scale.day_width, 1),
            "scene_size": [round(window.scene.scale.width), window.scene.layout.height],
            "visible_bars": len(window.visible_bars()),
            "switch_view_ms": round(switch_ms, 2),
            **_stats(samples),
            "header_paint_mean_ms": round(statistics.fmean(header_ms), 3),
            "column_paint_mean_ms": round(statistics.fmean(column_ms), 3),
        }
    return results


def _bench_hit_tests(window: PlanningWindow, count: int = 2000) -> dict[str, object]:
    """Point lookups spread over the whole scene, once per view (scene size differs)."""
    results: dict[str, object] = {}
    today = window.calendar.index_at_or_after(window.scene.now.date())
    for kind in ViewKind:
        window.set_view(kind, today)
        scene = window.scene
        rng = random.Random(7)
        rect = scene.sceneRect()
        points = [
            QPointF(rng.uniform(0, rect.width()), rng.uniform(0, rect.height()))
            for _ in range(count)
        ]
        samples: list[float] = []
        hits = 0
        for point in points:
            started = clock.perf_counter()
            items = scene.items(point)
            samples.append((clock.perf_counter() - started) * 1_000_000)
            hits += any(isinstance(item, BarItem) for item in items)
        results[kind.value] = {
            "scene_size": [round(rect.width()), round(rect.height())],
            "count": count,
            "total_ms": round(sum(samples) / 1000, 2),
            **_stats(samples, "us"),
            "hits": hits,
        }
    return results


def _mouse(kind: QEvent.Type, view: PlanningView, pos: QPointF, *, held: bool) -> QMouseEvent:
    button_flag = (
        Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    )
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    global_pos = QPointF(view.viewport().mapToGlobal(pos.toPoint()))
    return QMouseEvent(kind, pos, global_pos, button_flag, buttons, Qt.KeyboardModifier.NoModifier)


def _bench_drag(window: PlanningWindow, app: QApplication, moves: int = 200) -> dict[str, object]:
    window.set_view(ViewKind.WEEK)
    app.processEvents()
    view = window.view
    viewport = view.viewport()
    bar = window.select_sample_bar()
    if bar is None:
        return {"error": "aucune barre visible"}
    origin_task = bar.task
    center = QPointF(view.mapFromScene(bar.rect().center()))
    row_height = window.scene.metrics.lane + 2 * window.scene.metrics.padding
    QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseButtonPress, view, center, held=True))
    handler: list[float] = []
    painted: list[float] = []
    previews: set[tuple[int, int, int]] = set()
    for step in range(moves):
        offset = QPointF(
            320 * math.sin(2 * math.pi * step / 100),
            2.5 * row_height * math.sin(2 * math.pi * step / 67),
        )
        event = _mouse(QEvent.Type.MouseMove, view, center + offset, held=True)
        drag = view.controller.drag
        before = None if drag is None else drag.preview
        started = clock.perf_counter()
        QApplication.sendEvent(viewport, event)
        middle = clock.perf_counter()
        app.processEvents()
        finished = clock.perf_counter()
        handler.append((middle - started) * 1000)
        drag = view.controller.drag
        if drag is not None:
            previews.add(drag.preview)
            # Only moves that changed the snapped preview repaint anything.
            if drag.preview != before:
                painted.append((finished - started) * 1000)
    last = center + QPointF(150, 0)
    QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseMove, view, last, held=True))
    release = _mouse(QEvent.Type.MouseButtonRelease, view, last, held=False)
    commit_ms = _timed(lambda: QApplication.sendEvent(viewport, release))
    app.processEvents()
    moved_task = window.scene.plan.tasks[origin_task.id]
    return {
        "moves": moves,
        "distinct_previews": len(previews),
        "handler": _stats(handler),
        "repainting_moves": len(painted),
        "handler_plus_repaint": _stats(painted),
        "release_commit_relayout_ms": round(commit_ms, 2),
        "committed": moved_task != origin_task,
        "start_on_half_hour": moved_task.start.minute % SNAP_MINUTES == 0,
        "work_minutes_kept": window.calendar.work_between(*window.scene.span_of(moved_task))
        == window.calendar.work_between(*window.scene.span_of(origin_task)),
        "escape_restores_origin": _check_escape(window, app),
    }


def _check_escape(window: PlanningWindow, app: QApplication) -> bool:
    view = window.view
    viewport = view.viewport()
    bar = window.select_sample_bar()
    if bar is None:
        return False
    origin = bar.rect()
    task = bar.task
    center = QPointF(view.mapFromScene(origin.center()))
    QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseButtonPress, view, center, held=True))
    for step in range(1, 6):
        target = center + QPointF(60 * step, 30 * step)
        QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseMove, view, target, held=True))
    moved = bar.rect() != origin
    escape = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view, escape)
    restored = bar.rect() == origin
    end = center + QPointF(400, 0)
    QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseMove, view, end, held=True))
    still = bar.rect() == origin
    QApplication.sendEvent(viewport, _mouse(QEvent.Type.MouseButtonRelease, view, end, held=False))
    app.processEvents()
    return moved and restored and still and window.scene.plan.tasks[task.id] == task


def _bench_pdf(window: PlanningWindow) -> dict[str, object]:
    path = Path(tempfile.mkdtemp(prefix="planning_poc_")) / "planning_semaine.pdf"
    today = window.calendar.index_at_or_after(window.scene.now.date())
    started = clock.perf_counter()
    pages = export_week_pdf(window, path, today)
    export_ms = (clock.perf_counter() - started) * 1000
    return {
        "ms": round(export_ms, 1),
        "pages": pages,
        "bytes": path.stat().st_size,
        "path": str(path),
    }


def _machine(app: QApplication) -> dict[str, object]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "pyside6": pyside_version,
        "qt": qVersion(),
        "qpa": app.platformName(),
    }


def _full_range_lanes(plan: Plan, scene: PlanningScene) -> list[int]:
    """Lanes per row if every bar of the loaded range counted (the former row sizing)."""
    spans: dict[str, list[tuple[int, int]]] = {item.id: [] for item in plan.resources}
    for task in plan.tasks.values():
        for owner in task.resource_ids:
            spans[owner].append(scene.span_of(task))
    return [assign_lanes(items)[1] for items in spans.values()]


def run_bench(args: argparse.Namespace, app: QApplication) -> dict[str, object]:
    started = clock.perf_counter()
    calendar = WorkCalendar(args.start, args.days)
    plan = generate_plan(calendar, args.rows, args.seed)
    data_ms = (clock.perf_counter() - started) * 1000
    now = local_now()
    window, build_ms = build_window(plan, calendar, now, ViewKind(args.view))
    full_range = _full_range_lanes(plan, window.scene)
    in_view = [row.lanes for row in window.scene.layout.rows if row.resource is not None]
    result: dict[str, object] = {
        "machine": _machine(app),
        "data": {
            "rows": len(plan.resources),
            "calendar_days": args.days,
            "displayed_days": len(calendar),
            "holidays": {str(day): item.name for day, item in calendar.holidays.items()},
            "tasks": len(plan.tasks),
            "bars": plan.bar_count,
            "shared_tasks": sum(len(task.resource_ids) > 1 for task in plan.tasks.values()),
            "rows_with_overlaps_full_range": sum(count > 1 for count in full_range),
            "max_lanes_full_range": max(full_range),
            f"rows_taller_in_{args.view}_shown": sum(count > 1 for count in in_view),
            f"max_lanes_in_{args.view}_shown": max(in_view),
            "generation_ms": round(data_ms, 2),
        },
        "scene_build_ms": round(build_ms, 2),
        "relayout_ms": round(_timed(window.scene.relayout), 2),
        "repaint": _bench_repaint(window, app),
        "hit_test": _bench_hit_tests(window),
        "drag": _bench_drag(window, app),
        "pdf_a3_week": _bench_pdf(window),
    }
    window.close()
    return result


def take_screenshot(args: argparse.Namespace, path: Path) -> Path:
    calendar = WorkCalendar(args.start, args.days)
    plan = generate_plan(calendar, args.rows, args.seed)
    now = local_now()
    window, _build_ms = build_window(plan, calendar, now, ViewKind(args.view), args.goto)
    window.select_sample_bar()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(path)):
        raise OSError(f"Impossible d'enregistrer la capture {path}.")
    window.close()
    return path


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype de la grille Planning (Lot 0).")
    parser.add_argument("--view", choices=[kind.value for kind in ViewKind], default="week")
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--days", type=int, default=92, help="jours calendaires affiches")
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="AAAA-MM-JJ")
    parser.add_argument("--goto", type=date.fromisoformat, default=None, help="date a afficher")
    parser.add_argument("--seed", type=int, default=118)
    parser.add_argument("--bench", action="store_true", help="mesures JSON (hors ecran)")
    parser.add_argument("--screenshot", type=Path, default=None, help="capture PNG puis sortie")
    parser.add_argument("--dark", action="store_true", help="theme sombre")
    args = parser.parse_args(argv)
    if args.start is None:
        args.start = default_start(date.today())
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    app = _application(dark=args.dark)
    if args.screenshot is not None:
        print(take_screenshot(args, args.screenshot))
    if args.bench:
        print(json.dumps(run_bench(args, app), indent=2))
    if args.screenshot is not None or args.bench:
        return 0
    calendar = WorkCalendar(args.start, args.days)
    plan = generate_plan(calendar, args.rows, args.seed)
    kind = ViewKind(args.view)
    window, _build_ms = build_window(plan, calendar, local_now(), kind, args.goto)
    window.view.setFocus()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
