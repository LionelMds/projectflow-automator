"""Appointments of another calendar read from .ics and turned into planning tasks."""

from __future__ import annotations

from datetime import UTC, date, datetime

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.calendar_import import (
    ImportedEvent,
    drafts_for_drop,
    event_estimate,
    find_dossier,
    parse_ics,
)
from projectflow.core.planning.quick_entry import ProjectInfo
from projectflow.core.planning.walltime import day_start

CALENDAR = WorkCalendar(cantons=("GE",))
PROJECTS = {
    "2026-5154": ProjectInfo("2026-5154", client="Bouvier", designation="Porte d'entrée"),
    "2025-4987": ProjectInfo("2025-4987", client="Rossier", designation="Escalier extérieur"),
    "2024-4493-A1": ProjectInfo("2024-4493-A1", designation="Hall A1"),
}


def find(number: str) -> ProjectInfo | None:
    return PROJECTS.get(number)


def wall(day: date, hour: int, minute: int = 0) -> int:
    return day_start(day) + hour * 60 + minute


ICS = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:2026-5154 - Pose porte d'entrée\\, étage 2\r\n"
    'DTSTART;TZID="W. Europe Standard Time":20261112T080000\r\n'
    "DTEND;TZID=W. Europe Standard Time:20261112T120000\r\n"
    "LOCATION:Rue du Lac 4\\, Genève\r\n"
    "DESCRIPTION:Clé chez le concierge.\\nAppeler avant.\r\n"
    "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:Vacances\r\n"
    "DTSTART;VALUE=DATE:20261116\r\n"
    "DTEND;VALUE=DATE:20261121\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:Annulé\r\n"
    "STATUS:CANCELLED\r\n"
    "DTSTART:20261113T080000\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:Une ligne très longue qui conti\r\n"
    " nue sur la suivante\r\n"
    "DTSTART:20261113T070000Z\r\n"
    "DURATION:PT2H30M\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VTODO\r\n"
    "SUMMARY:Commander les gonds\r\n"
    "DUE;VALUE=DATE:20261118\r\n"
    "END:VTODO\r\n"
    "END:VCALENDAR\r\n"
)


def test_an_ics_gives_its_appointments_in_local_time() -> None:
    events = parse_ics(ICS)
    assert [event.subject for event in events] == [
        "2026-5154 - Pose porte d'entrée, étage 2",
        "Vacances",
        "Une ligne très longue qui continue sur la suivante",
        "Commander les gonds",
    ]
    pose, holidays, folded, todo = events
    assert (pose.start, pose.end, pose.all_day) == (
        datetime(2026, 11, 12, 8),  # noqa: DTZ001 - local wall time
        datetime(2026, 11, 12, 12),  # noqa: DTZ001 - local wall time
        False,
    )
    assert pose.location == "Rue du Lac 4, Genève"
    assert pose.notes == "Clé chez le concierge.\nAppeler avant."
    assert (holidays.start, holidays.end, holidays.all_day) == (
        datetime(2026, 11, 16),  # noqa: DTZ001 - local wall time
        datetime(2026, 11, 21),  # noqa: DTZ001 - local wall time
        True,
    )
    local = datetime(2026, 11, 13, 7, tzinfo=UTC).astimezone().replace(tzinfo=None)
    assert folded.start == local
    assert folded.end is not None
    assert (folded.end - local).total_seconds() == 150 * 60
    assert (todo.start, todo.all_day) == (datetime(2026, 11, 18), True)  # noqa: DTZ001


def test_an_ics_without_calendar_gives_nothing() -> None:
    assert parse_ics("bonjour") == []


