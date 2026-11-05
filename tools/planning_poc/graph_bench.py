r"""Graph benchmark of the shared planning stored in a SharePoint list (Lot 0 PoC).

Not packaged. Run from the repository root with the system Python (``py`` on Windows);
the script adds ``src`` to ``sys.path`` itself. Every command prints a summary and writes
a JSON report to ``data_dir()/planning-poc/report-<command>-<YYYYmmdd-HHMMSS>.json``
(on Windows ``%LOCALAPPDATA%\\Balz Metal Sa\\ProjectFlow\\planning-poc``); the resolved
site and list ids are cached in ``state.json`` next to the reports. Ctrl+C stops a
command and still prints and saves the partial results. Exit codes: 0 measured, 1 error
(Microsoft, configuration, sign-in), 2 unexpected error, 3 measured but the expected
behaviour failed (ECHEC line in the summary), 130 interrupted.

One poste::

    py tools/planning_poc/graph_bench.py setup --create
    py tools/planning_poc/graph_bench.py fields-check
    py tools/planning_poc/graph_bench.py conflict
    py tools/planning_poc/graph_bench.py latency --writes 100 --interval 3 --poll 2
    py tools/planning_poc/graph_bench.py load --pollers 8 --poll 2 --minutes 3
    py tools/planning_poc/graph_bench.py cleanup

``setup`` derives the site from the Planner plan configured in ProjectFlow on this poste;
``--site-url https://<tenant>.sharepoint.com/sites/<site>`` or ``--group-id <id>`` choose
another site. Add ``--interactive`` to any command the first time (see Authentication).

Two postes (each one runs ``setup`` once, which finds the same list by its name)::

    poste B: py tools/planning_poc/graph_bench.py pingpong --role pong --poll 2
    poste A: py tools/planning_poc/graph_bench.py pingpong --role ping --rounds 50 --poll 2
    both   : py tools/planning_poc/graph_bench.py cleanup     (once, at the end)

The round trip is measured on poste A's clock only, so the postes' clocks may differ;
pong stops after ``--idle-timeout`` seconds without a new ping.

Authentication: the script uses the installed app's Microsoft connection. The client id
comes from ``ApplicationSettings.load()`` (embedded settings, else
PROJECTFLOW_MICROSOFT_CLIENT_ID, else the default id) and ``MsalAccessTokenProvider``
uses the default ``TokenCacheStorage``: the Windows Credential Manager entry
"ProjectFlow Automator" / "microsoft-token-cache" or, since a Microsoft cache exceeds
the Credential Manager limit, ``data_dir()/msal-cache.json``. The script therefore runs
as the shared account signed in to ProjectFlow on this poste, without a new sign-in.
Sites.ReadWrite.All (and Sites.Manage.All for ``setup --create``) is a new permission
for that account: until it is consented, the silent token request fails and the command
stops with "Connexion Microsoft requise". ``--interactive`` then opens the browser once,
preselecting the shared account, to consent. Choose that same account: the sign-in keeps
only the chosen account in the shared cache, so another one would also switch the
installed app. If the tenant forbids user consent, Microsoft answers AADSTS65001 or
AADSTS90094: an administrator must add the permission to the ProjectFlow Automator app
registration and grant admin consent. The installed app keeps working meanwhile: it
reloads the shared cache before every silent refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import platform
import random
import statistics
import sys
import time
import traceback
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx
import structlog

from projectflow.application_settings import ApplicationSettings
from projectflow.auth.msal_client import (
    PLANNER_GRAPH_SCOPES,
    PLANNING_GRAPH_SCOPES,
    AccessTokenProvider,
    MsalAccessTokenProvider,
)
from projectflow.config import AppConfig
from projectflow.exceptions import AuthError, ConfigError, GraphError, ProjectFlowError
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import _planner_plan_group_id
from projectflow.graph.planning_store import (
    DATA_COLUMN,
    FIELDS_EXPAND,
    GraphListPlanningStore,
    PlanningConflictError,
    PlanningListRef,
    PlanningRecord,
    create_planning_list,
    find_list,
    group_site_id,
    manual_list_instructions,
    planning_column_problems,
    site_id_from_url,
)
from projectflow.platform.paths import data_dir

POC_DIR_NAME = "planning-poc"
DEFAULT_LIST_NAME = "ProjectFlow Planning PoC"
MANAGE_SCOPES = ("Sites.Manage.All",)
# Shared-account budget given for the PoC: requests per user and per 5 minutes.
BUDGET_REQUESTS = 3000
BUDGET_WINDOW_SECONDS = 300
PING_DOC_ID = "pingpong-a"
PONG_DOC_ID = "pingpong-b"
VISIBILITY_POLL_SECONDS = 2.0
PROGRESS_SECONDS = 15.0
EXIT_FAILED_CHECK = 3
# Delta forms tried by fields-check, the store's own first.
DELTA_VARIANTS: tuple[tuple[str, str], ...] = (
    ("expand_select", FIELDS_EXPAND),
    ("expand_fields", "fields"),
    ("no_expand", ""),
)
# Planning documents hold French names and free text: SharePoint must return them unchanged.
SPECIAL_TEXT = (
    "Équipe Genève « Vaud » àéèêëîïôûüç ÀÉÈ € <b>soudure</b> & \"devis\" 'n°' \\ "
    "ligne 1\nligne 2\ttab"
)
AUTH_HINT = (
    "Astuce: relancez la commande avec --interactive pour accorder l'autorisation "
    "Sites.ReadWrite.All au compte partage dans le navigateur (une seule fois)."
)


def say(text: str = "") -> None:
    print(text, flush=True)


def poc_dir() -> Path:
    return data_dir() / POC_DIR_NAME


def state_path() -> Path:
    return poc_dir() / "state.json"


def load_state() -> dict[str, Any]:
    try:
        payload = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_state(state: Mapping[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def require_list(state: Mapping[str, Any]) -> PlanningListRef:
    site_id = state.get("site_id")
    list_id = state.get("list_id")
    if not isinstance(site_id, str) or not site_id or not isinstance(list_id, str) or not list_id:
        raise ConfigError(
            "Liste SharePoint du PoC non configuree sur ce poste: lancez d'abord "
            "« py tools/planning_poc/graph_bench.py setup ».",
        )
    return PlanningListRef(site_id=site_id, list_id=list_id)


def distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p95": None, "max": None}
    if len(values) == 1:
        p50 = p95 = values[0]
    else:
        quantiles = statistics.quantiles(values, n=100, method="inclusive")
        p50, p95 = quantiles[49], quantiles[94]
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(p50, 3),
        "p95": round(p95, 3),
        "max": round(max(values), 3),
    }


def describe(values: Mapping[str, float | int | None]) -> str:
    if not values.get("count"):
        return "aucune mesure"
    return (
        f"P50 {values['p50']} s, P95 {values['p95']} s, max {values['max']} s, "
        f"moyenne {values['mean']} s (n={values['count']})"
    )


@dataclass
class HttpStats:
    calls: int = 0
    responses: int = 0
    statuses: Counter[int] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)
    call_durations: list[float] = field(default_factory=list)

    async def on_response(self, response: httpx.Response) -> None:
        self.responses += 1
        self.statuses[response.status_code] += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph_calls": self.calls,
            "http_responses": self.responses,
            "status_counts": {str(code): count for code, count in sorted(self.statuses.items())},
            "throttled_429": self.statuses[429],
            # GraphClient replays 429/5xx (and a 401 once): extra responses are those replays.
            "retries": max(0, self.responses - self.calls),
            "errors": dict(self.errors),
            "call_duration_s": distribution(self.call_durations),
        }


class CountingGraphClient(GraphClient):
    """GraphClient that counts logical calls and every HTTP response, retries included."""

    def __init__(
        self,
        *,
        token_provider: AccessTokenProvider,
        stats: HttpStats,
        timeout: float,
    ) -> None:
        super().__init__(
            token_provider=token_provider,
            http_client=httpx.AsyncClient(event_hooks={"response": [stats.on_response]}),
            request_timeout=timeout,
        )
        self.stats = stats

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.stats.calls += 1
        started = time.perf_counter()
        try:
            return await super().request(method, path, json=json, headers=headers)
        except GraphError as exc:
            self.stats.errors[f"{method} {exc.status_code}"] += 1
            raise
        finally:
            self.stats.call_durations.append(time.perf_counter() - started)


@dataclass
class Report:
    command: str
    arguments: dict[str, Any]
    started: float = field(default_factory=time.time)
    results: dict[str, Any] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)
    http: dict[str, HttpStats] = field(default_factory=dict)
    finalizers: list[Callable[[], None]] = field(default_factory=list)
    interrupted: bool = False
    failed: bool = False
    error: str = ""

    def http_stats(self, label: str) -> HttpStats:
        return self.http.setdefault(label, HttpStats())

    def finish(self) -> Path | None:
        for finalizer in self.finalizers:
            try:
                finalizer()
            except Exception as exc:  # noqa: BLE001 - partial results must still be saved
                self.summary.append(f"(resume incomplet: {exc!r})")
        say()
        say(f"=== Resume {self.command} ===")
        if self.interrupted:
            say("Interrompu (Ctrl+C): resultats partiels.")
        if self.error:
            say(f"Erreur: {self.error}")
        for line in self.summary:
            say(line)
        for label, stats in self.http.items():
            values = stats.as_dict()
            say(
                f"HTTP {label}: {values['graph_calls']} appels Graph, "
                f"{values['http_responses']} reponses, 429: {values['throttled_429']}, "
                f"rejeux: {values['retries']}, erreurs: {values['errors'] or 'aucune'}, "
                f"duree {describe(values['call_duration_s'])}",
            )
        return self._write()

    def _write(self) -> Path | None:
        finished = time.time()
        stamp = datetime.fromtimestamp(self.started).astimezone()
        payload = {
            "command": self.command,
            "arguments": self.arguments,
            "host": platform.node(),
            "python": platform.python_version(),
            "started_at": stamp.isoformat(timespec="seconds"),
            "finished_at": datetime.fromtimestamp(finished)
            .astimezone()
            .isoformat(
                timespec="seconds",
            ),
            "duration_s": round(finished - self.started, 3),
            "interrupted": self.interrupted,
            "failed": self.failed,
            "error": self.error,
            "results": self.results,
            "http": {label: stats.as_dict() for label, stats in self.http.items()},
            "summary": self.summary,
            "state_file": str(state_path()),
        }
        path = poc_dir() / f"report-{self.command}-{stamp.strftime('%Y%m%d-%H%M%S')}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            say(f"Rapport JSON non ecrit ({path}): {exc}")
            return None
        say(f"Rapport JSON: {path}")
        return path


@dataclass
class Session:
    args: argparse.Namespace
    report: Report
    clients: list[GraphClient] = field(default_factory=list)
    providers: dict[tuple[str, ...], MsalAccessTokenProvider] = field(default_factory=dict)

    def provider(self, scopes: Sequence[str] = PLANNING_GRAPH_SCOPES) -> MsalAccessTokenProvider:
        key = tuple(scopes)
        if key not in self.providers:
            self.providers[key] = MsalAccessTokenProvider(
                client_id=ApplicationSettings.load().microsoft_client_id,
                scopes=key,
                allow_interactive=bool(self.args.interactive),
            )
        return self.providers[key]

    def graph(
        self,
        label: str,
        scopes: Sequence[str] = PLANNING_GRAPH_SCOPES,
    ) -> CountingGraphClient:
        client = CountingGraphClient(
            token_provider=self.provider(scopes),
            stats=self.report.http_stats(label),
            timeout=float(self.args.timeout),
        )
        self.clients.append(client)
        return client

    def store(self, label: str, list_ref: PlanningListRef) -> GraphListPlanningStore:
        return GraphListPlanningStore(graph=self.graph(label), list_ref=list_ref)

    async def aclose(self) -> None:
        for client in self.clients:
            with contextlib.suppress(Exception):
                await client.aclose()


@dataclass
class RawDelta:
    ok: bool
    items: dict[str, dict[str, Any]]
    delta_link: str = ""
    pages: int = 0
    status: int | None = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "item_count": len(self.items),
            "pages": self.pages,
            "has_delta_link": bool(self.delta_link),
            "status": self.status,
            "error": self.error,
        }


async def raw_delta(graph: GraphClient, link: str) -> RawDelta:
    """Follow one delta request to its deltaLink and keep the raw items (last occurrence)."""
    items: dict[str, dict[str, Any]] = {}
    pages = 0
    next_link = link
    try:
        while True:
            payload = await graph.get(next_link)
            pages += 1
            values = payload.get("value")
            for raw in values if isinstance(values, list) else []:
                if isinstance(raw, dict) and isinstance(raw.get("id"), str):
                    items.pop(raw["id"], None)
                    items[raw["id"]] = raw
            following = payload.get("@odata.nextLink")
            if isinstance(following, str) and following:
                next_link = following
                continue
            delta_link = payload.get("@odata.deltaLink")
            return RawDelta(
                ok=True,
                items=items,
                delta_link=delta_link if isinstance(delta_link, str) else "",
                pages=pages,
            )
    except GraphError as exc:
        return RawDelta(ok=False, items=items, pages=pages, status=exc.status_code, error=str(exc))


def same_etag(left: str, right: str) -> dict[str, bool]:
    def normalized(value: str) -> str:
        return value.replace('"', "").replace("{", "").replace("}", "").strip().casefold()

    return {
        "literal": bool(left) and left == right,
        "normalized": bool(left) and normalized(left) == normalized(right),
    }


def encode(data: Mapping[str, Any]) -> str:
    """Encode a document exactly as GraphListPlanningStore writes PfData."""
    return json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"))


def probe_payload(marker: str, step: str, size_kb: float = 0) -> dict[str, Any]:
    """Return a probe document with special characters, padded to ``size_kb`` of JSON."""
    payload: dict[str, Any] = {"step": step, "marker": marker, "text": SPECIAL_TEXT}
    missing = int(size_kb * 1024) - len(encode(payload))
    if missing > 0:
        chunk = f"{SPECIAL_TEXT} {marker} "
        encoded_chunk = len(json.dumps(chunk, ensure_ascii=False)) - 2
        payload["filler"] = chunk * (missing // encoded_chunk + 1)
    return payload


def data_fidelity(value: object, written: Mapping[str, Any]) -> dict[str, Any]:
    """Compare a PfData value read back with the document that was written."""
    parsed: object = None
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = json.loads(value)
    return {
        "pf_data_identical": value == encode(written),
        "pf_data_parses_equal": parsed == dict(written),
        "pf_data_length": len(value) if isinstance(value, str) else None,
    }


def delta_url(items_path: str, token: str | None, expand: str) -> str:
    params = [f"token={token}"] if token else []
    if expand:
        params.append(f"$expand={expand}")
    url = f"{items_path}/delta"
    return f"{url}?{'&'.join(params)}" if params else url


def error_label(exc: ProjectFlowError) -> str:
    return str(exc.status_code) if isinstance(exc, GraphError) else type(exc).__name__


async def run_together(coroutines: Sequence[Coroutine[Any, Any, None]]) -> None:
    """Run coroutines concurrently; a failure cancels and awaits the others, then propagates.

    The cancelled tasks run their cleanup (probe deletion) while the Graph clients are open.
    """
    try:
        async with asyncio.TaskGroup() as group:
            for coroutine in coroutines:
                group.create_task(coroutine)
    except BaseExceptionGroup as failure:
        raise failure.exceptions[0] from None


async def wait_for_item(
    graph: GraphClient,
    link: str,
    item_id: str,
    *,
    marker: str,
    written: Mapping[str, Any],
    wait_seconds: float,
    expected_etag: str = "",
) -> dict[str, Any]:
    """Replay ``link`` until the item shows the write tagged with ``marker``."""
    started = time.perf_counter()
    attempts = 0
    while True:
        attempts += 1
        page = await raw_delta(graph, link)
        elapsed = time.perf_counter() - started
        item = page.items.get(item_id)
        if not page.ok:
            return {"seen": False, "attempts": attempts, **page.as_dict()}
        if item is not None:
            fields = item.get("fields")
            pf_data = fields.get(DATA_COLUMN) if isinstance(fields, dict) else None
            # The hex marker survives any HTML or entity encoding SharePoint might add.
            visible = isinstance(pf_data, str) and marker in pf_data
            if visible or not isinstance(fields, dict) or elapsed >= wait_seconds:
                etag = str(item.get("eTag") or item.get("@odata.etag") or "")
                return {
                    "seen": True,
                    "write_visible": visible,
                    "has_fields": isinstance(fields, dict),
                    "has_pf_data": isinstance(pf_data, str),
                    **data_fidelity(pf_data, written),
                    "item_keys": sorted(item),
                    "field_keys": sorted(fields) if isinstance(fields, dict) else [],
                    "etag": etag,
                    "etag_equals_get": same_etag(etag, expected_etag),
                    "seconds_until_visible": round(elapsed, 3),
                    "attempts": attempts,
                    **page.as_dict(),
                }
        if elapsed >= wait_seconds:
            return {"seen": False, "attempts": attempts, **page.as_dict()}
        await asyncio.sleep(VISIBILITY_POLL_SECONDS)


async def delete_quietly(
    store: GraphListPlanningStore,
    item_id: str,
    results: dict[str, Any],
) -> None:
    try:
        current = await store.get(item_id)
        if current is not None:
            await store.delete(item_id, current.etag)
        results["probe_deleted"] = True
    except Exception as exc:  # noqa: BLE001 - cleanup must never hide the measurements
        results["probe_deleted"] = False
        results["cleanup_error"] = repr(exc)


async def write_probe(
    store: GraphListPlanningStore,
    item_id: str,
    etag: str,
    data: Mapping[str, Any],
) -> str:
    try:
        return await store.update(item_id, etag, data)
    except PlanningConflictError:
        current = await store.get(item_id)
        if current is None:
            raise ConfigError(f"Sonde {item_id} supprimee pendant la mesure.") from None
        return await store.update(item_id, current.etag, data)


def probe_seq(record: PlanningRecord | None) -> int:
    value = record.data.get("seq") if record is not None and record.data else None
    return value if isinstance(value, int) else 0


def find_probe(records: Sequence[PlanningRecord], doc_id: str) -> PlanningRecord | None:
    candidates = [
        record
        for record in records
        if not record.deleted and record.kind == "probe" and record.doc_id == doc_id
    ]
    # Both postes may have created it: the oldest item wins on both sides.
    return min(
        candidates,
        key=lambda record: (int(record.item_id) if record.item_id.isdigit() else 0, record.item_id),
        default=None,
    )


async def planner_group_id(session: Session) -> str:
    plan_id = AppConfig.load().planner.target_plan_id
    if not plan_id:
        raise ConfigError(
            "Aucun plan Planner configure dans ProjectFlow sur ce poste: "
            "utilisez --site-url ou --group-id.",
        )
    planner = session.graph("planner", PLANNER_GRAPH_SCOPES)
    plan = await planner.get(f"/planner/plans/{plan_id}?$select=id,title,container,owner")
    group_id = _planner_plan_group_id(plan)
    if not group_id:
        raise ConfigError("Impossible d'identifier le groupe Microsoft 365 du plan Planner.")
    session.report.results["planner_plan"] = {"id": plan_id, "title": plan.get("title")}
    return group_id


async def create_list(session: Session, graph: GraphClient, site_id: str) -> str | None:
    name = session.args.list_name
    attempts: dict[str, str] = {}
    session.report.results["create_attempts"] = attempts
    try:
        list_id = await create_planning_list(graph, site_id, name)
    except ConfigError:
        attempts["Sites.ReadWrite.All"] = "403"
    else:
        attempts["Sites.ReadWrite.All"] = "created"
        return list_id
    say("Creation refusee avec Sites.ReadWrite.All (attendu): essai avec Sites.Manage.All.")
    try:
        list_id = await create_planning_list(session.graph("manage", MANAGE_SCOPES), site_id, name)
    except AuthError as exc:
        attempts["Sites.Manage.All"] = f"jeton refuse: {exc}"
    except ConfigError:
        attempts["Sites.Manage.All"] = "403"
    else:
        attempts["Sites.Manage.All"] = "created"
        return list_id
    return None


async def cmd_setup(session: Session) -> None:
    args, report = session.args, session.report
    graph = session.graph("sites")
    group_id = ""
    if args.site_url:
        site_id, source = await site_id_from_url(graph, args.site_url), "url"
    elif args.group_id:
        group_id = args.group_id.strip()
        site_id, source = await group_site_id(graph, group_id), "group"
    else:
        group_id = await planner_group_id(session)
        site_id, source = await group_site_id(graph, group_id), "planner"
    site = await graph.get(f"/sites/{site_id}?$select=id,webUrl,displayName")
    web_url = str(site.get("webUrl") or "")
    report.results["site"] = {
        "id": site_id,
        "web_url": web_url,
        "display_name": site.get("displayName"),
        "source": source,
        "group_id": group_id,
    }
    say(f"Site SharePoint: {site.get('displayName')} ({web_url})")

    list_id = await find_list(graph, site_id, args.list_name)
    report.results["list_found"] = list_id is not None
    created = False
    if list_id is None and args.create:
        list_id = await create_list(session, graph, site_id)
        created = list_id is not None
    report.results["list_created"] = created
    save_state(
        {
            **load_state(),
            "site_id": site_id,
            "site_web_url": web_url,
            "site_source": source,
            "group_id": group_id,
            "list_name": args.list_name,
            "list_id": list_id or "",
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    if not list_id:
        if not args.create:
            report.summary.append(
                f"Liste « {args.list_name} » introuvable: relancez avec --create.",
            )
        report.summary.append(manual_list_instructions(args.list_name))
        return
    problems = await planning_column_problems(graph, site_id, list_id)
    report.results["list_id"] = list_id
    report.results["column_problems"] = list(problems)
    report.summary.append(
        f"Liste « {args.list_name} » {'creee' if created else 'trouvee'}: {list_id}",
    )
    report.summary.extend(problems or ["Colonnes PfKind, PfDocId, PfProject, PfData: OK"])
    report.summary.append(f"Etat enregistre: {state_path()}")


def variant_label(name: str, expand: str) -> str:
    return f"{name} ($expand={expand})" if expand else f"{name} (sans $expand)"


def yes_no(value: object) -> str:
    return "non mesure" if value is None else ("oui" if value else "non")


def etag_flag(value: object) -> str:
    if not isinstance(value, dict):
        return yes_no(value)
    return f"{yes_no(value.get('normalized'))} (texte exact: {yes_no(value.get('literal'))})"


def describe_reading(reading: Mapping[str, Any]) -> str:
    if not reading.get("seen"):
        status = reading.get("status")
        return f"refuse ({status})" if status else "ecriture non vue dans le delai"
    return (
        f"porte PfData: {yes_no(reading.get('has_pf_data'))}, "
        f"identique a l'ecriture: {yes_no(reading.get('pf_data_parses_equal'))} "
        f"(texte exact: {yes_no(reading.get('pf_data_identical'))}), "
        f"ecriture visible: {yes_no(reading.get('write_visible'))} "
        f"apres {reading.get('seconds_until_visible')} s"
    )


async def open_delta_variants(
    graph: GraphClient,
    items_path: str,
    variants: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Ask each delta form for a token=latest link; return the accepted links by variant."""
    links: dict[str, str] = {}
    for name, expand in DELTA_VARIANTS:
        page = await raw_delta(graph, delta_url(items_path, "latest", expand))
        variant: dict[str, Any] = {"expand": expand, "latest": page.as_dict()}
        variants[name] = variant
        if page.ok and page.delta_link:
            links[name] = page.delta_link
            variant["delta_link_keeps_expand"] = (
                "expand" in unquote(page.delta_link).casefold() if expand else None
            )
        state = "accepte" if name in links else f"refuse ({page.status})"
        say(f"Delta token=latest {variant_label(name, expand)}: {state}")
    return links


