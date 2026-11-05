"""Shared planning stored in a SharePoint list: one list item per planning document.

Each item carries four text columns: PfKind (document type), PfDocId (document uuid),
PfProject (project number or empty) and PfData (the document as compact JSON). Changes
are read through the listItem delta API and written with If-Match, so a write made on
another poste in the meantime fails with PlanningConflictError instead of being lost.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import quote, unquote, urlsplit

from projectflow.exceptions import ConfigError, GraphError
from projectflow.graph.client import GraphClient

PlanningKind = Literal["task", "resource", "absence", "meta", "probe"]
PLANNING_KINDS: Final = frozenset({"task", "resource", "absence", "meta", "probe"})
KIND_COLUMN: Final = "PfKind"
DOC_ID_COLUMN: Final = "PfDocId"
PROJECT_COLUMN: Final = "PfProject"
DATA_COLUMN: Final = "PfData"
PLANNING_COLUMNS: Final = (KIND_COLUMN, DOC_ID_COLUMN, PROJECT_COLUMN, DATA_COLUMN)
FIELDS_EXPAND: Final = f"fields($select={','.join(PLANNING_COLUMNS)})"
DEFAULT_PLANNING_LIST_NAME: Final = "ProjectFlow Planning"
PLANNING_LIST_COLUMNS: Final[tuple[dict[str, Any], ...]] = (
    {"name": KIND_COLUMN, "text": {}},
    {"name": DOC_ID_COLUMN, "text": {}},
    {"name": PROJECT_COLUMN, "text": {}},
    {"name": DATA_COLUMN, "text": {"allowMultipleLines": True, "textType": "plain"}},
)
HTTP_BAD_REQUEST = 400
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_GONE = 410
HTTP_PRECONDITION_FAILED = 412
RESYNC_APPLY_DIFFERENCES: Final = "resyncChangesApplyDifferences"
RESYNC_UPLOAD_DIFFERENCES: Final = "resyncChangesUploadDifferences"
# A 410 asks for a new full enumeration; failing again after that is a real error.
MAX_RESYNC_ATTEMPTS = 2
# Beyond this many items without column values, one paged listing costs less than a GET each.
BULK_FIELDS_THRESHOLD = 20
FIELDS_FETCH_CONCURRENCY = 4
TITLE_MAX_LENGTH = 255
CONFLICT_MESSAGE = (
    "Cet element du planning a ete modifie sur un autre poste entre-temps (412). "
    "Actualisez le planning puis recommencez la modification."
)


@dataclass(frozen=True, slots=True)
class PlanningListRef:
    site_id: str
    list_id: str


@dataclass(frozen=True, slots=True)
class PlanningRecord:
    item_id: str
    etag: str
    kind: str
    doc_id: str
    data: dict[str, Any] | None
    deleted: bool = False
    project: str = ""


@dataclass(frozen=True, slots=True)
class PlanningChanges:
    """Result of one delta read.

    When ``full`` is true (no delta link given, or Microsoft required a resync), the records
    list every live item. ``resync_reason`` keeps Microsoft's 410 code: after
    resyncChangesApplyDifferences (or a first read) local documents absent from the records
    are dropped, but after resyncChangesUploadDifferences local documents that are pending
    or absent from the records must be written again, not dropped (see
    ``drops_missing_local``). Deleted records only carry their item id.
    """

    records: tuple[PlanningRecord, ...]
    delta_link: str
    resynced: bool = False
    full: bool = False
    requests: int = 0
    fields_fetched: int = 0
    resync_reason: str = ""

    @property
    def uploads_local_differences(self) -> bool:
        return self.resync_reason.casefold() == RESYNC_UPLOAD_DIFFERENCES.casefold()

    @property
    def drops_missing_local(self) -> bool:
        """True when local documents absent from ``records`` must be removed locally."""
        return self.full and not self.uploads_local_differences


class PlanningConflictError(GraphError):
    """Raised when If-Match no longer matches the item: someone else saved it first."""

    def __init__(
        self,
        *,
        error_code: str | None = None,
        inner_error_code: str | None = None,
    ) -> None:
        super().__init__(
            CONFLICT_MESSAGE,
            status_code=HTTP_PRECONDITION_FAILED,
            error_code=error_code,
            inner_error_code=inner_error_code,
        )


@dataclass(slots=True)
class _Counter:
    requests: int = 0


class GraphListPlanningStore:
    def __init__(
        self,
        *,
        graph: GraphClient,
        list_ref: PlanningListRef,
        page_size: int | None = None,
    ) -> None:
        self._graph = graph
        self._list_ref = list_ref
        self._page_size = page_size
        self._expand_fields = True

    @property
    def list_ref(self) -> PlanningListRef:
        return self._list_ref

    @property
    def expands_fields(self) -> bool:
        """False once Microsoft refused $expand on delta: column values are then read apart."""
        return self._expand_fields

    async def aclose(self) -> None:
        await self._graph.aclose()

    def delta_url(self, token: str | None = None) -> str:
        """Return the delta request that starts a full enumeration, or uses ``token``."""
        return self._delta_url(token, expand=self._expand_fields)

    def _delta_url(self, token: str | None, *, expand: bool) -> str:
        params: list[str] = []
        if token:
            params.append(f"token={quote(token, safe='')}")
        if expand:
            params.append(f"$expand={FIELDS_EXPAND}")
        if self._page_size:
            params.append(f"$top={self._page_size}")
        url = f"{self._items_path}/delta"
        return f"{url}?{'&'.join(params)}" if params else url

    async def latest_delta_link(self) -> str:
        """Return a delta link reporting only the changes made from now on."""
        _items, delta_link = await self._collect_from_start("latest", _Counter())
        return delta_link

    async def changes(self, delta_link: str | None = None) -> PlanningChanges:
        counter = _Counter()
        link = delta_link
        resynced = False
        resync_reason = ""
        attempts = 0
        while True:
            try:
                if link:
                    raw_items, next_delta_link = await self._collect(link, counter)
                else:
                    raw_items, next_delta_link = await self._collect_from_start(None, counter)
                break
            except GraphError as exc:
                attempts += 1
                if exc.status_code != HTTP_GONE or attempts > MAX_RESYNC_ATTEMPTS:
                    raise
                # Microsoft puts a fresh enumeration link in the Location header, which
                # GraphClient does not expose, so the enumeration restarts from scratch.
                # An UploadDifferences request is kept over a later code: it never drops data.
                resynced = True
                link = None
                if resync_reason != RESYNC_UPLOAD_DIFFERENCES:
                    resync_reason = exc.error_code or ""

        decoded = {
            item_id: _record_from_delta_item(item_id, raw) for item_id, raw in raw_items.items()
        }
        missing = [item_id for item_id, record in decoded.items() if record is None]
        fetched = await self._fetch_records(missing, counter) if missing else {}
        records = tuple(
            decoded[item_id] or fetched.get(item_id) or _deleted_record(item_id)
            for item_id in decoded
        )
        return PlanningChanges(
            records=records,
            delta_link=next_delta_link,
            resynced=resynced,
            full=resynced or not delta_link,
            requests=counter.requests,
            fields_fetched=len(missing),
            resync_reason=resync_reason,
        )

    async def get(self, item_id: str) -> PlanningRecord | None:
        """Return the current version of one item, or None when it no longer exists."""
        return await self._get_record(item_id, _Counter())

    async def create(
        self,
        kind: PlanningKind,
        doc_id: str,
        data: Mapping[str, Any],
        project: str = "",
    ) -> PlanningRecord:
        if kind not in PLANNING_KINDS:
            raise ValueError(f"Type de document planning inconnu: {kind}")
        normalized_doc_id = doc_id.strip()
        if not normalized_doc_id:
            raise ValueError("Identifiant de document planning manquant.")
        encoded = _encode(data)
        normalized_project = project.strip()
        fields = {
            "Title": f"{kind}:{normalized_doc_id}"[:TITLE_MAX_LENGTH],
            KIND_COLUMN: kind,
            DOC_ID_COLUMN: normalized_doc_id,
            PROJECT_COLUMN: normalized_project,
            DATA_COLUMN: encoded,
        }
        payload = await self._graph.post(self._items_path, json={"fields": fields})
        item_id = _string(payload.get("id"))
        if not item_id:
            raise GraphError("SharePoint n'a pas retourne l'identifiant de l'element cree.")
        etag = _item_etag(payload) or await self._current_etag(item_id)
        return PlanningRecord(
            item_id=item_id,
            etag=etag,
            kind=kind,
            doc_id=normalized_doc_id,
            data=_parse_data(encoded),
            project=normalized_project,
        )

    async def update(
        self,
        record_or_item_id: PlanningRecord | str,
        etag: str,
        data: Mapping[str, Any],
        project: str | None = None,
    ) -> str:
        """Replace the document of one item if ``etag`` is still current; return the new etag."""
        item_id = (
            record_or_item_id.item_id
            if isinstance(record_or_item_id, PlanningRecord)
            else record_or_item_id
        )
        fields = {DATA_COLUMN: _encode(data)}
        if project is not None:
            fields[PROJECT_COLUMN] = project.strip()
        try:
            payload = await self._graph.patch(
                f"{self._item_path(item_id)}/fields",
                json=fields,
                headers={"If-Match": _if_match(etag)},
            )
        except GraphError as exc:
            raise _conflict_or_same(exc) from exc
        return _item_etag(payload) or await self._current_etag(item_id)

    async def delete(self, item_id: str, etag: str) -> None:
        """Delete one item if ``etag`` is still current; an already deleted item is fine."""
        try:
            await self._graph.delete(
                self._item_path(item_id),
                headers={"If-Match": _if_match(etag)},
            )
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                return
            raise _conflict_or_same(exc) from exc

    @property
    def _items_path(self) -> str:
        return f"/sites/{self._list_ref.site_id}/lists/{self._list_ref.list_id}/items"

    def _item_path(self, item_id: str) -> str:
        normalized = item_id.strip()
        if not normalized:
            raise ValueError("Identifiant d'element SharePoint manquant.")
        return f"{self._items_path}/{quote(normalized, safe='')}"

    async def _collect_from_start(
        self,
        token: str | None,
        counter: _Counter,
    ) -> tuple[dict[str, dict[str, Any]], str]:
        try:
            return await self._collect(self.delta_url(token), counter)
        except GraphError as exc:
            if exc.status_code != HTTP_BAD_REQUEST or not self._expand_fields:
                raise
        # Retry without $expand; only a success proves that $expand was the problem. Column
        # values then come from the reads in changes().
        collected = await self._collect(self._delta_url(token, expand=False), counter)
        self._expand_fields = False
        return collected

    async def _collect(
        self,
        link: str,
        counter: _Counter,
    ) -> tuple[dict[str, dict[str, Any]], str]:
        items: dict[str, dict[str, Any]] = {}
        next_link = link
        while True:
            counter.requests += 1
            payload = await self._graph.get(next_link)
            for raw in _values(payload):
                item_id = _string(raw.get("id"))
                if not item_id:
                    continue
                # The same item may come back several times: the last occurrence wins.
                items.pop(item_id, None)
                items[item_id] = raw
            following = payload.get("@odata.nextLink")
            if isinstance(following, str) and following and following != next_link:
                next_link = following
                continue
            delta_link = payload.get("@odata.deltaLink")
            if isinstance(delta_link, str) and delta_link:
                return items, delta_link
            raise GraphError(
                "Microsoft Graph n'a pas retourne de lien de suivi des modifications "
                "du planning. Actualisez le planning.",
            )

    async def _fetch_records(
        self,
        item_ids: Sequence[str],
        counter: _Counter,
    ) -> dict[str, PlanningRecord]:
        if len(item_ids) > BULK_FIELDS_THRESHOLD:
            return await self._list_records(set(item_ids), counter)
        semaphore = asyncio.Semaphore(FIELDS_FETCH_CONCURRENCY)
        records: dict[str, PlanningRecord] = {}

        async def fetch(item_id: str) -> None:
            async with semaphore:
                if (record := await self._get_record(item_id, counter)) is not None:
                    records[item_id] = record

        # A TaskGroup cancels the other reads as soon as one fails.
        try:
            async with asyncio.TaskGroup() as group:
                for item_id in item_ids:
                    group.create_task(fetch(item_id))
        except BaseExceptionGroup as failure:
            raise failure.exceptions[0] from None
        return {item_id: records[item_id] for item_id in item_ids if item_id in records}

    async def _list_records(
        self,
        wanted: set[str],
        counter: _Counter,
    ) -> dict[str, PlanningRecord]:
        records: dict[str, PlanningRecord] = {}
        link = f"{self._items_path}?$expand={FIELDS_EXPAND}"
        while link:
            counter.requests += 1
            payload = await self._graph.get(link)
            for raw in _values(payload):
                item_id = _string(raw.get("id"))
                if item_id in wanted and (record := _record_from_item(item_id, raw)) is not None:
                    records[item_id] = record
            following = payload.get("@odata.nextLink")
            link = following if isinstance(following, str) and following != link else ""
        return records

    async def _get_record(self, item_id: str, counter: _Counter) -> PlanningRecord | None:
        counter.requests += 1
        try:
            payload = await self._graph.get(f"{self._item_path(item_id)}?$expand={FIELDS_EXPAND}")
        except GraphError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                return None
            raise
        return _record_from_item(item_id, payload)

    async def _current_etag(self, item_id: str) -> str:
        payload = await self._graph.get(f"{self._item_path(item_id)}?$select=id,eTag")
        etag = _item_etag(payload)
        if not etag:
            raise GraphError(
                "SharePoint n'a pas retourne la version de l'element du planning. "
                "Actualisez le planning.",
            )
        return etag


async def group_site_id(graph: GraphClient, group_id: str) -> str:
    """Return the id of the SharePoint site of a Microsoft 365 group (the Planner plan's)."""
    normalized = group_id.strip()
    if not normalized:
        raise ConfigError("Groupe Microsoft 365 du planning non configure.")
    payload = await graph.get(
        f"/groups/{quote(normalized, safe='')}/sites/root?$select=id,webUrl,displayName",
    )
    site_id = _string(payload.get("id"))
    if not site_id:
        raise ConfigError("Impossible de trouver le site SharePoint du groupe Microsoft 365.")
    return site_id


async def site_id_from_url(graph: GraphClient, url: str) -> str:
    """Return the site id of an address such as https://entreprise.sharepoint.com/sites/xyz."""
    parts = urlsplit(url.strip())
    hostname = parts.hostname or ""
    if parts.scheme.casefold() != "https" or not hostname:
        raise ConfigError(
            "Adresse SharePoint invalide: utilisez une adresse du type "
            "https://entreprise.sharepoint.com/sites/nom-du-site.",
        )
    relative_path = _site_relative_path(parts.path)
    route = f"/sites/{hostname}:{quote(relative_path)}" if relative_path else f"/sites/{hostname}"
    payload = await graph.get(f"{route}?$select=id")
    site_id = _string(payload.get("id"))
    if not site_id:
        raise ConfigError(f"Site SharePoint introuvable: {url.strip()}")
    return site_id


async def find_list(graph: GraphClient, site_id: str, display_name: str) -> str | None:
    wanted = display_name.strip().casefold()
    link = f"/sites/{site_id}/lists?$select=id,displayName"
    while link:
        payload = await graph.get(link)
        for raw in _values(payload):
            list_id = _string(raw.get("id"))
            if list_id and _string(raw.get("displayName")).strip().casefold() == wanted:
                return list_id
        following = payload.get("@odata.nextLink")
        link = following if isinstance(following, str) and following != link else ""
    return None


async def create_planning_list(graph: GraphClient, site_id: str, display_name: str) -> str:
    """Create the planning list with its four columns and return its id.

    Microsoft requires Sites.Manage.All for this call: without it, ConfigError explains how
    to create the list by hand.
    """
    name = display_name.strip()
    if not name:
        raise ConfigError("Nom de la liste SharePoint du planning manquant.")
    body = {
        "displayName": name,
        "columns": [dict(column) for column in PLANNING_LIST_COLUMNS],
        "list": {"template": "genericList"},
    }
    try:
        payload = await graph.post(f"/sites/{site_id}/lists", json=body)
    except GraphError as exc:
        if exc.status_code == HTTP_FORBIDDEN:
            raise ConfigError(manual_list_instructions(name)) from exc
        if exc.status_code == HTTP_CONFLICT:
            existing = await find_list(graph, site_id, name)
            if existing:
                return existing
        raise
    list_id = _string(payload.get("id"))
    if not list_id:
        raise GraphError("SharePoint n'a pas retourne l'identifiant de la liste creee.")
    return list_id


async def planning_column_problems(
    graph: GraphClient,
    site_id: str,
    list_id: str,
) -> tuple[str, ...]:
    """Describe what is wrong with the list's columns (empty when the list is usable)."""
    payload = await graph.get(f"/sites/{site_id}/lists/{list_id}/columns?$select=name,text")
    columns = {
        name: raw
        for raw in _values(payload)
        if (name := _string(raw.get("name"))) in PLANNING_COLUMNS
    }
    problems = [
        f"Colonne {name} absente de la liste (nom interne exact attendu)."
        for name in PLANNING_COLUMNS
        if name not in columns
    ]
    for name, raw in columns.items():
        text = raw.get("text")
        if name == DATA_COLUMN:
            if (
                not isinstance(text, dict)
                or text.get("allowMultipleLines") is not True
                or text.get("textType", "plain") != "plain"
                or text.get("appendChangesToExistingText") is True
            ):
                problems.append(
                    f"Colonne {DATA_COLUMN}: type de colonne incorrect, elle doit etre de type "
                    "« Plusieurs lignes de texte » en texte brut (sans texte enrichi ni ajout "
                    "des modifications au texte existant).",
                )
        elif not isinstance(text, dict):
            problems.append(
                f"Colonne {name}: type de colonne incorrect, elle doit etre de type "
                "« Une seule ligne de texte ».",
            )
    return tuple(problems)


def manual_list_instructions(display_name: str) -> str:
    return (
        "Creation automatique de la liste SharePoint du planning refusee (403): Microsoft "
        "exige l'autorisation Sites.Manage.All pour creer une liste. Soit un administrateur "
        "Microsoft 365 accorde Sites.Manage.All a ProjectFlow Automator, soit creez la liste "
        "a la main dans le site SharePoint du groupe du plan Planner: Nouveau > Liste > "
        f"Liste vide, nom exact « {display_name} », puis ajoutez les colonnes "
        f"{KIND_COLUMN}, {DOC_ID_COLUMN} et {PROJECT_COLUMN} de type « Une seule ligne de "
        f"texte » et la colonne {DATA_COLUMN} de type « Plusieurs lignes de texte » en texte "
        "brut (texte enrichi desactive). Saisissez les noms de colonnes exactement ainsi, "
        "sans espace, puis relancez la configuration."
    )


def _record_from_delta_item(item_id: str, raw: Mapping[str, Any]) -> PlanningRecord | None:
    if isinstance(raw.get("deleted"), dict):
        return _deleted_record(item_id)
    return _record_from_item(item_id, raw)


def _record_from_item(item_id: str, raw: Mapping[str, Any]) -> PlanningRecord | None:
    """Return None when the payload lacks the column values or the version."""
    fields = raw.get("fields")
    etag = _item_etag(raw)
    if not isinstance(fields, dict) or KIND_COLUMN not in fields or not etag:
        return None
    return PlanningRecord(
        item_id=item_id,
        etag=etag,
        kind=_string(fields.get(KIND_COLUMN)),
        doc_id=_string(fields.get(DOC_ID_COLUMN)),
        data=_parse_data(fields.get(DATA_COLUMN)),
        project=_string(fields.get(PROJECT_COLUMN)),
    )


def _deleted_record(item_id: str) -> PlanningRecord:
    return PlanningRecord(item_id=item_id, etag="", kind="", doc_id="", data=None, deleted=True)


def _conflict_or_same(exc: GraphError) -> GraphError:
    if exc.status_code == HTTP_PRECONDITION_FAILED:
        return PlanningConflictError(
            error_code=exc.error_code,
            inner_error_code=exc.inner_error_code,
        )
    return exc


def _if_match(etag: str) -> str:
    normalized = etag.strip()
    if not normalized:
        raise ValueError("Version (etag) de l'element du planning manquante.")
    return normalized


def _encode(data: Mapping[str, Any]) -> str:
    return json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"))


def _parse_data(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _item_etag(payload: Mapping[str, Any]) -> str:
    return _string(payload.get("eTag")) or _string(payload.get("@odata.etag"))


def _site_relative_path(path: str) -> str:
    segments = [segment for segment in unquote(path).split("/") if segment]
    if len(segments) >= 2 and segments[0].casefold() in {"sites", "teams"}:  # noqa: PLR2004
        return f"/{segments[0]}/{segments[1]}"
    return ""


def _values(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("value", [])
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
