from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from test_planning_service import (
    ME,
    MORNING,
    OTHER,
    FakeServer,
    FakeStore,
    Recorder,
    edited,
    loaded,
    new_service,
    new_task,
    remote_edit,
    task_of,
)

from projectflow.core.planning.cache import CacheContent, PlanningCache
from projectflow.core.planning.changes import DocumentChange
from projectflow.core.planning.models import PlanningTask
from projectflow.core.planning.service import UNREADABLE_SERVER_VERSION, PlanningService
from projectflow.core.planning.store import StoreUnavailableError
from projectflow.core.planning.sync import (
    AUTH_MESSAGE,
    FORBIDDEN_MESSAGE,
    FORBIDDEN_RETRY_SECONDS,
    MODE_INTERVALS,
    OFFLINE_MESSAGE,
    OFFLINE_PROBE_SECONDS,
    SENDING_MESSAGE,
    WRITE_FOLLOW_UP_SECONDS,
    SyncEngine,
    SyncMode,
    SyncStatus,
)
from projectflow.exceptions import AuthError, GraphError


async def until(condition: Callable[[], bool]) -> None:
    for _ in range(2000):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("La condition attendue n'est jamais arrivee.")


class WatchedCache(PlanningCache):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.saved = asyncio.Event()

    async def save(self, content: CacheContent) -> None:
        await super().save(content)
        self.saved.set()


async def two_postes(
    *tasks: PlanningTask,
) -> tuple[FakeServer, PlanningService, FakeStore, PlanningService, FakeStore]:
    server = FakeServer()
    for task in tasks:
        server.put(task)
    mine, my_store = await loaded(server, author=ME)
    theirs, their_store = await loaded(server, author=OTHER)
    return server, mine, my_store, theirs, their_store


# --------------------------------------------------------------- writes ----


@pytest.mark.asyncio
async def test_a_local_change_is_sent_then_followed_by_a_read() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    engine = SyncEngine(service, store)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")

    delay = await engine.sync_once()

    assert delay == WRITE_FOLLOW_UP_SECONDS
    assert store.calls[1:] == [("create", task.id), ("changes", "")]
    assert server.document("task", task.id) == task_of(service, task.id)
    assert service.etag("task", task.id) == server.items["task", task.id].etag
    assert service.pending == 0
    assert engine.status.state == "ok"
    assert engine.status.pending == 0


@pytest.mark.asyncio
async def test_coalesced_changes_are_sent_as_one_write() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    service.apply((edited(task_of(service, task.id), title="Garde-corps inox"),), label="Titre")
    service.apply((edited(task_of(service, task.id), urgent=True),), label="Urgent")

    await SyncEngine(service, store).sync_once()

    assert store.writes == ["create"]
    stored = server.document("task", task.id)
    assert (stored.title, stored.urgent) == ("Garde-corps inox", True)


@pytest.mark.asyncio
async def test_a_change_made_while_its_write_is_on_the_way_is_sent_after_it() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    engine = SyncEngine(service, store)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    store.gate = asyncio.Event()

    running = asyncio.ensure_future(engine.sync_once())
    await until(lambda: store.calls[-1] == ("create", task.id))
    service.apply((edited(task_of(service, task.id), title="Garde-corps inox"),), label="Titre")
    store.gate.set()
    await running

    assert store.writes == ["create", "update"]
    assert server.document("task", task.id).title == "Garde-corps inox"
    assert service.pending == 0


@pytest.mark.asyncio
async def test_a_rejected_document_does_not_block_the_others() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    engine = SyncEngine(service, store)
    refused, accepted = new_task("Refusée"), new_task("Acceptée")
    store.rejected.add(refused.id)
    service.apply(
        (DocumentChange.creation(refused), DocumentChange.creation(accepted)), label="Ajout"
    )

    delay = await engine.sync_once()

    assert server.live("task", accepted.id) is not None
    assert server.live("task", refused.id) is None
    assert service.pending == 1
    assert engine.status.state == "error"
    assert engine.status.message == (
        "Le serveur refuse une modification du planning (Element refuse par SharePoint.) : "
        "nouvel essai automatique."
    )
    assert delay == WRITE_FOLLOW_UP_SECONDS