async def patch_probe_fields(
    graph: GraphClient,
    items_path: str,
    probe: PlanningRecord,
    written: Mapping[str, Any],
    results: dict[str, Any],
) -> tuple[str, str] | None:
    """PATCH PfData without the store; return the eTags of the response and of a later GET."""
    try:
        patched = await graph.patch(
            f"{items_path}/{probe.item_id}/fields",
            json={DATA_COLUMN: encode(written)},
            headers={"If-Match": probe.etag},
        )
    except GraphError as exc:
        results["patch_error"] = f"{exc.status_code}: {exc}"
        return None
    patch_etag = str(patched.get("@odata.etag") or "")
    results["patch_response_keys"] = sorted(patched)
    results["patch_returns_odata_etag"] = bool(patch_etag)
    results["patch_response_pf_data"] = data_fidelity(patched.get(DATA_COLUMN), written)
    fetched = await graph.get(f"{items_path}/{probe.item_id}?$select=id,eTag")
    get_etag = str(fetched.get("eTag") or "")
    results["etag_after_patch"] = {"patch": patch_etag, "get": get_etag}
    results["etag_patch_equals_get"] = same_etag(patch_etag, get_etag) if patch_etag else None
    return patch_etag, get_etag


async def read_delta_variants(
    graph: GraphClient,
    items_path: str,
    item_id: str,
    links: Mapping[str, str],
    variants: dict[str, dict[str, Any]],
    *,
    marker: str,
    written: Mapping[str, Any],
    wait_seconds: float,
    get_etag: str,
) -> None:
    async def wait(link: str) -> dict[str, Any]:
        return await wait_for_item(
            graph,
            link,
            item_id,
            marker=marker,
            written=written,
            wait_seconds=wait_seconds,
            expected_etag=get_etag,
        )

    for name, expand in DELTA_VARIANTS:
        link = links.get(name)
        if link is None:
            continue
        variant = variants[name]
        say(f"Lecture du delta {variant_label(name, expand)}...")
        variant["after_patch"] = await wait(link)
        if expand and not variant.get("delta_link_keeps_expand"):
            variant["after_patch_with_appended_expand"] = await wait(f"{link}&$expand={expand}")
        variant["full_enumeration"] = await wait(delta_url(items_path, None, expand))


