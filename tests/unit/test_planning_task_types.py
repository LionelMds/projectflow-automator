from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.changes import PlanningSnapshot
from projectflow.core.planning.commands import (
    CommandContext,
    PlanningCommandError,
    calendar_resolver,
    create_task,
    replace_task_types,
    retyped_tasks,
    update_task,
)
from projectflow.core.planning.models import (
    DEFAULT_TASK_TYPE,
    DEFAULT_TASK_TYPES,
    DEFAULT_TEAMS,
    Author,
    PlanningMeta,
    PlanningTask,
    TaskType,
    default_task_type_key,
)
from projectflow.core.planning.quick_entry import EntryContext, parse_entry
from projectflow.core.planning.task_types import (
    SIMPLE_TASK_TYPES,
    TYPE_PALETTE,
    dark_variant,
    new_type_key,
    next_palette_color,
    parse_keywords,
    simple_mapping,
)
from projectflow.ui.planning.colors import type_colors

AUTHOR = Author(initials="LM", name="Lionel Mottiez", poste="BUREAU-LM")
STAMP = "2026-10-01T08:00:00Z"
NOW = "2026-10-26T09:00:00Z"
CUSTOM = (
    TaskType(
        key="atelier",
        label="Atelier",
        color="#4f7ea8",
        color_dark="#78a6d1",
        keywords=("soudure", "debit"),
        team="atelier",
    ),
    TaskType(key="pose", label="Pose sur site", color="#4a925c", color_dark="#6fb880"),
)


def make_task(task_id: str, task_type: str, *, deleted: bool = False) -> PlanningTask:
    return PlanningTask.model_validate(
        {
            "id": task_id,
            "title": f"Tâche {task_id}",
            "task_type": task_type,
            "created_at": STAMP,
            "updated_at": STAMP,
            "deleted_at": STAMP if deleted else None,
        }
    )


def make_ctx(snapshot: PlanningSnapshot) -> CommandContext:
    return CommandContext(
        snapshot=snapshot, author=AUTHOR, now=NOW, calendar_for=calendar_resolver(snapshot)
    )


def detailed_snapshot() -> PlanningSnapshot:
    keys = [kind.key for kind in DEFAULT_TASK_TYPES]
    tasks = [make_task(f"t-{key}", key) for key in keys]
    tasks += [make_task("t-soudure-2", "soudure"), make_task("gone", "debit", deleted=True)]
    return PlanningSnapshot(tasks={task.id: task for task in tasks}, meta=PlanningMeta())


def entry_context(task_types: tuple[TaskType, ...]) -> EntryContext:
    return EntryContext(
        today=date(2026, 10, 22),
        week_start=date(2026, 10, 19),
        calendar=WorkCalendar(cantons=("GE",)),
        resources=(),
        teams=DEFAULT_TEAMS,
        task_types=task_types,
        find_project=lambda _number: None,
        default_year=2026,
    )


# -------------------------------------------------------------- defaults ----


def test_default_type_is_soudure_when_configured_else_the_first() -> None:
    assert default_task_type_key(DEFAULT_TASK_TYPES) == DEFAULT_TASK_TYPE
    assert default_task_type_key(CUSTOM) == "atelier"
    assert PlanningMeta(task_types=CUSTOM).default_task_type == "atelier"
    # An empty list never reaches the planning: the detailed types come back.
    assert PlanningMeta(task_types=()).task_types == DEFAULT_TASK_TYPES


def test_unknown_key_shows_as_the_default_type_of_the_list() -> None:
    meta = PlanningMeta(task_types=CUSTOM)
    assert meta.task_type("soudure").key == "atelier"
    assert meta.task_type("pose").label == "Pose sur site"
    colors = type_colors(meta, "livraison")
    assert (colors.stripe, colors.stripe_dark) == ("#4f7ea8", "#78a6d1")
    assert type_colors(meta, "pose").stripe == "#4a925c"