@pytest.mark.asyncio
async def test_a_refused_write_is_retried_on_its_own_schedule_then_given_up() -> None:
    server = FakeServer()
    refused, other = new_task("Refusée"), new_task("Autre")
    server.put(refused)
    server.put(other)
    service, store = await loaded(server)
    now = [0.0]
    engine = SyncEngine(service, store, monotonic=lambda: now[0])
    engine.set_mode("visible")
    recorder = Recorder()
    service.subscribe(recorder)
    store.rejected.add(refused.id)
    service.apply((edited(task_of(service, refused.id), notes="Nacelle"),), label="Notes")
    remote_edit(server, refused.id, title="Renommée ailleurs")

    attempts: list[float] = []
    delays: list[float] = []
    while service.pending and now[0] < 60.0:
        tried = store.writes.count("update")
        remote_edit(server, other.id, notes=f"{now[0]:.0f} s")
        delays.append(await engine.sync_once())
        if store.writes.count("update") > tried:
            attempts.append(now[0])
        assert task_of(service, other.id).notes == f"{now[0]:.0f} s"
        now[0] += 1.0

    assert attempts == [0.0, 2.0, 6.0, 14.0, 30.0]
    assert max(delays) <= MODE_INTERVALS["visible"]
    assert engine.status.state == "ok"
    shown = task_of(service, refused.id)
    assert (shown.title, shown.notes) == ("Renommée ailleurs", "")
    [conflict] = recorder.of("conflict")
    assert conflict.message == (
        "Le serveur refuse d'enregistrer cette tâche (Element refuse par SharePoint.) : "
        "votre modification n'a pas été enregistrée."
    )
    assert conflict.restorable[0].notes == "Nacelle"


@pytest.mark.asyncio
async def test_an_unreadable_server_version_puts_back_the_last_readable_one() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    service.apply((edited(task_of(service, task.id), title="Inox"),), label="Titre")
    known = service.etag("task", task.id)
    newer = {**server.document("task", task.id).to_json(), "status": "cancelled"}
    server.record("task", task.id, newer)  # written by a newer ProjectFlow

    await SyncEngine(service, store).sync_once()

    assert task_of(service, task.id).title == "Garde-corps"
    assert service.pending == 0
    assert service.etag("task", task.id) == known
    assert [event.message for event in recorder.of("conflict")] == [UNREADABLE_SERVER_VERSION]


# ------------------------------------------------------------ conflicts ----


@pytest.mark.asyncio
async def test_a_conflict_on_different_fields_merges_both_changes() -> None:
    task = new_task()
    server, mine, my_store, theirs, their_store = await two_postes(task)
    theirs.apply((edited(task_of(theirs, task.id), notes="Nacelle"),), label="Notes")
    await SyncEngine(theirs, their_store).sync_once()
    recorder = Recorder()
    mine.subscribe(recorder)

    mine.apply((edited(task_of(mine, task.id), title="Garde-corps inox"),), label="Titre")
    await SyncEngine(mine, my_store).sync_once()

    stored = server.document("task", task.id)
    assert (stored.title, stored.notes) == ("Garde-corps inox", "Nacelle")
    assert stored.updated_by == ME
    assert task_of(mine, task.id) == stored
    assert my_store.writes == ["update", "get", "update"]
    assert mine.etag("task", task.id) == server.items["task", task.id].etag
    assert mine.pending == 0
    assert recorder.of("conflict") == []
    assert [event.author for event in recorder.of("remote")] == [OTHER]