async def measure_store_writes(
    store: GraphListPlanningStore,
    graph: GraphClient,
    items_path: str,
    probe: PlanningRecord,
    results: dict[str, Any],
    *,
    written: dict[str, Any],
    etag: str,
    store_link: str,
    size_kb: float,
    wait_seconds: float,
) -> None:
    # Can a writer chain updates on the eTag the PATCH returned, without a GET?
    second = probe_payload(uuid.uuid4().hex, "second-patch")
    last_written = written
    current = ""
    try:
        current = await store.update(probe, etag, second)
        results["patch_etag_usable_as_if_match"] = True
        last_written = second
    except PlanningConflictError:
        results["patch_etag_usable_as_if_match"] = False
    except GraphError as exc:
        results["second_patch_error"] = f"{exc.status_code}: {exc}"
    if not current:
        record = await store.get(probe.item_id)
        current = record.etag if record is not None else ""

    large = probe_payload(uuid.uuid4().hex, "large", size_kb)
    encoded = encode(large)
    large_result: dict[str, Any] = {
        "size_kb": size_kb,
        "chars": len(encoded),
        "utf8_bytes": len(encoded.encode("utf-8")),
    }
    results["large_payload"] = large_result
    try:
        await store.update(probe.item_id, current, large)
    except GraphError as exc:
        large_result["accepted"] = False
        large_result["error"] = f"{exc.status_code}: {exc}"
    else:
        large_result["accepted"] = True
        last_written = large
        raw = await graph.get(f"{items_path}/{probe.item_id}?$expand=fields($select={DATA_COLUMN})")
        fields = raw.get("fields")
        stored = fields.get(DATA_COLUMN) if isinstance(fields, dict) else None
        large_result["get"] = data_fidelity(stored, large)
        record = await store.get(probe.item_id)
        large_result["store_get_equal"] = record is not None and record.data == large

    if not store_link:
        return
    # Replay the store's own link until the last write shows up, decoded.
    deadline = time.perf_counter() + wait_seconds
    while True:
        try:
            changes = await store.changes(store_link)
        except GraphError as exc:
            results["store_changes"] = {"error": f"{exc.status_code}: {exc}"}
            return
        record = next((item for item in changes.records if item.item_id == probe.item_id), None)
        equal = record is not None and record.data == last_written
        if equal or time.perf_counter() >= deadline:
            break
        await asyncio.sleep(VISIBILITY_POLL_SECONDS)
    results["store_changes"] = {
        "seen": record is not None,
        "parsed": record is not None and record.data is not None,
        "data_equals_last_write": equal,
        "last_write": last_written.get("step"),
        "requests": changes.requests,
        "fields_fetched": changes.fields_fetched,
        "expands_fields": store.expands_fields,
    }