def test_commands_accept_only_the_configured_types() -> None:
    snapshot = PlanningSnapshot(meta=PlanningMeta(task_types=CUSTOM))
    ctx = make_ctx(snapshot)
    created = create_task(ctx, title="Pose", task_type="pose")
    assert created[0].after.task_type == "pose"  # type: ignore[union-attr]
    with pytest.raises(PlanningCommandError):
        create_task(ctx, title="Soudure", task_type="soudure")
    task = make_task("a", "atelier")
    ctx = make_ctx(replace(snapshot, tasks={"a": task}))
    with pytest.raises(PlanningCommandError):
        update_task(ctx, "a", task_type="montage")


# ---------------------------------------------------------- the helpers ----


def test_keys_keywords_and_colours() -> None:
    assert new_type_key("Atelier", ()) == "atelier"
    assert new_type_key("Atelier", ("atelier", "atelier2")) == "atelier3"
    assert new_type_key("Relevé / métré", ()) == "relevemetre"
    assert new_type_key("!!", ()) == "type"
    assert parse_keywords(" Pose, POSER ,, Échafaudage, pose ") == ("pose", "poser", "echafaudage")
    assert dark_variant("#4A925C") == "#6fb880"  # a palette pair
    dark = dark_variant("#203040")
    assert dark != "#203040"
    assert dark.startswith("#")
    assert len(dark) == len("#rrggbb")
    assert dark_variant("nope") == TYPE_PALETTE[-1][1]
    assert next_palette_color(CUSTOM) == TYPE_PALETTE[1]


# ---------------------------------------------------------------- preset ----


def test_simplify_maps_every_task_in_one_step() -> None:
    snapshot = detailed_snapshot()
    mapping = simple_mapping(kind.key for kind in DEFAULT_TASK_TYPES)
    retyped = retyped_tasks(snapshot, SIMPLE_TASK_TYPES, mapping)
    # « montage » keeps its key; the deleted task is left alone.
    assert "t-montage" not in retyped
    assert "gone" not in retyped
    assert len(retyped) == len(DEFAULT_TASK_TYPES)

    changes = replace_task_types(make_ctx(snapshot), SIMPLE_TASK_TYPES, mapping)
    after = snapshot.apply(changes)
    assert changes[0].kind == "meta"
    assert len(changes) == 1 + len(retyped)
    assert after.meta.task_types == SIMPLE_TASK_TYPES
    types = {task.id: task.task_type for task in after.tasks.values()}
    assert types == {
        "t-releve": "autre",
        "t-dessin": "autre",
        "t-debit": "atelier",
        "t-soudure": "atelier",
        "t-soudure-2": "atelier",
        "t-finition": "atelier",
        "t-montage": "montage",
        "t-livraison": "montage",
        "t-sav": "autre",
        "t-soustraitance": "autre",
        "t-interne": "autre",
        "gone": "debit",
    }
    assert after.meta.default_task_type == "atelier"


def test_replace_task_types_checks_the_list() -> None:
    ctx = make_ctx(detailed_snapshot())
    with pytest.raises(PlanningCommandError):
        replace_task_types(ctx, ())
    with pytest.raises(PlanningCommandError):
        replace_task_types(ctx, (CUSTOM[0], CUSTOM[0]))


# ----------------------------------------------------------- quick entry ----


def test_quick_entry_follows_the_configured_types() -> None:
    context = entry_context((*CUSTOM, SIMPLE_TASK_TYPES[2]))
    assert parse_entry("soudure portail", context).task_type == "atelier"
    assert parse_entry("garde-corps /pose", context).task_type == "pose"
    # The folded label works as a command too, and so does a prefix.
    assert parse_entry("garde-corps /posesursite", context).task_type == "pose"
    assert parse_entry("rangement /autre", context).task_type == "autre"
    assert parse_entry("rangement", context).task_type == "autre"
    # Nothing recognised: the default type of the list, not « soudure ».
    assert parse_entry("portail", context).task_type == "atelier"
    assert parse_entry("portail /montage", context).task_type == "atelier"