def test_the_dossier_is_found_in_the_subject() -> None:
    assert find_dossier("2026-5154 - Pose porte", find, default_year=2026) == (
        "2026-5154",
        PROJECTS["2026-5154"],
        "Pose porte",
    )
    # A short number counts only when the répertoire knows it, this year or the last.
    assert find_dossier("Pose 5154 Bouvier", find, default_year=2026)[:2] == (
        "2026-5154",
        PROJECTS["2026-5154"],
    )
    assert find_dossier("RDV 4987 escalier", find, default_year=2026)[0] == "2025-4987"
    assert find_dossier("Livraison 1200 kg à 8h", find, default_year=2026) == (
        "",
        None,
        "Livraison 1200 kg à 8h",
    )
    # Written by hand, a full number is kept even unknown (another year is looked up).
    assert find_dossier("2024 \u2013 4493 a1 : contrôle", find, default_year=2026) == (
        "2024-4493-A1",
        PROJECTS["2024-4493-A1"],
        "contrôle",
    )
    assert find_dossier("2026-5102 REV03 Pose", find, default_year=2026)[::2] == (
        "2026-5102-REV03",
        "Pose",
    )
    assert find_dossier("2023-1234 - x", find, default_year=2026)[:2] == ("2023-1234", None)


def test_the_length_of_an_appointment_is_its_work() -> None:
    thursday = ImportedEvent(
        "x",
        datetime(2026, 11, 12, 10),  # noqa: DTZ001
        datetime(2026, 11, 12, 15),  # noqa: DTZ001
    )
    assert event_estimate(thursday, CALENDAR) == (240, 0.0)  # lunch left out
    evening = ImportedEvent(
        "x",
        datetime(2026, 11, 12, 19),  # noqa: DTZ001
        datetime(2026, 11, 12, 20, 40),  # noqa: DTZ001
    )
    assert event_estimate(evening, CALENDAR) == (90, 0.0)
    week = ImportedEvent(
        "x",
        datetime(2026, 11, 13),  # noqa: DTZ001 - a Friday
        datetime(2026, 11, 18),  # noqa: DTZ001
        all_day=True,
    )
    assert event_estimate(week, CALENDAR) == (0, 3.0)  # Friday, Monday, Tuesday
    assert event_estimate(ImportedEvent("mail"), CALENDAR) == (0, 0.0)


def test_dropped_in_a_column_it_keeps_its_length_and_no_date() -> None:
    events = parse_ics(ICS)[:1]
    (draft,) = drafts_for_drop(events, find_project=find, default_year=2026, calendar=CALENDAR)
    assert (draft.title, draft.project_number, draft.start) == (
        "Pose porte d'entrée, étage 2",
        "2026-5154",
        None,
    )
    assert draft.estimate_minutes == 240
    assert draft.notes == "Lieu : Rue du Lac 4, Genève\nClé chez le concierge.\nAppeler avant."


def test_dropped_on_a_day_they_keep_their_hours_and_their_days_apart() -> None:
    events = [
        ImportedEvent(
            "2026-5154 - Porte d'entrée",
            datetime(2026, 11, 12, 8),  # noqa: DTZ001 - Thursday
            datetime(2026, 11, 12, 12),  # noqa: DTZ001
        ),
        ImportedEvent(
            "Réglage 5154",
            datetime(2026, 11, 13, 5),  # noqa: DTZ001 - Friday, before the work starts
            datetime(2026, 11, 13, 9),  # noqa: DTZ001
        ),
        ImportedEvent("Mail à traiter"),
    ]
    tuesday = date(2026, 11, 17)
    drop = wall(tuesday, 13)
    drafts = drafts_for_drop(
        events, find_project=find, default_year=2026, calendar=CALENDAR, drop_start=drop
    )
    assert [d.start for d in drafts] == [
        wall(tuesday, 8),
        wall(date(2026, 11, 18), 6, 30),
        drop,
    ]
    # Only the designation: the title stays empty, the task shows the dossier.
    assert [d.title for d in drafts] == ["", "Réglage", "Mail à traiter"]
    assert [d.estimate_minutes for d in drafts] == [240, 150, 0]