def summarize_fields_check(report: Report) -> None:
    results = report.results
    variants: dict[str, dict[str, Any]] = results.get("delta_variants", {})
    lines: list[str] = []
    failures: list[str] = []
    carrying: list[str] = []
    for name, expand in DELTA_VARIANTS:
        variant = variants.get(name)
        if variant is None:
            continue
        label = variant_label(name, expand)
        latest = variant["latest"]
        if not latest["ok"]:
            lines.append(f"Delta {label}: refuse ({latest['status']}): {latest['error']}")
            continue
        keeps = yes_no(variant.get("delta_link_keeps_expand"))
        lines.append(f"Delta {label}: accepte, le deltaLink conserve $expand: {keeps}")
        for key, title in (
            ("after_patch", "apres token=latest"),
            ("after_patch_with_appended_expand", "apres token=latest + $expand ajoute au lien"),
            ("full_enumeration", "enumeration complete"),
        ):
            reading = variant.get(key)
            if reading is None:
                continue
            lines.append(f"  {title}: {describe_reading(reading)}")
            if key != "full_enumeration" and reading.get("has_pf_data") and name not in carrying:
                carrying.append(name)

    measured = any("after_patch" in variant for variant in variants.values())
    if "patch_error" in results:
        failures.append(f"PATCH /fields refuse: {results['patch_error']}")
    elif variants and not any(variant["latest"]["ok"] for variant in variants.values()):
        failures.append("toutes les variantes du delta sont refusees")
    elif carrying:
        chosen = carrying[0]
        expand = dict(DELTA_VARIANTS)[chosen]
        lines.append(
            f"Colonnes dans le delta: OUI, variante retenue {variant_label(chosen, expand)}"
        )
        if chosen != DELTA_VARIANTS[0][0]:
            lines.append(f"  -> le store demande $expand={FIELDS_EXPAND}: a adapter")
    elif measured:
        failures.append(
            "aucune variante du delta ne porte PfData: le store relira chaque element "
            "modifie (lectures de secours)",
        )

    first_seen: dict[str, Any] = next(
        (
            variant["after_patch"]
            for variant in variants.values()
            if variant.get("after_patch", {}).get("seen")
        ),
        {},
    )
    lines.extend(
        [
            (
                "Reponse PATCH /fields contient @odata.etag: "
                f"{yes_no(results.get('patch_returns_odata_etag'))}"
            ),
            f"eTag PATCH == eTag GET: {etag_flag(results.get('etag_patch_equals_get'))}",
            f"eTag GET == eTag delta: {etag_flag(first_seen.get('etag_equals_get'))}",
            (
                f"eTag ({results.get('if_match_etag_source', '?')}) reutilisable en If-Match: "
                f"{yes_no(results.get('patch_etag_usable_as_if_match'))}"
            ),
        ],
    )
    large = results.get("large_payload")
    if isinstance(large, dict):
        size = f"Document de {large['chars']} caracteres ({large['utf8_bytes']} octets)"
        if not large.get("accepted"):
            lines.append(f"{size}: refuse ({large.get('error')})")
        else:
            identical = large.get("get", {}).get("pf_data_identical")
            lines.append(
                f"{size}: accepte, relu a l'identique: {yes_no(identical)}, "
                f"decode par le store: {yes_no(large.get('store_get_equal'))}",
            )
            if not large.get("store_get_equal"):
                failures.append("le store ne relit pas le grand document a l'identique")
    store_changes = results.get("store_changes")
    if isinstance(store_changes, dict):
        equal = store_changes.get("data_equals_last_write")
        lines.append(
            f"Store: delta decode a l'identique ({store_changes.get('last_write')}, accents, "
            f'« », < > & ", retours a la ligne): {yes_no(equal)}, '
            f"{store_changes.get('fields_fetched')} lecture(s) de secours pour les colonnes",
        )
        if not equal:
            failures.append("le store ne relit pas la derniere ecriture a l'identique")
    if failures:
        report.failed = True
        lines.extend(f"ECHEC: {failure}" for failure in failures)
    report.summary.extend(lines)


