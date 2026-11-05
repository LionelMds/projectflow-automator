"""Outlook calendar of the signed-in account, through Microsoft Graph (Calendars.ReadWrite).

Used by ``core.planning.outlook_sync`` to copy the planning into one calendar. The key
and the fingerprint of each event live in extended properties of ProjectFlow's own name
space: events added by hand in that calendar are never touched.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import quote

from projectflow.core.planning.outlook_sync import EventSpec, ExistingEvent
from projectflow.exceptions import GraphError
from projectflow.graph.client import GraphClient

PROPERTY_SET: Final = "{6f1c8d52-3a4e-4b8e-9d1a-5b2f0c7e9a41}"
KEY_PROPERTY: Final = f"String {PROPERTY_SET} Name ProjectFlowKey"
FINGERPRINT_PROPERTY: Final = f"String {PROPERTY_SET} Name ProjectFlowFingerprint"
TIME_ZONE: Final = "W. Europe Standard Time"
"""Windows name of Swiss time, as Graph and Outlook know it."""
PAGE_SIZE: Final = 200
NOT_FOUND: Final = 404


class OutlookCalendarGateway:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def ensure_calendar(self, name: str) -> str:
        """Id of the calendar ``name`` (found without case), created when missing."""
        wanted = name.strip().casefold()
        path: str | None = "/me/calendars?$select=id,name&$top=100"
        while path:
            page = await self._graph.get(path)
            for item in page.get("value", []):
                if str(item.get("name", "")).strip().casefold() == wanted:
                    return str(item["id"])
            path = page.get("@odata.nextLink")
        created = await self._graph.post("/me/calendars", json={"name": name.strip()})
        return str(created["id"])

    async def list_events(self, calendar_id: str) -> dict[str, ExistingEvent]:
        """Events of ours in the calendar, by key (the others are left out)."""
        expand = quote(
            f"singleValueExtendedProperties($filter=id eq '{KEY_PROPERTY}' "
            f"or id eq '{FINGERPRINT_PROPERTY}')",
            safe="$=()'",
        )
        path: str | None = (
            f"/me/calendars/{calendar_id}/events?$select=id&$top={PAGE_SIZE}&$expand={expand}"
        )
        found: dict[str, ExistingEvent] = {}
        while path:
            page = await self._graph.get(path)
            for item in page.get("value", []):
                values = {
                    str(prop.get("id", "")).casefold(): str(prop.get("value", ""))
                    for prop in item.get("singleValueExtendedProperties", []) or []
                }
                key = values.get(KEY_PROPERTY.casefold(), "")
                if key:
                    found[key] = ExistingEvent(
                        str(item["id"]), values.get(FINGERPRINT_PROPERTY.casefold(), "")
                    )
            path = page.get("@odata.nextLink")
        return found

    async def create_event(self, calendar_id: str, spec: EventSpec) -> str:
        payload = event_payload(spec)
        payload["singleValueExtendedProperties"] = [
            {"id": KEY_PROPERTY, "value": spec.key},
            {"id": FINGERPRINT_PROPERTY, "value": spec.fingerprint},
        ]
        created = await self._graph.post(f"/me/calendars/{calendar_id}/events", json=payload)
        return str(created["id"])

    async def update_event(self, event_id: str, spec: EventSpec) -> None:
        payload = event_payload(spec)
        payload["singleValueExtendedProperties"] = [
            {"id": FINGERPRINT_PROPERTY, "value": spec.fingerprint},
        ]
        await self._graph.patch(f"/me/events/{event_id}", json=payload)

    async def delete_event(self, event_id: str) -> None:
        try:
            await self._graph.delete(f"/me/events/{event_id}")
        except GraphError as exc:
            if exc.status_code == NOT_FOUND:
                return  # Already removed in Outlook: nothing left to do.
            raise


def event_payload(spec: EventSpec) -> dict[str, Any]:
    """Graph body of ``spec``: no reminder, no invitation, « busy » or « tentative »."""
    if spec.all_day:
        start, end = spec.start.date().isoformat(), spec.end.date().isoformat()
        start_text, end_text = f"{start}T00:00:00", f"{end}T00:00:00"
    else:
        start_text = spec.start.isoformat(timespec="seconds")
        end_text = spec.end.isoformat(timespec="seconds")
    return {
        "subject": spec.subject,
        "body": {"contentType": "text", "content": spec.body},
        "start": {"dateTime": start_text, "timeZone": TIME_ZONE},
        "end": {"dateTime": end_text, "timeZone": TIME_ZONE},
        "isAllDay": spec.all_day,
        "location": {"displayName": spec.location},
        "showAs": "tentative" if spec.tentative else "busy",
        "isReminderOn": False,
        "responseRequested": False,
    }