@pytest.mark.asyncio
async def test_a_conflict_on_the_same_field_keeps_the_other_postes_value() -> None:
    task = new_task()
    server, mine, my_store, theirs, their_store = await two_postes(task)
    theirs.apply((edited(task_of(theirs, task.id), title="Inox"),), label="Titre")
    await SyncEngine(theirs, their_store).sync_once()
    recorder = Recorder()
    mine.subscribe(recorder)

    mine.apply((edited(task_of(mine, task.id), title="Acier"),), label="Titre")
    await SyncEngine(mine, my_store).sync_once()

    assert server.document("task", task.id).title == "Inox"
    assert task_of(mine, task.id).title == "Inox"
    assert my_store.writes == ["update", "get"]
    assert mine.pending == 0
    [conflict] = recorder.of("conflict")
    assert conflict.message == "PLA · ATELIER-1 a modifié cette tâche entre-temps"
    assert conflict.author == OTHER
    assert conflict.changed == frozenset({("task", task.id)})
    # « Réappliquer ma version » brings back the value this poste lost.
    [mine_version] = conflict.restorable
    assert mine_version.title == "Acier"
    mine.restore(mine_version, label="Réappliquer")
    await SyncEngine(mine, my_store).sync_once()
    assert server.document("task", task.id).title == "Acier"


@pytest.mark.asyncio
async def test_reapplying_a_lost_shift_keeps_the_other_postes_other_fields() -> None:
    """Shift by +60 min there, -60 min here: the lost version holds my dates, their notes."""
    start, end = MORNING[0] + 90, MORNING[1] - 90  # 08:00 → 10:30
    task = new_task(start=start, end=end)
    server, mine, my_store, theirs, their_store = await two_postes(task)
    later = (start + 60, end + 60)
    earlier = (start - 60, end - 60)
    theirs.apply(
        (edited(task_of(theirs, task.id), start=later[0], end=later[1], notes="Nacelle"),),
        label="Décaler",
    )
    await SyncEngine(theirs, their_store).sync_once()
    recorder = Recorder()
    mine.subscribe(recorder)

    mine.apply((edited(task_of(mine, task.id), start=earlier[0], end=earlier[1]),), label="Décaler")
    await SyncEngine(mine, my_store).sync_once()

    shown = task_of(mine, task.id)
    assert (shown.start, shown.end, shown.notes) == (*later, "Nacelle")
    [conflict] = recorder.of("conflict")
    [lost] = conflict.restorable
    assert (lost.start, lost.end, lost.notes) == (*earlier, "Nacelle")
    mine.restore(lost, label="Réappliquer")
    await SyncEngine(mine, my_store).sync_once()
    stored = server.document("task", task.id)
    assert (stored.start, stored.end, stored.notes) == (*earlier, "Nacelle")


@pytest.mark.asyncio
async def test_assignments_made_on_two_postes_are_merged_per_person() -> None:
    task = new_task(assignments={"r1": {}})
    server, mine, my_store, theirs, their_store = await two_postes(task)
    theirs.apply(
        (edited(task_of(theirs, task.id), assignments={"r1": {}, "r2": {}}),), label="Affecter"
    )
    await SyncEngine(theirs, their_store).sync_once()

    mine.apply((edited(task_of(mine, task.id), assignments={}),), label="Retirer")
    await SyncEngine(mine, my_store).sync_once()
    await SyncEngine(theirs, their_store).sync_once()

    assert server.document("task", task.id).assignee_ids == ("r2",)
    assert task_of(mine, task.id).assignee_ids == ("r2",)
    assert task_of(theirs, task.id).assignee_ids == ("r2",)


@pytest.mark.asyncio
async def test_a_creation_already_on_the_server_is_settled_without_a_new_write() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    server.put(task_of(service, task.id))  # sent before, but the answer was lost

    await SyncEngine(service, store).sync_once()

    assert store.writes == ["create", "get"]
    assert service.pending == 0
    assert service.etag("task", task.id) == server.items["task", task.id].etag
    assert recorder.of("conflict") == []