async def cmd_fields_check(session: Session) -> None:
    args, report = session.args, session.report
    results = report.results
    ref = require_list(load_state())
    graph = session.graph("sites")
    store = GraphListPlanningStore(graph=graph, list_ref=ref)
    items_path = f"/sites/{ref.site_id}/lists/{ref.list_id}/items"
    variants: dict[str, dict[str, Any]] = {}
    results["delta_variants"] = variants
    report.finalizers.append(lambda: summarize_fields_check(report))
    probe = await store.create("probe", f"fields-check-{uuid.uuid4().hex}", {"step": "create"})
    results["probe_item_id"] = probe.item_id
    say(f"Sonde creee: element {probe.item_id}")
    try:
        links = await open_delta_variants(graph, items_path, variants)
        store_link = ""
        try:
            store_link = await store.latest_delta_link()
        except GraphError as exc:
            results["store_latest_error"] = f"{exc.status_code}: {exc}"

        # The PATCH does not depend on the delta: it is measured whatever the variants gave.
        marker = uuid.uuid4().hex
        written = probe_payload(marker, "patch")
        etags = await patch_probe_fields(graph, items_path, probe, written, results)
        if etags is None:
            return
        patch_etag, get_etag = etags
        say("PATCH /fields fait.")
        await read_delta_variants(
            graph,
            items_path,
            probe.item_id,
            links,
            variants,
            marker=marker,
            written=written,
            wait_seconds=args.wait,
            get_etag=get_etag,
        )
        results["if_match_etag_source"] = "patch" if patch_etag else "get"
        await measure_store_writes(
            store,
            graph,
            items_path,
            probe,
            results,
            written=written,
            etag=patch_etag or get_etag,
            store_link=store_link,
            size_kb=args.payload_kb,
            wait_seconds=args.wait,
        )
    finally:
        if not args.keep:
            await delete_quietly(store, probe.item_id, results)


async def cmd_conflict(session: Session) -> None:
    args, results = session.args, session.report.results
    store = session.store("sites", require_list(load_state()))
    probe = await store.create("probe", f"conflict-{uuid.uuid4().hex}", {"step": "create"})
    results["probe_item_id"] = probe.item_id
    try:
        try:
            await store.update(probe, probe.etag, {"step": "fresh"})
            results["fresh_update"] = "200"
        except GraphError as exc:
            results["fresh_update"] = f"{exc.status_code}: {exc}"
        try:
            await store.update(probe, probe.etag, {"step": "stale"})
            results["stale_update"] = "accepted"
        except PlanningConflictError:
            results["stale_update"] = "412"
        except GraphError as exc:
            results["stale_update"] = f"{exc.status_code}: {exc}"
        after = await store.get(probe.item_id)
        results["data_after"] = after.data if after else None
        try:
            await store.delete(probe.item_id, probe.etag)
            results["stale_delete"] = "accepted"
        except PlanningConflictError:
            results["stale_delete"] = "412"
        except GraphError as exc:
            results["stale_delete"] = f"{exc.status_code}: {exc}"
    finally:
        if not args.keep:
            await delete_quietly(store, probe.item_id, results)
    passed = (
        results["fresh_update"] == "200"
        and results["stale_update"] == "412"
        and results.get("stale_delete") == "412"
        and (results.get("data_after") or {}).get("step") == "fresh"
    )
    results["passed"] = passed
    session.report.failed = not passed
    session.report.summary.extend(
        [
            f"Mise a jour avec l'eTag courant: {results['fresh_update']} (attendu 200)",
            f"Mise a jour avec l'eTag perime: {results['stale_update']} (attendu 412)",
            f"Suppression avec l'eTag perime: {results.get('stale_delete')} (attendu 412)",
            f"Donnees conservees: {results.get('data_after')}",
            f"Resultat: {'REUSSI' if passed else 'ECHEC'}",
        ],
    )


async def cmd_latency(session: Session) -> None:
    args, report = session.args, session.report
    ref = require_list(load_state())
    writer = session.store("writer", ref)
    reader = session.store("reader", ref)
    written: dict[int, float] = {}
    acked: dict[int, float] = {}
    seen: dict[int, float] = {}
    write_durations: list[float] = []
    delta_durations: list[float] = []
    counters: Counter[str] = Counter()

    def finalize() -> None:
        latencies = [seen[seq] - written[seq] for seq in sorted(seen) if seq in written]
        after_ack = [seen[seq] - acked[seq] for seq in sorted(seen) if seq in acked]
        missed = sorted(set(written) - set(seen))
        superseded = [seq for seq in missed if any(other > seq for other in seen)]
        report.results.update(
            writes=len(written),
            acknowledged=len(acked),
            seen=len(seen),
            misses=len(missed),
            missed_seqs=missed,
            superseded_before_seen=len(superseded),
            latency_s=distribution(latencies),
            latency_after_ack_s=distribution(after_ack),
            write_call_s=distribution(write_durations),
            delta_call_s=distribution(delta_durations),
            delta_calls=len(delta_durations),
            counters=dict(counters),
            samples=[
                {
                    "seq": seq,
                    "t_write": written[seq],
                    "t_ack": acked.get(seq),
                    "t_seen": seen.get(seq),
                }
                for seq in sorted(written)
            ],
        )
        report.summary.extend(
            [
                (
                    f"Ecritures: {len(written)}, vues par le lecteur: {len(seen)}, "
                    f"manquees: {len(missed)} (dont {len(superseded)} ecrasees avant lecture)"
                ),
                f"Latence ecriture -> visible: {describe(report.results['latency_s'])}",
                f"Latence apres accuse PATCH: {describe(report.results['latency_after_ack_s'])}",
                f"Duree PATCH: {describe(report.results['write_call_s'])}",
                f"Duree d'un appel delta (lecteur): {describe(report.results['delta_call_s'])}",
                f"Compteurs: {dict(counters) or 'aucun'}",
            ],
        )

    report.finalizers.append(finalize)
    probe = await writer.create("probe", f"latency-{uuid.uuid4().hex}", {"seq": 0})
    report.results["probe_item_id"] = probe.item_id
    stop = asyncio.Event()
    last_seen = asyncio.Event()
    try:
        link = await reader.latest_delta_link()

        async def write_loop() -> None:
            etag = probe.etag
            for seq in range(1, args.writes + 1):
                cycle = time.perf_counter()
                written[seq] = time.time()
                try:
                    etag = await write_probe(
                        writer,
                        probe.item_id,
                        etag,
                        {"seq": seq, "t_write": written[seq]},
                    )
                except ProjectFlowError as exc:
                    counters[f"write_error_{error_label(exc)}"] += 1
                else:
                    acked[seq] = time.time()
                write_durations.append(time.perf_counter() - cycle)
                await asyncio.sleep(max(0.0, args.interval - (time.perf_counter() - cycle)))

        async def read_loop() -> None:
            nonlocal link
            while not stop.is_set():
                cycle = time.perf_counter()
                try:
                    changes = await reader.changes(link)
                except ProjectFlowError as exc:
                    counters[f"delta_error_{error_label(exc)}"] += 1
                else:
                    now = time.time()
                    delta_durations.append(time.perf_counter() - cycle)
                    link = changes.delta_link
                    counters["fields_fetched"] += changes.fields_fetched
                    counters["resyncs"] += int(changes.resynced)
                    for record in changes.records:
                        seq = probe_seq(record) if record.item_id == probe.item_id else 0
                        if seq > 0 and seq not in seen:
                            seen[seq] = now
                            say(f"seq {seq:>4}: visible apres {now - written.get(seq, now):.2f} s")
                        if seq >= args.writes:
                            last_seen.set()
                remaining = max(0.0, args.poll - (time.perf_counter() - cycle))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=remaining)

        say(
            f"Mesure: {args.writes} ecritures toutes les {args.interval} s, lecture toutes les "
            f"{args.poll} s (Ctrl+C pour arreter)."
        )
        reader_task = asyncio.create_task(read_loop())
        try:
            await write_loop()
            grace = max(10.0, 3 * args.poll)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(last_seen.wait(), timeout=grace)
        finally:
            stop.set()
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
    finally:
        await delete_quietly(writer, probe.item_id, report.results)


async def cmd_pingpong(session: Session) -> None:
    args = session.args
    store = session.store("pingpong", require_list(load_state()))
    snapshot = await store.changes(None)
    link = snapshot.delta_link
    ping = find_probe(snapshot.records, PING_DOC_ID)
    pong = find_probe(snapshot.records, PONG_DOC_ID)
    if args.role == "ping":
        await _ping(session, store, link, ping, pong)
    else:
        await _pong(session, store, link, ping, pong)