@pytest.mark.asyncio
async def test_documents_lost_by_a_restored_server_go_out_at_the_next_cycle() -> None:
    lost, kept = new_task("Perdue"), new_task("Gardée")
    server, mine, my_store, theirs, their_store = await two_postes(lost, kept)
    engine = SyncEngine(mine, my_store)
    server.forget("task", lost.id)
    my_store.upload_next = True

    await engine.sync_once()
    queued = engine.status.pending
    await engine.sync_once()
    await SyncEngine(theirs, their_store).sync_once()

    assert queued == 1
    assert my_store.writes == ["create"]
    assert engine.status == SyncStatus("ok", engine.status.last_success, 0)
    assert server.document("task", lost.id).title == "Perdue"
    assert set(theirs.snapshot.tasks) == {lost.id, kept.id}


@pytest.mark.asyncio
async def test_a_hard_deletion_elsewhere_wins_over_a_local_edit() -> None:
    task = new_task()
    server = FakeServer()
    server.put(task)
    service, store = await loaded(server)
    recorder = Recorder()
    service.subscribe(recorder)
    service.apply((edited(task_of(service, task.id), notes="Nacelle"),), label="Notes")
    server.remove("task", task.id)

    await SyncEngine(service, store).sync_once()

    assert store.writes == ["update", "get"]
    assert task.id not in service.snapshot.tasks
    assert service.pending == 0
    [conflict] = recorder.of("conflict")
    assert conflict.restorable[0].notes == "Nacelle"

    service.restore(conflict.restorable[0])
    await SyncEngine(service, store).sync_once()
    assert server.document("task", task.id).notes == "Nacelle"


@pytest.mark.asyncio
async def test_a_soft_deletion_elsewhere_wins_over_a_local_edit() -> None:
    task = new_task()
    server, mine, my_store, theirs, their_store = await two_postes(task)
    theirs.apply(
        (edited(task_of(theirs, task.id), deleted_at="2026-10-22T09:00:00Z", deleted_by=OTHER),),
        label="Supprimer",
    )
    await SyncEngine(theirs, their_store).sync_once()
    recorder = Recorder()
    mine.subscribe(recorder)

    mine.apply((edited(task_of(mine, task.id), title="Garde-corps inox"),), label="Titre")
    await SyncEngine(mine, my_store).sync_once()

    assert task_of(mine, task.id).deleted
    assert server.document("task", task.id).title == "Garde-corps"
    [conflict] = recorder.of("conflict")
    assert conflict.message == (
        "PLA · ATELIER-1 a supprimé cette tâche entre-temps : "
        "votre modification n'a pas été enregistrée."
    )
    assert conflict.restorable[0].title == "Garde-corps inox"


# -------------------------------------------------------------- status ----


@pytest.mark.asyncio
async def test_offline_makes_the_planning_read_only_until_the_store_answers() -> None:
    server = FakeServer()
    service, store = await loaded(server)
    clock = iter([100.0, 200.0])
    engine = SyncEngine(service, store, monotonic=lambda: next(clock))
    recorder = Recorder()
    service.subscribe(recorder)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    store.failure = StoreUnavailableError("Reseau indisponible.")

    assert await engine.sync_once() == OFFLINE_PROBE_SECONDS
    assert await engine.sync_once() == OFFLINE_PROBE_SECONDS
    assert engine.status.state == "offline"
    assert engine.read_only
    assert engine.status.pending == 1
    assert engine.status.last_success is None
    assert [event.message for event in recorder.of("status")] == [SENDING_MESSAGE, OFFLINE_MESSAGE]

    store.failure = None
    await engine.sync_once()

    assert engine.status.state == "ok"
    assert not engine.read_only
    assert engine.status.last_success == 100.0
    assert server.live("task", task.id) is not None
    assert service.pending == 0


@pytest.mark.asyncio
async def test_sign_in_problems_ask_for_a_new_connection() -> None:
    service, store = await loaded(FakeServer())
    engine = SyncEngine(service, store)
    error = StoreUnavailableError("Connexion Microsoft requise.")
    error.__cause__ = AuthError("Jeton expire.")
    store.failure = error

    await engine.sync_once()
    assert (engine.status.state, engine.status.message) == ("auth_required", AUTH_MESSAGE)
    assert engine.read_only

    store.failure = GraphError("Non autorise.", status_code=401)
    await engine.sync_once()
    assert engine.status.state == "auth_required"


@pytest.mark.asyncio
async def test_a_missing_permission_is_not_reported_as_offline() -> None:
    service, store = await loaded(FakeServer())
    engine = SyncEngine(service, store)
    error = StoreUnavailableError("Acces refuse.")
    error.__cause__ = GraphError("Access denied.", status_code=403)  # as GraphPlanningBackend
    store.failure = error

    assert await engine.sync_once() == FORBIDDEN_RETRY_SECONDS
    assert (engine.status.state, engine.status.message) == ("forbidden", FORBIDDEN_MESSAGE)
    assert engine.read_only


@pytest.mark.asyncio
async def test_other_errors_back_off_exponentially_up_to_a_minute() -> None:
    service, store = await loaded(FakeServer())
    engine = SyncEngine(service, store)
    store.failure = GraphError("Erreur du serveur.", status_code=500)

    delays = [await engine.sync_once() for _ in range(7)]

    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert engine.status.state == "error"
    assert not engine.read_only
    assert "Erreur du serveur." in engine.status.message

    store.failure = None
    assert await engine.sync_once() == MODE_INTERVALS["background"]
    store.failure = GraphError("Erreur du serveur.", status_code=500)
    assert await engine.sync_once() == 2.0


@pytest.mark.asyncio
async def test_the_cadence_follows_the_mode() -> None:
    service, store = await loaded(FakeServer())
    engine = SyncEngine(service, store)

    modes: tuple[SyncMode, ...] = ("visible", "background", "hidden", "idle")
    delays = {}
    for mode in modes:
        engine.set_mode(mode)
        delays[mode] = await engine.sync_once()

    assert delays == {"visible": 2.0, "background": 10.0, "hidden": 60.0, "idle": 120.0}
    service.apply((DocumentChange.creation(new_task()),), label="Nouvelle tâche")
    assert await engine.sync_once() == WRITE_FOLLOW_UP_SECONDS
    assert await engine.sync_once() == 120.0


# ---------------------------------------------------------- run and stop ----


@pytest.mark.asyncio
async def test_run_waits_between_cycles_and_stops() -> None:
    service, store = await loaded(FakeServer())
    delays: list[float] = []
    stopping: list[asyncio.Future[None]] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            stopping.append(asyncio.ensure_future(engine.stop()))

    engine = SyncEngine(service, store, sleep=sleep)
    engine.set_mode("visible")

    await asyncio.wait_for(engine.run(), 5)

    assert delays[:3] == [2.0, 2.0, 2.0]
    assert not engine.running
    await stopping[0]


@pytest.mark.asyncio
async def test_wake_mode_and_local_changes_interrupt_the_wait() -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server)
    pauses: list[float] = []

    async def sleep_forever(delay: float) -> None:
        pauses.append(delay)
        await asyncio.Event().wait()

    engine = SyncEngine(service, store, sleep=sleep_forever)
    engine.set_mode("idle")
    runner = asyncio.ensure_future(engine.run())
    await until(lambda: len(pauses) == 1)

    remote_edit(server, task.id, title="Garde-corps inox")
    engine.wake()
    await until(lambda: len(pauses) == 2)
    assert task_of(service, task.id).title == "Garde-corps inox"

    engine.set_mode("visible")
    await until(lambda: len(pauses) == 3)
    assert pauses == [120.0, 120.0, 2.0]

    service.apply((edited(task_of(service, task.id), urgent=True),), label="Urgent")
    await until(lambda: len(pauses) == 4)
    assert server.document("task", task.id).urgent
    assert pauses[-1] == WRITE_FOLLOW_UP_SECONDS

    await engine.stop()
    await runner
    assert not engine.running


@pytest.mark.asyncio
async def test_stop_sends_the_outbox(tmp_path: Path) -> None:
    server = FakeServer()
    cache = PlanningCache(tmp_path)
    service, store = await loaded(server, cache=cache)
    engine = SyncEngine(service, store)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")

    await engine.stop()

    assert server.live("task", task.id) is not None
    content = await cache.load()
    assert content.outbox == ()
    assert content.documents["task"][task.id].etag == server.items["task", task.id].etag