async def _ping(
    session: Session,
    store: GraphListPlanningStore,
    link: str,
    ping: PlanningRecord | None,
    pong: PlanningRecord | None,
) -> None:
    args, report = session.args, session.report
    if ping is None:
        ping = await store.create("probe", PING_DOC_ID, {"seq": 0, "kind": "ping"})
    if pong is None:
        pong = await store.create("probe", PONG_DOC_ID, {"seq": 0, "kind": "pong"})
    base = max(probe_seq(ping), probe_seq(pong))
    rtts: list[float] = []
    misses: list[int] = []
    apparent: list[float] = []
    read_errors: Counter[str] = Counter()

    def finalize() -> None:
        report.results.update(
            rounds_done=len(rtts) + len(misses),
            answered=len(rtts),
            misses=len(misses),
            missed_seqs=misses,
            read_errors=dict(read_errors),
            rtt_s=distribution(rtts),
            rtt_half_s=distribution([rtt / 2 for rtt in rtts]),
            pong_minus_ping_clock_s=distribution(apparent),
        )
        report.summary.extend(
            [
                (
                    f"Tours: {len(rtts) + len(misses)}, reponses: {len(rtts)}, "
                    f"sans reponse: {len(misses)}, erreurs de lecture: "
                    f"{dict(read_errors) or 'aucune'}"
                ),
                f"Aller-retour (horloge du ping): {describe(report.results['rtt_s'])}",
                f"Aller simple estime (RTT/2): {describe(report.results['rtt_half_s'])}",
                (
                    "t_pong - t_ping (inclut le decalage des horloges): "
                    f"{describe(report.results['pong_minus_ping_clock_s'])}"
                ),
            ],
        )

    report.finalizers.append(finalize)
    report.results["probes"] = {"ping": ping.item_id, "pong": pong.item_id}
    say(
        f"Ping pret (sondes {ping.item_id}/{pong.item_id}). Le role pong doit tourner sur "
        "l'autre poste."
    )
    ping_id, pong_id = ping.item_id, pong.item_id
    answers: dict[int, tuple[float, PlanningRecord]] = {}
    answered = asyncio.Event()

    async def read_loop() -> None:
        # Free-running cadence, like the app's timer: its phase against each write is random,
        # so the round trips are not snapped to a grid that starts at the write.
        reader_link = link
        while True:
            cycle = time.perf_counter()
            try:
                changes = await store.changes(reader_link)
            except ProjectFlowError as exc:
                read_errors[error_label(exc)] += 1
            else:
                seen_at = time.perf_counter()
                reader_link = changes.delta_link
                for record in changes.records:
                    seq = probe_seq(record) if record.item_id == pong_id else 0
                    if seq > 0 and seq not in answers:
                        answers[seq] = (seen_at, record)
                        answered.set()
            await asyncio.sleep(max(0.0, args.poll - (time.perf_counter() - cycle)))

    reader = asyncio.create_task(read_loop())
    try:
        etag = ping.etag
        for round_number in range(1, args.rounds + 1):
            if reader.done():
                break
            seq = base + round_number
            # A random pause samples every phase between both postes' polling cycles.
            await asyncio.sleep(random.uniform(0, args.poll))
            started = time.perf_counter()
            t_ping = time.time()
            etag = await write_probe(
                store,
                ping_id,
                etag,
                {"seq": seq, "kind": "ping", "t_ping": t_ping, "host": platform.node()},
            )
            deadline = started + args.round_timeout
            while (
                seq not in answers
                and not reader.done()
                and (remaining := deadline - time.perf_counter()) > 0
            ):
                answered.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(answered.wait(), timeout=remaining)
            if seq not in answers:
                misses.append(seq)
                say(f"tour {round_number:>3} (seq {seq}): pas de reponse en {args.round_timeout} s")
                continue
            seen_at, answer = answers[seq]
            rtt = seen_at - started
            rtts.append(rtt)
            t_pong = (answer.data or {}).get("t_pong")
            if isinstance(t_pong, int | float):
                apparent.append(float(t_pong) - t_ping)
            say(f"tour {round_number:>3} (seq {seq}): aller-retour {rtt:.2f} s")
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader


async def _pong(
    session: Session,
    store: GraphListPlanningStore,
    link: str,
    ping: PlanningRecord | None,
    pong: PlanningRecord | None,
) -> None:
    args, report = session.args, session.report
    apparent: list[float] = []
    answered: list[int] = []

    def finalize() -> None:
        report.results.update(
            answered=len(answered),
            answered_seqs=answered,
            pong_minus_ping_clock_s=distribution(apparent),
        )
        report.summary.extend(
            [
                f"Pings repondus: {len(answered)}",
                (
                    "t_vu - t_ping (inclut le decalage des horloges): "
                    f"{describe(report.results['pong_minus_ping_clock_s'])}"
                ),
                "Le resultat de reference (aller-retour) est dans le rapport du poste ping.",
            ],
        )

    report.finalizers.append(finalize)
    last_activity = time.perf_counter()
    say("Pong pret: en attente des pings (Ctrl+C pour arreter).")
    while True:
        if ping is not None and pong is not None:
            ping_seq = probe_seq(ping)
            if ping_seq > probe_seq(pong):
                seen_at = time.time()
                data = {"seq": ping_seq, "kind": "pong", "t_pong": seen_at, "host": platform.node()}
                etag = await write_probe(store, pong.item_id, pong.etag, data)
                pong = PlanningRecord(
                    item_id=pong.item_id,
                    etag=etag,
                    kind="probe",
                    doc_id=PONG_DOC_ID,
                    data=data,
                )
                t_ping = (ping.data or {}).get("t_ping")
                if isinstance(t_ping, int | float):
                    apparent.append(seen_at - float(t_ping))
                answered.append(ping_seq)
                last_activity = time.perf_counter()
                say(f"pong seq {ping_seq}")
        if time.perf_counter() - last_activity > args.idle_timeout:
            say(f"Aucun nouveau ping depuis {args.idle_timeout} s: arret.")
            return
        await asyncio.sleep(args.poll)
        changes = await store.changes(link)
        link = changes.delta_link
        ping = find_probe(changes.records, PING_DOC_ID) or (None if changes.full else ping)
        if pong is None or changes.full:
            pong = find_probe(changes.records, PONG_DOC_ID) or (None if changes.full else pong)


async def cmd_load(session: Session) -> None:
    args, report = session.args, session.report
    ref = require_list(load_state())
    stats = report.http_stats("pollers")
    durations: list[float] = []
    errors: Counter[str] = Counter()
    calls = [0] * args.pollers
    started = time.perf_counter()
    deadline = started + args.minutes * 60

    def finalize() -> None:
        elapsed = max(time.perf_counter() - started, 1e-6)
        per_window = stats.responses / elapsed * BUDGET_WINDOW_SECONDS
        per_poller = per_window / max(args.pollers, 1)
        report.results.update(
            elapsed_s=round(elapsed, 1),
            pollers=args.pollers,
            poll_s=args.poll,
            delta_calls=sum(calls),
            calls_per_poller=calls,
            http_responses=stats.responses,
            throttled_429=stats.statuses[429],
            errors=dict(errors),
            delta_call_s=distribution(durations),
            responses_per_5_min=round(per_window),
            budget_share=round(per_window / BUDGET_REQUESTS, 3),
            responses_per_5_min_per_poller=round(per_poller),
            postes_within_budget=int(BUDGET_REQUESTS / per_poller) if per_poller else None,
        )
        report.summary.extend(
            [
                (
                    f"{args.pollers} lecteurs toutes les {args.poll} s pendant "
                    f"{elapsed / 60:.1f} min: {stats.responses} reponses HTTP, "
                    f"429: {stats.statuses[429]}, erreurs: {dict(errors) or 'aucune'}"
                ),
                f"Duree d'un appel delta: {describe(report.results['delta_call_s'])}",
                (
                    f"Rythme: {round(per_window)} requetes / 5 min, soit "
                    f"{report.results['budget_share']:.0%} du budget de {BUDGET_REQUESTS}"
                ),
                (
                    f"Par poste simule: {round(per_poller)} requetes / 5 min -> environ "
                    f"{report.results['postes_within_budget']} postes dans le budget "
                    "(lecture seule)"
                ),
            ],
        )

    report.finalizers.append(finalize)

    async def poller(index: int) -> None:
        store = session.store("pollers", ref)
        link = ""
        await asyncio.sleep(random.uniform(0, args.poll))
        while time.perf_counter() < deadline:
            cycle = time.perf_counter()
            try:
                if not link:
                    link = await store.latest_delta_link()
                else:
                    link = (await store.changes(link)).delta_link
            except ProjectFlowError as exc:
                errors[error_label(exc)] += 1
            durations.append(time.perf_counter() - cycle)
            calls[index] += 1
            await asyncio.sleep(max(0.0, args.poll - (time.perf_counter() - cycle)))

    async def writer() -> None:
        store = session.store("writer", ref)
        probe = await store.create("probe", f"load-{uuid.uuid4().hex}", {"seq": 0})
        etag = probe.etag
        seq = 0
        try:
            while time.perf_counter() < deadline:
                seq += 1
                try:
                    etag = await write_probe(store, probe.item_id, etag, {"seq": seq})
                except ProjectFlowError as exc:
                    errors[f"write {error_label(exc)}"] += 1
                await asyncio.sleep(args.write_interval)
        finally:
            await delete_quietly(store, probe.item_id, report.results)

    async def progress() -> None:
        while True:
            await asyncio.sleep(PROGRESS_SECONDS)
            say(
                f"{(time.perf_counter() - started) / 60:.1f} min: {stats.responses} reponses, "
                f"429: {stats.statuses[429]}, erreurs: {sum(errors.values())}",
            )

    say(f"Charge: {args.pollers} lecteurs pendant {args.minutes} min (Ctrl+C pour arreter).")
    tasks: list[Coroutine[Any, Any, None]] = [poller(index) for index in range(args.pollers)]
    if args.write_interval > 0:
        tasks.append(writer())
    progress_task = asyncio.create_task(progress())
    try:
        await run_together(tasks)
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task


async def cmd_cleanup(session: Session) -> None:
    store = session.store("sites", require_list(load_state()))
    snapshot = await store.changes(None)
    probes = [
        record for record in snapshot.records if not record.deleted and record.kind == "probe"
    ]
    deleted = 0
    for record in probes:
        try:
            await store.delete(record.item_id, record.etag)
        except PlanningConflictError:
            current = await store.get(record.item_id)
            if current is not None:
                await store.delete(current.item_id, current.etag)
        deleted += 1
        say(f"Sonde supprimee: element {record.item_id} ({record.doc_id})")
    session.report.results.update(
        items=len(snapshot.records),
        probes_found=len(probes),
        probes_deleted=deleted,
    )
    session.report.summary.append(f"Sondes supprimees: {deleted} / {len(probes)}")


Command = Callable[[Session], Awaitable[None]]


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--interactive",
        action="store_true",
        help="autorise l'ouverture du navigateur si l'autorisation Microsoft manque",
    )
    common.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="delai maximal d'une requete Graph en secondes (defaut 30)",
    )
    common.add_argument("--verbose", action="store_true", help="affiche les journaux Graph")

    parser = argparse.ArgumentParser(
        prog="graph_bench.py",
        description=(
            "Banc d'essai Microsoft Graph du planning partage (liste SharePoint, Lot 0). "
            "Chaque commande affiche un resume et ecrit un rapport JSON dans "
            f"{poc_dir()}."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")

    def add(name: str, handler: Command, help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, parents=[common], help=help_text, description=help_text)
        sub.set_defaults(handler=handler)
        return sub

    setup = add("setup", cmd_setup, "trouve ou cree la liste SharePoint et memorise les ids")
    target = setup.add_mutually_exclusive_group()
    target.add_argument("--site-url", help="adresse du site, ex. https://x.sharepoint.com/sites/y")
    target.add_argument("--group-id", help="id du groupe Microsoft 365 dont le site est utilise")
    setup.add_argument("--list-name", default=DEFAULT_LIST_NAME, help="nom de la liste")
    setup.add_argument("--create", action="store_true", help="cree la liste si elle manque")

    fields_check = add(
        "fields-check",
        cmd_fields_check,
        "verifie si le delta porte les colonnes ($expand=fields) et la stabilite des eTags",
    )
    fields_check.add_argument(
        "--wait",
        type=float,
        default=30.0,
        help="attente maximale de visibilite d'une ecriture en secondes (defaut 30)",
    )
    fields_check.add_argument(
        "--payload-kb",
        type=float,
        default=30.0,
        help="taille du grand document ecrit dans PfData, en Ko de JSON (defaut 30)",
    )
    fields_check.add_argument("--keep", action="store_true", help="garde la sonde a la fin")

    conflict = add("conflict", cmd_conflict, "verifie le refus 412 d'un eTag perime")
    conflict.add_argument("--keep", action="store_true", help="garde la sonde a la fin")

    latency = add("latency", cmd_latency, "latence ecriture -> visible dans un seul processus")
    latency.add_argument("--writes", type=int, default=100, help="nombre d'ecritures (100)")
    latency.add_argument("--interval", type=float, default=3.0, help="secondes entre ecritures")
    latency.add_argument("--poll", type=float, default=2.0, help="secondes entre lectures delta")

    pingpong = add("pingpong", cmd_pingpong, "aller-retour entre deux postes")
    pingpong.add_argument("--role", choices=("ping", "pong"), required=True)
    pingpong.add_argument("--rounds", type=int, default=50, help="tours du ping (defaut 50)")
    pingpong.add_argument("--poll", type=float, default=2.0, help="secondes entre lectures delta")
    pingpong.add_argument(
        "--round-timeout",
        type=float,
        default=60.0,
        help="attente maximale d'une reponse par tour (ping, defaut 60 s)",
    )
    pingpong.add_argument(
        "--idle-timeout",
        type=float,
        default=180.0,
        help="arret du pong apres ce delai sans nouveau ping (defaut 180 s)",
    )

    load = add("load", cmd_load, "lecteurs delta concurrents sur le compte partage")
    load.add_argument("--pollers", type=int, default=8, help="lecteurs simultanes (defaut 8)")
    load.add_argument("--poll", type=float, default=2.0, help="secondes entre lectures delta")
    load.add_argument("--minutes", type=float, default=3.0, help="duree en minutes (defaut 3)")
    load.add_argument(
        "--write-interval",
        type=float,
        default=0.0,
        help="ajoute un redacteur toutes les N secondes (0 = aucun)",
    )

    add("cleanup", cmd_cleanup, "supprime toutes les sondes (PfKind = probe)")
    return parser


def configure_logging(*, verbose: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.INFO if verbose else logging.WARNING,
        ),
    )


async def run(args: argparse.Namespace, report: Report) -> None:
    session = Session(args=args, report=report)
    handler: Command = args.handler
    try:
        await handler(session)
    finally:
        await session.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    arguments = {key: value for key, value in vars(args).items() if key != "handler"}
    name = f"pingpong-{args.role}" if args.command == "pingpong" else args.command
    report = Report(command=name, arguments=arguments)
    exit_code = 0
    try:
        asyncio.run(run(args, report))
    except KeyboardInterrupt:
        report.interrupted = True
        exit_code = 130
    except ProjectFlowError as exc:
        report.error = str(exc)
        exit_code = 1
        if isinstance(exc, AuthError) and not args.interactive:
            report.summary.append(AUTH_HINT)
    except Exception as exc:  # noqa: BLE001 - the partial report must still be written
        report.error = repr(exc)
        report.results["traceback"] = traceback.format_exc()
        traceback.print_exc()
        exit_code = 2
    report.finish()
    if exit_code == 0 and report.failed:
        exit_code = EXIT_FAILED_CHECK
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