@pytest.mark.asyncio
async def test_stop_gives_up_after_its_timeout_and_keeps_the_outbox(tmp_path: Path) -> None:
    server = FakeServer()
    cache = PlanningCache(tmp_path)
    service, store = await loaded(server, cache=cache)
    engine = SyncEngine(service, store, flush_timeout=0.05)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    store.gate = asyncio.Event()

    await asyncio.wait_for(engine.stop(), 5)

    assert service.pending == 1
    content = await cache.load()
    assert [write.doc_id for write in content.outbox] == [task.id]


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_a_cycle_stuck_on_the_store(tmp_path: Path) -> None:
    server = FakeServer()
    cache = PlanningCache(tmp_path)
    service, store = await loaded(server, cache=cache)
    engine = SyncEngine(service, store, flush_timeout=0.05)
    store.gate = asyncio.Event()
    runner = asyncio.ensure_future(engine.run())
    await until(lambda: len(store.calls) == 2)
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")

    await asyncio.wait_for(engine.stop(), 5)

    content = await cache.load()
    assert [write.doc_id for write in content.outbox] == [task.id]
    store.gate.set()
    await asyncio.wait_for(runner, 5)
    assert server.live("task", task.id) is not None


@pytest.mark.asyncio
async def test_the_outbox_survives_a_restart(tmp_path: Path) -> None:
    server = FakeServer()
    service, store = new_service(server, cache=PlanningCache(tmp_path))
    store.failure = StoreUnavailableError("Reseau indisponible.")
    assert await service.load() is False
    task = new_task()
    service.apply((DocumentChange.creation(task),), label="Nouvelle tâche")
    service.apply((edited(task_of(service, task.id), notes="Nacelle"),), label="Notes")
    engine = SyncEngine(service, store)
    await engine.sync_once()
    assert engine.status.state == "offline"

    restarted, restarted_store = await loaded(server, cache=PlanningCache(tmp_path))

    assert task_of(restarted, task.id).notes == "Nacelle"
    assert restarted.pending == 1
    await SyncEngine(restarted, restarted_store).sync_once()
    assert server.document("task", task.id).notes == "Nacelle"
    assert restarted_store.writes == ["create"]
    assert restarted.pending == 0


@pytest.mark.asyncio
async def test_a_pending_purge_survives_a_restart(tmp_path: Path) -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    service, store = await loaded(server, cache=PlanningCache(tmp_path))
    current = task_of(service, task.id)
    service.apply(
        (DocumentChange(kind="task", doc_id=task.id, before=current, after=None),), label="Purger"
    )
    store.failure = StoreUnavailableError("Reseau indisponible.")
    await SyncEngine(service, store).sync_once()

    restarted, restarted_store = await loaded(server, cache=PlanningCache(tmp_path))
    assert restarted.pending == 1
    await SyncEngine(restarted, restarted_store).sync_once()

    assert restarted_store.writes == ["delete"]
    assert server.live("task", task.id) is None
    assert restarted.pending == 0


@pytest.mark.asyncio
async def test_a_local_change_is_on_disk_while_a_cycle_waits_on_the_store(tmp_path: Path) -> None:
    server = FakeServer()
    task = new_task()
    server.put(task)
    cache = WatchedCache(tmp_path)
    service, store = await loaded(server, cache=cache)
    engine = SyncEngine(service, store)
    store.gate = asyncio.Event()  # the request hangs until the HTTP timeout
    runner = asyncio.ensure_future(engine.run())
    await until(lambda: len(store.calls) == 2)
    cache.saved.clear()

    service.apply((edited(task_of(service, task.id), notes="Nacelle"),), label="Notes")
    await asyncio.wait_for(cache.saved.wait(), 5)

    content = await PlanningCache(tmp_path).load()
    assert [write.doc_id for write in content.outbox] == [task.id]
    runner.cancel()  # ProjectFlow is killed
    await asyncio.gather(runner, return_exceptions=True)
