from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest

from projectflow.core.planning.calendar import WorkCalendar
from projectflow.core.planning.models import (
    DEFAULT_TASK_TYPES,
    DEFAULT_TEAMS,
    AbsenceReason,
    DayPart,
    Resource,
    Team,
)
from projectflow.core.planning.quick_entry import (
    UNREADABLE_WARNING,
    AbsenceDraft,
    EntryContext,
    ParsedEntry,
    ProjectInfo,
    fold_text,
    format_days,
    format_duration,
    format_span,
    parse_entry,
)
from projectflow.core.planning.walltime import (
    WallTime,
    format_wall_time,
    minute_of_day,
    parse_wall_time,
)

TODAY = date(2026, 10, 22)  # a Thursday
RIGHT_QUOTE = chr(0x2019)
COMBINING_ACUTE = chr(0x301)

RESOURCES = (
    Resource(id="lbi", name="Luca Bianchi", initials="LBI", team="montage"),
    Resource(id="ych", name="Yann Chappuis", initials="YCH", team="montage"),
    Resource(id="jmf", name="Jean-Marc Fasel", initials="JMF", team="montage"),
    Resource(id="mdu2", name="Michel Duc", initials="MDU", team="montage"),
    Resource(id="oti", name="Olivier Tissot", initials="OTI", team="montage", active=False),
    Resource(id="kdu", name="Kevin Dupuis", initials="KDU", team="atelier"),
    Resource(id="tfa", name="Thomas Favre", initials="TFA", team="atelier"),
    Resource(id="jro", name="Jean Rochat", initials="JRO", team="atelier"),
    Resource(id="mdu1", name="Marc Dupraz", initials="MDU", team="atelier"),
    Resource(id="de", name="David Emery", initials="DE", team="atelier"),
    Resource(id="le", name="Laurent Egger", initials="LE", team="atelier"),
    Resource(id="nre", name="Noah Rey", initials="NRE", team="atelier", apprentice=True),
    Resource(id="sbe", name="Sébastien Berset", initials="SBE", team="bureau"),
    Resource(id="pm", name="Pierre Monnet", initials="PM", team="bureau"),
    Resource(
        id="thl",
        name="Thermo-Lac SA",
        initials="THL",
        team="soustraitants",
        kind_of_resource="subcontractor",
    ),
)
MONTAGE_TEAM = ("lbi", "ych", "jmf", "mdu2")

PROJECTS = {
    number: ProjectInfo(number, client, "", designation, "LMO")
    for number, client, designation in (
        ("2026-121", "Commune de Marsens", "Marquise entrée école"),
        ("2026-124", "Gérance Rhône SA", "Portail coulissant"),
        ("2026-112", "M. Perret", "Escalier extérieur"),
        ("2026-129", "PPE Les Cèdres", "Garde-corps balcons"),
        ("2026-118", "Ville de Nyon", "Barrières parc"),
        ("2026-118-1", "Ville de Nyon", "Barrières parc, lot 2"),
        ("2026-109", "Mme Rossier", "Portail jardin"),
        ("2026-130", "Hôpital de Morges", "Pose main courante"),
        ("2026-5231", "Implenia SA", "Garde-corps immeuble"),
        ("2025-301", "Coop", "Auvent quai"),
    )
}

CONTEXT = EntryContext(
    today=TODAY,
    week_start=date(2026, 10, 19),
    calendar=WorkCalendar(cantons=("GE",)),
    resources=RESOURCES,
    teams=DEFAULT_TEAMS,
    task_types=DEFAULT_TASK_TYPES,
    find_project=PROJECTS.get,
    default_year=2026,
)


def parse(text: str, **changes: Any) -> ParsedEntry:
    return parse_entry(text, replace(CONTEXT, **changes))


def at(text: str) -> WallTime:
    return parse_wall_time(text)


def instants(entry: ParsedEntry) -> tuple[str | None, str | None]:
    start = format_wall_time(entry.start) if entry.start is not None else None
    end = format_wall_time(entry.end) if entry.end is not None else None
    return start, end


# ------------------------------------------------------------ destination ----


@pytest.mark.parametrize(
    ("text", "destination"),
    [
        ("2026-121 montage marquise jeu-ven LBI YCH", "grid"),
        ("124 débit portail 1,5j KDU lun", "grid"),
        ("livraison 112 ven matin", "unassigned"),
        ("129 relevé de cotes 2p", "backlog"),
        ("thermolaquage RAL 7016 THL lun-mer", "grid"),
        ("/vac LBI 26.10-30.10", "absence"),
        ("5231 pose garde-corps +montage S45 ?", "grid"),
        ("jeu 7h30-11h métré 118 SBE", "grid"),
        ("rangement stock TFA lun", "grid"),
        ("sav portail 109 demain à 10h 1h30 !", "unassigned"),
        ("<06.11 129 fabrication 2j 2p", "backlog"),
        ("", "invalid"),
        ("   ", "invalid"),
        ("?", "invalid"),
        ("! ?", "invalid"),
        ("am", "invalid"),
        ("/vac 26.10", "invalid"),
        ("255 portail", "backlog"),
        ("LBI", "backlog"),
        ("pose portail", "backlog"),
        ("S44 121", "unassigned"),
        ("à 10h 121", "unassigned"),
        ("matin 121 KDU", "grid"),
        ("/ecole NRE ven", "absence"),
        ("MDU soudure lun", "unassigned"),
        ("lu LBI", "backlog"),
        ("me je ve LBI", "backlog"),
    ],
)
def test_destination(text: str, destination: str) -> None:
    assert parse(text).destination == destination


# ---------------------------------------------------------------- dossier ----


@pytest.mark.parametrize(
    ("text", "number", "client"),
    [
        ("2026-121 montage", "2026-121", "Commune de Marsens"),
        ("121 montage", "2026-121", "Commune de Marsens"),
        ("118-1 pose", "2026-118-1", "Ville de Nyon"),
        ("2026-118-1 pose", "2026-118-1", "Ville de Nyon"),
        ("5231 pose", "2026-5231", "Implenia SA"),
        ("301 auvent", "2025-301", "Coop"),
        ("255 portail", "2026-255", None),
        ("2025-118 portail", "2025-118", None),
        ("thermolaquage RAL 7016 THL", "", None),
        ("débit HEA 200 KDU", "", None),
        ("profil HEB 140 et IPE 120", "", None),
        ("inox 316 KDU", "", None),
        ("Ø 150 tube 121", "2026-121", "Commune de Marsens"),
        ("M12 120 vis 124", "2026-124", "Gérance Rhône SA"),
        ("150 pces débit 121", "2026-121", "Commune de Marsens"),
        ("200 x 100 tube", "", None),
        ("121 et 124 portails", "2026-121", "Commune de Marsens"),
        ("#118 portail", "", None),
        ("pose 2 portails 121", "2026-121", "Commune de Marsens"),
        ("<06.11 129 fabrication", "2026-129", "PPE Les Cèdres"),
        ("27.10 112 livraison", "2026-112", "M. Perret"),
        ("12345 pièces", "", None),
    ],
)
def test_dossier(text: str, number: str, client: str | None) -> None:
    entry = parse(text)

    assert entry.project_number == number
    assert (entry.project.client if entry.project is not None else None) == client


def test_dossier_lookup_tries_the_previous_year_with_normalised_numbers() -> None:
    asked: list[str] = []

    def find(number: str) -> ProjectInfo | None:
        asked.append(number)
        return PROJECTS.get(number)

    parse("301 auvent 118-01", find_project=find)

    assert asked == ["2026-301", "2025-301"]


def test_unknown_dossier_is_kept_with_a_warning() -> None:
    entry = parse("255 portail")

    assert entry.project is None
    assert entry.warnings == ("2026-255 introuvable dans le répertoire",)
    assert [(token.kind, token.ambiguous) for token in entry.tokens] == [("project", True)]


# ----------------------------------------------------------------- people ----


@pytest.mark.parametrize(
    ("text", "people"),
    [
        ("pose LBI YCH jeu", ("lbi", "ych")),
        ("pose lbi ych", ("lbi", "ych")),
        ("LBI, YCH pose", ("lbi", "ych")),
        ("LBI+YCH pose", ("lbi", "ych")),
        ("pose @lbi", ("lbi",)),
        ("pose Luca", ("lbi",)),
        ("pose luca bianchi", ("lbi",)),
        ("relevé Sébastien", ("sbe",)),
        ("relevé sebastien", ("sbe",)),
        ("relevé seb", ("sbe",)),
        ("soudure jean", ("jro",)),
        ("pose jean-marc", ("jmf",)),
        ("pose +montage", MONTAGE_TEAM),
        ("+bureau relevé", ("sbe", "pm")),
        ("+mont pose", MONTAGE_TEAM),
        ("galva THL", ("thl",)),
        ("galva thermo-lac sa", ("thl",)),
        ("relevé de cotes", ()),
        ("relevé DE cotes", ("de",)),
        ("pose le portail LE", ("le",)),
        ("LE pose la marquise", ("le",)),
        ("OTI pose", ()),
        ("pose LBI LBI", ("lbi",)),
        ("PM relevé", ("pm",)),
        ("lun pm KDU", ("kdu",)),
        ("+lbi +le", ("lbi", "le")),
        ("pose +montage LBI", MONTAGE_TEAM),
        ("thermolaquage 129", ()),
    ],
)
def test_people(text: str, people: tuple[str, ...]) -> None:
    assert parse(text).resource_ids == people


@pytest.mark.parametrize(
    ("text", "kind", "value", "message"),
    [
        (
            "MDU soudure",
            "person",
            "mdu2,mdu1",
            "« MDU » : plusieurs personnes possibles (MDU Michel Duc, MDU Marc Dupraz)",
        ),
        (
            "jea pose",
            "person",
            "jmf,jro",
            "« jea » : plusieurs personnes possibles (JMF Jean-Marc Fasel, JRO Jean Rochat)",
        ),
        ("+zzz pose", "person", "", "« +zzz » : personne ou équipe inconnue"),
        ("255 portail", "project", "2026-255", "2026-255 introuvable dans le répertoire"),
        ("am 118", "part", "", "« am » est ambigu : écrire « matin » ou « aprem »"),
        (
            "/sou 118",
            "type",
            "soudure,soustraitance",
            "« /sou » : plusieurs types possibles (Soudure / assemblage, Sous-traitance)",
        ),
        ("/xyz 118", "type", "", "Commande « /xyz » inconnue"),
        ("31.02 LBI", "date", "", "Date invalide : 31.02"),
        ("S60 LBI", "week", "", "Semaine invalide : S60"),
        ("25h LBI lun", "time", "", "Heure invalide : 25h"),
        ("0j 121", "duration", "", "Durée nulle : 0j"),
        ("<31.02 121", "deadline", "", "Date invalide : <31.02"),
        ("0p 121", "people", "", "Nombre de personnes invalide : 0p"),
        ("OTI pose lun", "person", "oti", "« OTI » : personne inactive (OTI Olivier Tissot)"),
        ("+oti pose", "person", "oti", "« +oti » : personne inactive (OTI Olivier Tissot)"),
        ("lun 3000j LBI", "duration", "", "Durée trop longue : 3000j"),
        ("27.10.2035 LBI", "date", "", "Date invalide : 27.10.2035"),
        ("27.10-03.10 LBI", "date", "", "Fin avant le début : 27.10-03.10"),
        ("S45-S44 LBI", "week", "", "Fin avant le début : S45-S44"),
        ("lun 0h LBI", "duration", "", "Durée nulle : 0h"),
    ],
)
def test_ambiguous_tokens(text: str, kind: str, value: str, message: str) -> None:
    entry = parse(text)
    ambiguous = [token for token in entry.tokens if token.ambiguous]

    assert [(token.kind, token.value, token.message) for token in ambiguous] == [
        (kind, value, message)
    ]
    assert message in entry.warnings


def test_ambiguous_people_are_not_assigned() -> None:
    entry = parse("MDU jea soudure lun")

    assert entry.resource_ids == ()
    assert entry.title == "soudure"
    assert len(entry.warnings) == 2


def test_team_without_active_member_warns() -> None:
    teams = (*DEFAULT_TEAMS, Team(key="peinture", name="Peinture", alias="peinture", order=5))

    entry = parse("+peinture lun", teams=teams)

    assert entry.resource_ids == ()
    assert entry.warnings == ("L'équipe Peinture n'a aucun membre actif",)


# --------------------------------------------------------------- schedule ----


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("2026-121 montage marquise jeu-ven LBI YCH", "2026-10-22T06:30", "2026-10-23T12:00"),
        ("124 débit portail 1,5j KDU lun", "2026-10-26T06:30", "2026-10-27T12:00"),
        ("livraison 112 ven matin", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("thermolaquage RAL 7016 THL lun-mer", "2026-10-26T06:30", "2026-10-28T16:30"),
        ("5231 pose garde-corps +montage S45 ?", "2026-11-02T06:30", "2026-11-06T12:00"),
        ("jeu 7h30-11h métré 118 SBE", "2026-10-22T07:30", "2026-10-22T11:00"),
        ("rangement stock TFA lun", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("sav portail 109 demain à 10h 1h30 !", "2026-10-23T10:00", "2026-10-23T11:30"),
        ("auj LBI", "2026-10-22T06:30", "2026-10-22T16:30"),
        ("aujourd'hui LBI", "2026-10-22T06:30", "2026-10-22T16:30"),
        ("demain LBI", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("après-demain LBI", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("lun LBI", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("mar LBI", "2026-10-27T06:30", "2026-10-27T16:30"),
        ("mercredi LBI", "2026-10-28T06:30", "2026-10-28T16:30"),
        ("jeudi LBI", "2026-10-22T06:30", "2026-10-22T16:30"),
        ("Vendredi LBI", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("jeu-mar LBI", "2026-10-22T06:30", "2026-10-27T16:30"),
        ("lun à mer LBI", "2026-10-26T06:30", "2026-10-28T16:30"),
        ("lun au mer LBI", "2026-10-26T06:30", "2026-10-28T16:30"),
        ("du lun au mer LBI", "2026-10-26T06:30", "2026-10-28T16:30"),
        ("lun prochain LBI", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("lun-dim LBI", "2026-10-26T06:30", "2026-10-30T12:00"),
        ("27.10 LBI", "2026-10-27T06:30", "2026-10-27T16:30"),
        ("27.10.2026 LBI", "2026-10-27T06:30", "2026-10-27T16:30"),
        ("27-29.10 LBI", "2026-10-27T06:30", "2026-10-29T16:30"),
        ("27.10-03.11 LBI", "2026-10-27T06:30", "2026-11-03T16:30"),
        ("du 27.10 au 30.10 LBI", "2026-10-27T06:30", "2026-10-30T12:00"),
        ("S44 LBI", "2026-10-26T06:30", "2026-10-30T12:00"),
        ("S44-S45 LBI", "2026-10-26T06:30", "2026-11-06T12:00"),
        ("S45 lun-mer LBI", "2026-11-02T06:30", "2026-11-04T16:30"),
        ("S45 jeu LBI", "2026-11-05T06:30", "2026-11-05T16:30"),
        ("S2 LBI", "2027-01-11T06:30", "2027-01-15T12:00"),
        ("lun matin LBI", "2026-10-26T06:30", "2026-10-26T12:00"),
        ("lun mat LBI", "2026-10-26T06:30", "2026-10-26T12:00"),
        ("lun aprem LBI", "2026-10-26T13:00", "2026-10-26T16:30"),
        ("lun apm LBI", "2026-10-26T13:00", "2026-10-26T16:30"),
        ("lun après-midi LBI", "2026-10-26T13:00", "2026-10-26T16:30"),
        ("lun pm KDU", "2026-10-26T13:00", "2026-10-26T16:30"),
        ("lun-mer matin LBI", "2026-10-26T06:30", "2026-10-28T12:00"),
        ("lun-mer aprem LBI", "2026-10-26T13:00", "2026-10-28T16:30"),
        ("ven aprem LBI", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("lun 13:00-15:30 LBI", "2026-10-26T13:00", "2026-10-26T15:30"),
        ("lun de 13h à 15h LBI", "2026-10-26T13:00", "2026-10-26T15:00"),
        ("lun 7h LBI", "2026-10-26T07:00", "2026-10-26T07:30"),
        ("lun à 10h LBI", "2026-10-26T10:00", "2026-10-26T10:30"),
        ("lun dès 7h 2h LBI", "2026-10-26T07:00", "2026-10-26T09:00"),
        ("lun à 10h 1j LBI", "2026-10-26T10:00", "2026-10-26T16:30"),
        ("lun matin 2h KDU", "2026-10-26T06:30", "2026-10-26T08:30"),
        ("lun 12h30 LBI", "2026-10-26T13:00", "2026-10-26T13:30"),
        ("ven à 12h LBI", "2026-10-26T06:30", "2026-10-26T07:00"),
        ("jeu 16h30 LBI", "2026-10-23T06:30", "2026-10-23T07:00"),
        ("25.12 LBI", "2026-12-28T06:30", "2026-12-28T16:30"),
        ("31.12 LBI", "2027-01-04T06:30", "2027-01-04T16:30"),
        ("sam LBI", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("dim LBI", "2026-10-26T06:30", "2026-10-26T16:30"),
        ("jeu 1j LBI", "2026-10-22T06:30", "2026-10-22T16:30"),
        ("ven 1j LBI", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("jeu 2j LBI", "2026-10-22T06:30", "2026-10-23T12:00"),
        ("jeu 3j LBI", "2026-10-22T06:30", "2026-10-26T16:30"),
        ("ven 2j LBI", "2026-10-23T06:30", "2026-10-26T16:30"),
        ("lun 5j LBI", "2026-10-26T06:30", "2026-10-30T12:00"),
        ("jeu 1j½ LBI", "2026-10-22T06:30", "2026-10-23T12:00"),
        ("27-29.10 2j LBI", "2026-10-27T06:30", "2026-10-29T16:30"),
        ("lun 3j LBI", "2026-10-26T06:30", "2026-10-28T16:30"),
        ("lun 2,5j LBI", "2026-10-26T06:30", "2026-10-28T12:00"),
        ("lun 0,5j LBI", "2026-10-26T06:30", "2026-10-26T12:00"),
        ("lun ½j LBI", "2026-10-26T06:30", "2026-10-26T12:00"),
        ("lun 1j½ LBI", "2026-10-26T06:30", "2026-10-27T12:00"),
        ("lun matin ½j LBI", "2026-10-26T06:30", "2026-10-26T12:00"),
        ("lun aprem ½j LBI", "2026-10-26T13:00", "2026-10-26T16:30"),
        ("lun à 10h ½j LBI", "2026-10-26T10:00", "2026-10-26T12:00"),
        ("lun à 14h ½j LBI", "2026-10-26T14:00", "2026-10-26T16:30"),
        ("ven aprem ½j LBI", "2026-10-23T06:30", "2026-10-23T12:00"),
        ("lun 1j 3h LBI", "2026-10-26T06:30", "2026-10-27T09:30"),
        ("jeu 1j 3h LBI", "2026-10-22T06:30", "2026-10-23T09:30"),
        ("lun 4h LBI", "2026-10-26T06:30", "2026-10-26T10:30"),
        ("lun 1h30 LBI", "2026-10-26T06:30", "2026-10-26T08:00"),
        ("lun 90min LBI", "2026-10-26T06:30", "2026-10-26T08:00"),
        ("lun 1j 4h LBI", "2026-10-26T06:30", "2026-10-27T10:30"),
        ("lun 11h-7h LBI", "2026-10-26T11:00", "2026-10-26T11:30"),
        ("pose portail 3j", None, None),
    ],
)
def test_schedule(text: str, start: str | None, end: str | None) -> None:
    assert instants(parse(text)) == (start, end)


@pytest.mark.parametrize(
    ("text", "changes", "start", "end"),
    [
        ("lun LBI", {"week_start": date(2026, 11, 2)}, "2026-11-02T06:30", "2026-11-02T16:30"),
        ("jeu LBI", {"week_start": date(2026, 11, 2)}, "2026-11-05T06:30", "2026-11-05T16:30"),
        ("lun LBI", {"week_start": date(2026, 10, 12)}, "2026-10-12T06:30", "2026-10-12T16:30"),
        (
            "demain LBI",
            {"today": date(2026, 10, 23)},
            "2026-10-26T06:30",
            "2026-10-26T16:30",
        ),
        (
            "31.12 LBI",
            {"calendar": WorkCalendar(cantons=("VD",))},
            "2026-12-31T06:30",
            "2026-12-31T16:30",
        ),
        (
            "02.01 LBI",
            {"calendar": WorkCalendar(cantons=("VD",))},
            "2027-01-04T06:30",
            "2027-01-04T16:30",
        ),
    ],
)
def test_schedule_depends_on_the_context(
    text: str, changes: dict[str, Any], start: str, end: str
) -> None:
    assert instants(parse(text, **changes)) == (start, end)


# ------------------------------------------------------------------ cells ----

DAY_CELL = {
    "cell_start": at("2026-10-26T06:30"),
    "cell_end": at("2026-10-26T16:30"),
    "cell_resource_ids": ("kdu",),
}
RANGE_CELL = {
    "cell_start": at("2026-10-26T06:30"),
    "cell_end": at("2026-10-28T16:30"),
    "cell_resource_ids": ("lbi", "ych"),
}
HALF_HOUR_CELL = {
    "cell_start": at("2026-10-26T09:00"),
    "cell_end": at("2026-10-26T09:30"),
    "cell_resource_ids": ("sbe",),
}


@pytest.mark.parametrize(
    ("text", "cell", "people", "start", "end"),
    [
        ("124 débit", DAY_CELL, ("kdu",), "2026-10-26T06:30", "2026-10-26T16:30"),
        ("124 débit 1,5j", DAY_CELL, ("kdu",), "2026-10-26T06:30", "2026-10-27T12:00"),
        ("118 métré ½j", HALF_HOUR_CELL, ("sbe",), "2026-10-26T09:00", "2026-10-26T12:00"),
        ("124 débit LBI", DAY_CELL, ("lbi",), "2026-10-26T06:30", "2026-10-26T16:30"),
        ("124 débit matin", DAY_CELL, ("kdu",), "2026-10-26T06:30", "2026-10-26T12:00"),
        ("124 débit 7h30-11h", DAY_CELL, ("kdu",), "2026-10-26T07:30", "2026-10-26T11:00"),
        ("124 débit jeu", DAY_CELL, ("kdu",), "2026-10-22T06:30", "2026-10-22T16:30"),
        ("124 débit 2h", DAY_CELL, ("kdu",), "2026-10-26T06:30", "2026-10-26T08:30"),
        ("124 débit à 10h", DAY_CELL, ("kdu",), "2026-10-26T10:00", "2026-10-26T16:30"),
        ("124 débit 7h", DAY_CELL, ("kdu",), "2026-10-26T06:30", "2026-10-26T14:30"),
        ("118 métré 7h", HALF_HOUR_CELL, ("sbe",), "2026-10-26T09:00", "2026-10-27T07:00"),
        ("121 pose", RANGE_CELL, ("lbi", "ych"), "2026-10-26T06:30", "2026-10-28T16:30"),
        ("118 métré", HALF_HOUR_CELL, ("sbe",), "2026-10-26T09:00", "2026-10-26T09:30"),
    ],
)
def test_cell_context(
    text: str,
    cell: dict[str, Any],
    people: tuple[str, ...],
    start: str,
    end: str,
) -> None:
    entry = parse(text, **cell)

    assert entry.destination == "grid"
    assert entry.resource_ids == people
    assert instants(entry) == (start, end)


def test_range_over_several_rows_needs_everyone() -> None:
    entry = parse("121 pose", **RANGE_CELL)

    assert entry.required_people == 2
    assert entry.estimate_minutes == 1620


@pytest.mark.parametrize(
    ("cell", "first", "last", "people"),
    [
        (DAY_CELL, date(2026, 10, 26), date(2026, 10, 26), ("kdu",)),
        (RANGE_CELL, date(2026, 10, 26), date(2026, 10, 28), ("lbi", "ych")),
    ],
)
def test_absence_on_a_cell(
    cell: dict[str, Any], first: date, last: date, people: tuple[str, ...]
) -> None:
    entry = parse("/vac", **cell)

    assert entry.destination == "absence"
    assert entry.absence == AbsenceDraft(people, "vacances", first, last, "day")


# -------------------------------------------------------------- durations ----


@pytest.mark.parametrize(
    ("text", "estimate", "days", "ignored"),
    [
        ("3j 121", 1620, 3, False),
        ("2,5j 121", 1350, 2.5, False),
        ("0,5j 121", 270, 0.5, False),
        ("½j 121", 270, 0.5, False),
        ("1j½ 121", 810, 1.5, False),
        # The app's own format, typed back.
        ("1 j ½ 121", 810, 1.5, False),
        ("2 j ½ 121", 1350, 2.5, False),
        ("1j ½ 121", 810, 1.5, False),
        ("1 ½ j 121", 810, 1.5, False),
        ("1 1/2 j 121", 810, 1.5, False),
        ("1 1/2j 121", 810, 1.5, False),
        ("1j1/2 121", 810, 1.5, False),
        ("1 jour et demi 121", 810, 1.5, False),
        ("2 journées et demie 121", 1350, 2.5, False),
        ("1/2j 121", 270, 0.5, False),
        ("demi-journée 121", 270, 0.5, False),
        ("2 jours 121", 1080, 2, False),
        ("2j 1j 121", 1620, 3, False),
        ("4h 121", 240, 0, False),
        ("1h30 121", 90, 0, False),
        ("90min 121", 90, 0, False),
        ("1,5h 121", 90, 0, False),
        ("4 heures 121", 240, 0, False),
        ("1j 4h 121", 780, 0, False),
        ("soudure 8h", 480, 0, False),
        ("100min 121", 90, 0, False),
        ("1h15 121", 90, 0, False),
        ("2,25j 121", 1350, 2.5, False),
        ("0,2j 121", 270, 0.5, False),
        ("27-29.10 2j LBI", 1620, 3, True),
        ("jeu-ven 3h LBI", 870, 2, True),
        ("lun 7h30-11h 2h LBI", 210, 0, True),
        ("lun LBI", 540, 1, False),
        ("ven LBI", 330, 1, False),
        ("lun matin LBI", 330, 0.5, False),
        ("S44 LBI", 2490, 5, False),
        ("jeu 2j LBI", 870, 2, False),
        ("lun 5j LBI", 2490, 5, False),
        ("lun aprem ½j LBI", 210, 0.5, False),
        # Typed days stay as typed once dated, even where whole_days reads otherwise.
        ("lun à 10h 1j LBI", 330, 1, False),
        ("ven ½j LBI", 330, 0.5, False),
        ("jeu 1j½ LBI", 870, 1.5, False),
        # An afternoon start is half of its day: 1 j ends on Tuesday at noon.
        ("lun aprem 1j LBI", 540, 1, False),
        ("jeu aprem 2j LBI", 870, 2, False),
        ("lun 1j 4h LBI", 780, 0, False),
        ("lun 4h LBI", 240, 0, False),
        ("pose portail", 0, 0, False),
        ("/vac LBI lun 2j", 0, 0, False),
    ],
)
def test_duration(text: str, estimate: int, days: float, *, ignored: bool) -> None:
    entry = parse(text)

    assert entry.estimate_minutes == estimate
    assert entry.estimate_days == days
    assert entry.ignored_duration is ignored


GENEVA = WorkCalendar(cantons=("GE",))
VAUD = WorkCalendar(cantons=("VD",))


@pytest.mark.parametrize(
    ("text", "calendar", "start", "end", "days"),
    [
        # Jeûne genevois: Thursday 9 September 2027 in Geneva only.
        ("08.09 2j LBI", GENEVA, "2027-09-08T06:30", "2027-09-10T12:00", 2),
        ("08.09 2j LBI", VAUD, "2027-09-08T06:30", "2027-09-09T16:30", 2),
        ("09.09 1j LBI", GENEVA, "2027-09-10T06:30", "2027-09-10T12:00", 1),
        # Lundi du Jeûne: Monday 20 September 2027 in Vaud only.
        ("17.09 2j LBI", VAUD, "2027-09-17T06:30", "2027-09-21T16:30", 2),
        ("17.09 2j LBI", GENEVA, "2027-09-17T06:30", "2027-09-20T16:30", 2),
        ("20.09 ½j LBI", VAUD, "2027-09-21T06:30", "2027-09-21T12:00", 0.5),
        ("24.12 2j LBI", GENEVA, "2026-12-24T06:30", "2026-12-28T16:30", 2),
    ],
)
def test_days_skip_the_holidays_of_the_canton(
    text: str, calendar: WorkCalendar, start: str, end: str, days: float
) -> None:
    entry = parse(text, calendar=calendar)

    assert instants(entry) == (start, end)
    assert entry.estimate_days == days


@pytest.mark.parametrize(
    ("text", "calendar", "warning"),
    [
        (
            "09.09 1j LBI",
            GENEVA,
            "jeu 09.09.2027 n'est pas travaillé (Jeûne genevois) : début le ven 10.09.2027",
        ),
        (
            "20.09 ½j LBI",
            VAUD,
            "lun 20.09.2027 n'est pas travaillé (Lundi du Jeûne) : début le mar 21.09.2027",
        ),
    ],
)
def test_a_start_on_a_cantonal_holiday_warns(
    text: str, calendar: WorkCalendar, warning: str
) -> None:
    assert parse(text, calendar=calendar).warnings == (warning,)


def test_days_follow_the_canton_of_the_first_person() -> None:
    geneva, vaud = WorkCalendar(cantons=("GE",)), WorkCalendar(cantons=("VD",))

    def calendar_for(resource_id: str | None) -> WorkCalendar:
        return vaud if resource_id == "lbi" else geneva

    in_vaud = parse("08.09 2j LBI KDU", calendar_for=calendar_for)
    in_geneva = parse("08.09 2j KDU LBI", calendar_for=calendar_for)

    assert instants(in_vaud)[1] == "2027-09-09T16:30"
    assert instants(in_geneva)[1] == "2027-09-10T12:00"
    assert in_geneva.interpretation.endswith("mer 08.09.2027 06:30 → ven 10.09.2027 12:00 · 2 j")


def test_a_day_duration_token_gives_its_days() -> None:
    entry = parse("lun 1j½ LBI 2h")

    assert [(token.kind, token.value) for token in entry.tokens] == [
        ("day", "2026-10-26"),
        ("duration", "1.5j"),
        ("person", "lbi"),
        ("duration", "120"),
    ]
    assert instants(entry) == ("2026-10-26T06:30", "2026-10-27T15:00")


@pytest.mark.parametrize(
    "text",
    [
        "pose 1 j ½ LBI",
        "pose 1j ½ LBI",
        "pose 1 ½ j LBI",
        "pose 1 1/2j LBI",
        "pose 1 j 1/2 LBI",
        "pose 1 jour et demi LBI",
        "pose 1 journée et demie LBI",
    ],
)
def test_a_spaced_half_day_belongs_to_the_duration(text: str) -> None:
    entry = parse(text)

    assert (entry.title, entry.estimate_days, entry.estimate_minutes) == ("pose", 1.5, 810)
    assert [token.kind for token in entry.tokens] == ["duration", "person"]
    assert entry.tokens[0].value == "1.5j"
    assert entry.warnings == ()


def test_ignored_duration_marks_its_token() -> None:
    entry = parse("27-29.10 121 LBI 2j")
    duration = next(token for token in entry.tokens if token.kind == "duration")

    assert duration.message == "Durée ignorée : la plage tapée fixe la fin"
    assert entry.warnings == ("Durée ignorée : la plage tapée fixe la fin",)


# ------------------------------------------------------------------- type ----


@pytest.mark.parametrize(
    ("text", "task_type"),
    [
        ("/montage 118", "montage"),
        ("/atelier 118", "debit"),
        ("/releve 118", "releve"),
        ("/dessin 118", "dessin"),
        ("/debit 118", "debit"),
        ("/soudure 118", "soudure"),
        ("/finition 118", "finition"),
        ("/livraison 118", "livraison"),
        ("/sav 118", "sav"),
        ("/soustraitance 118", "soustraitance"),
        ("/interne", "interne"),
        ("/liv 118", "livraison"),
        ("/bureau 118", "releve"),
        ("/Dessin pose 121", "dessin"),
        ("galva 129", "soustraitance"),
        ("plans 121", "dessin"),
        ("meulage 124", "finition"),
        ("transport 112", "livraison"),
        ("livraisons 112", "livraison"),
        ("soudures 124", "soudure"),
        ("nettoyage atelier", "interne"),
        ("métré 118", "releve"),
        ("Relevé de cotes", "releve"),
        ("LBI jeu", "montage"),
        ("SBE lun", "releve"),
        ("KDU lun", "debit"),
        ("THL lun", "soustraitance"),
        ("121", "soudure"),
        ("130 KDU lun", "montage"),
        ("portail coulissant", "soudure"),
        ("pose portail KDU", "montage"),
    ],
)
def test_task_type(text: str, task_type: str) -> None:
    assert parse(text).task_type == task_type


# ------------------------------------------------------------------ title ----


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("2026-121 montage marquise jeu-ven LBI YCH", "montage marquise"),
        ("124 débit portail 1,5j KDU lun", "débit portail"),
        ("thermolaquage RAL 7016 THL lun-mer", "thermolaquage RAL 7016"),
        ("5231 pose garde-corps +montage S45 ?", "pose garde-corps"),
        ("sav portail 109 demain à 10h 1h30 !", "sav portail"),
        ("pose garde-corps, côté lac LBI jeu", "pose garde-corps, côté lac"),
        ("LBI, YCH montage", "montage"),
        ("121 LBI jeu", "Marquise entrée école"),
        ("LBI jeu", "Montage chantier"),
        ("  pose   portail  ", "pose portail"),
        ("PORTAIL coulissant 124", "PORTAIL coulissant"),
        ("#118 portail", "#118 portail"),
        ("relevé de cotes 118", "relevé de cotes"),
        ("relevé DE cotes 118", "relevé cotes"),
        ('"appeler avant" pose 121', "pose"),
        ("portail! 124", "portail"),
        ("/vac LBI lun", ""),
        ("débit HEA 200 KDU", "débit HEA 200"),
        ("Ø 150 tube 121", "Ø 150 tube"),
        ("121 et 124 portails", "et 124 portails"),
        ("pose LBI - YCH 121", "pose"),
        ("?", ""),
        ("S2 LBI soudure", "soudure"),
        ("pose le portail LE", "pose le portail"),
        ("pose : portail ; LBI", "pose : portail"),
        ("LBI, YCH, KDU pose", "pose"),
        ("pose LBI, portail", "pose, portail"),
    ],
)
def test_title(text: str, title: str) -> None:
    assert parse(text).title == title


# ---------------------------------------------------------- flags, notes ----


@pytest.mark.parametrize(
    ("text", "provisional", "urgent"),
    [
        ("121 ?", True, False),
        ("121 !", False, True),
        ("121 ?!", True, True),
        ("portail! 124", False, True),
        ("121 urgent", False, True),
        ("121 provisoire", True, False),
        ("121 URGENT", False, True),
        ("121", False, False),
        ("S45? 121", True, False),
    ],
)
def test_flags(text: str, *, provisional: bool, urgent: bool) -> None:
    entry = parse(text)

    assert (entry.provisional, entry.urgent) == (provisional, urgent)


@pytest.mark.parametrize(
    ("text", "deadline"),
    [
        ("<06.11 129", date(2026, 11, 6)),
        ("< 06.11 129", date(2026, 11, 6)),
        ("avant 06.11 129", date(2026, 11, 6)),
        ("avant le 06.11 129", date(2026, 11, 6)),
        ("avant ven 129", date(2026, 10, 23)),
        ("<05.01 129", date(2027, 1, 5)),
        ("<20.10 129", date(2026, 10, 20)),
        ("<06.11.2027 129", date(2027, 11, 6)),
        ("129", None),
    ],
)
def test_deadline(text: str, deadline: date | None) -> None:
    assert parse(text).deadline == deadline


@pytest.mark.parametrize(
    ("text", "required"),
    [
        ("129 2p", 2),
        ("129 2 pers", 2),
        ("129 3 personnes", 3),
        ("129 2 pers.", 2),
        ("129 LBI YCH", 2),
        ("129", 1),
        ("129 +montage", 4),
        ("129 LBI 3p", 3),
    ],
)
def test_required_people(text: str, required: int) -> None:
    assert parse(text).required_people == required


@pytest.mark.parametrize(
    ("text", "notes"),
    [
        ('"appeler avant" 121', "appeler avant"),
        ("« attention vitrage » pose", "attention vitrage"),
        ('"a" "b" pose', "a\nb"),
        ('pose "non fermé, lun', "non fermé, lun"),
        ("pose 121", ""),
    ],
)
def test_notes(text: str, notes: str) -> None:
    assert parse(text).notes == notes


def test_quoted_note_is_not_parsed() -> None:
    entry = parse('pose "LBI lun 2j" 121')

    assert entry.resource_ids == ()
    assert entry.start is None
    assert entry.estimate_minutes == 0


# --------------------------------------------------------------- absences ----


@pytest.mark.parametrize(
    ("text", "reason", "people", "first", "last", "part"),
    [
        (
            "/vac LBI 26.10-30.10",
            "vacances",
            ("lbi",),
            date(2026, 10, 26),
            date(2026, 10, 30),
            "day",
        ),
        ("/absent LBI", "absent", ("lbi",), TODAY, TODAY, "day"),
        (
            "/ecole NRE ven matin",
            "ecole",
            ("nre",),
            date(2026, 10, 23),
            date(2026, 10, 23),
            "morning",
        ),
        ("/cie NRE S45", "cie", ("nre",), date(2026, 11, 2), date(2026, 11, 6), "day"),
        (
            "/militaire YCH 02.11-13.11",
            "militaire",
            ("ych",),
            date(2026, 11, 2),
            date(2026, 11, 13),
            "day",
        ),
        (
            "/formation SBE lun-mer",
            "formation",
            ("sbe",),
            date(2026, 10, 26),
            date(2026, 10, 28),
            "day",
        ),
        (
            "/recup LBI demain aprem",
            "recuperation",
            ("lbi",),
            date(2026, 10, 23),
            date(2026, 10, 23),
            "afternoon",
        ),
        ("/conge KDU lun", "conge", ("kdu",), date(2026, 10, 26), date(2026, 10, 26), "day"),
        (
            "/vac LBI YCH S44",
            "vacances",
            ("lbi", "ych"),
            date(2026, 10, 26),
            date(2026, 10, 30),
            "day",
        ),
        ("/absent LBI lun 3j", "absent", ("lbi",), date(2026, 10, 26), date(2026, 10, 28), "day"),
        ("/malade TFA", "absent", ("tfa",), TODAY, TODAY, "day"),
        ("/vacances LBI lun", "vacances", ("lbi",), date(2026, 10, 26), date(2026, 10, 26), "day"),
        ("/mil YCH lun", "militaire", ("ych",), date(2026, 10, 26), date(2026, 10, 26), "day"),
        ("/École NRE ven", "ecole", ("nre",), date(2026, 10, 23), date(2026, 10, 23), "day"),
    ],
)
def test_absence(
    text: str,
    reason: AbsenceReason,
    people: tuple[str, ...],
    *,
    first: date,
    last: date,
    part: DayPart,
) -> None:
    entry = parse(text)

    assert entry.destination == "absence"
    assert entry.absence == AbsenceDraft(people, reason, first, last, part)
    assert (entry.start, entry.end, entry.estimate_minutes) == (None, None, 0)


def test_absence_without_person_is_invalid_but_drafted() -> None:
    entry = parse("/vac 26.10")

    assert entry.destination == "invalid"
    assert entry.absence == AbsenceDraft((), "vacances", date(2026, 10, 26), date(2026, 10, 26))
    assert entry.warnings == ("Indiquer qui est absent",)


def test_absence_range_ignores_a_typed_duration() -> None:
    entry = parse("/vac LBI 26.10-30.10 3j")

    assert entry.ignored_duration is True
    assert entry.absence is not None
    assert entry.absence.last_day == date(2026, 10, 30)


# --------------------------------------------------------------- warnings ----


@pytest.mark.parametrize(
    ("text", "warning"),
    [
        ("ven aprem livraison 112 LBI", "Pas d'après-midi travaillé le vendredi : matin retenu"),
        ("25.12 LBI montage", "ven 25.12 n'est pas travaillé (Noël) : début le lun 28.12"),
        (
            "31.12 LBI",
            (
                "jeu 31.12 n'est pas travaillé (Restauration de la République) : "
                "début le lun 04.01.2027"
            ),
        ),
        ("sam LBI", "sam 24.10 n'est pas travaillé : début le lun 26.10"),
        ("jeu à 7h15 LBI", "à 7h15 arrondi à 07:30"),
        ("jeu 7h10-11h LBI", "7h10-11h arrondi à 07:00-11:00"),
        ("jeu 7h20 LBI", "7h20 arrondi à 07:30"),
        ("100min 121", "100min arrondi à 1 h 30"),
        ("2,25j 121", "2,25j arrondi à 2 j ½"),
        ("0,2j 121", "0,2j arrondi à ½ j"),
        ("lun 1,4j LBI", "1,4j arrondi à 1 j ½"),
        ("27-29.10 121 LBI 2j", "Durée ignorée : la plage tapée fixe la fin"),
        ("26.10 28.10 LBI", "Plusieurs dates : la première est retenue"),
        ("lun mar LBI", "Plusieurs jours : le premier est retenu"),
        ("ven 27.10 LBI", "« ven » ne correspond pas au mar 27.10 : la date est retenue"),
        ("jeu à 7h à 9h LBI", "Plusieurs heures de début : la première est retenue"),
        ("ven à 12h LBI", "Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30"),
        ("/vac 26.10", "Indiquer qui est absent"),
        (
            "/absent LBI lun 7h-11h",
            "Les heures ne comptent pas pour une absence : seuls les jours sont retenus",
        ),
        ("lun 11h-7h LBI", "La fin précède le début : 30 min retenues"),
        ("/recup LBI demain aprem", "Pas d'après-midi travaillé le vendredi"),
    ],
)
def test_warning(text: str, warning: str) -> None:
    assert warning in parse(text).warnings


@pytest.mark.parametrize(
    "text",
    [
        "2026-121 montage marquise jeu-ven LBI YCH",
        "124 débit portail 1,5j KDU lun",
        "jeu 22.10 LBI",
        "lun 26.10 LBI",
        "S45 lun LBI",
        "/vac LBI 26.10-30.10",
    ],
)
def test_consistent_entries_have_no_warning(text: str) -> None:
    assert parse(text).warnings == ()


# --------------------------------------------------------- interpretation ----


@pytest.mark.parametrize(
    ("text", "sentence"),
    [
        (
            "2026-121 montage marquise jeu-ven LBI YCH",
            (
                "2026-121 Commune de Marsens · Montage marquise · LBI, YCH · "
                "jeu 22.10 06:30 → ven 23.10 12:00 · 2 j"
            ),
        ),
        (
            "129 relevé de cotes 2p",
            "2026-129 PPE Les Cèdres · Relevé de cotes · À planifier · 2 pers.",
        ),
        (
            "<06.11 129 fabrication 2j 2p",
            (
                "2026-129 PPE Les Cèdres · Fabrication · À planifier · 2 j · 2 pers. · "
                "avant le ven 06.11"
            ),
        ),
        (
            "livraison 112 ven matin",
            (
                "2026-112 M. Perret · Livraison · Non affecté · "
                "ven 23.10 06:30 → ven 23.10 12:00 · 1 j"
            ),
        ),
        (
            "sav portail 109 demain à 10h 1h30 !",
            (
                "2026-109 Mme Rossier · Sav portail · Non affecté · "
                "ven 23.10 10:00 → ven 23.10 11:30 · 1 h 30 · urgent"
            ),
        ),
        (
            "5231 pose garde-corps +montage S45 ?",
            (
                "2026-5231 Implenia SA · Pose garde-corps · LBI, YCH, JMF, MDU · "
                "lun 02.11 06:30 → ven 06.11 12:00 · 5 j · provisoire"
            ),
        ),
        ("/vac LBI 26.10-30.10", "Vacances · LBI · lun 26.10 → ven 30.10"),
        ("/ecole NRE ven matin", "École professionnelle · NRE · ven 23.10 matin"),
        ("/vac 26.10", "Vacances · qui ? · lun 26.10"),
        (
            "S2 LBI",
            "Montage chantier · LBI · lun 11.01.2027 06:30 → ven 15.01.2027 12:00 · 5 j",
        ),
        ("", "Saisie vide"),
        ("?", "Saisie incomplète"),
        ("255 portail LBI", "2026-255 · Portail · LBI · À planifier"),
        (
            "LBI pose 121 3p jeu",
            (
                "2026-121 Commune de Marsens · Pose · LBI · "
                "jeu 22.10 06:30 → jeu 22.10 16:30 · 1 j · 3 pers."
            ),
        ),
        (
            "jeu 2j 121 LBI",
            (
                "2026-121 Commune de Marsens · Marquise entrée école · LBI · "
                "jeu 22.10 06:30 → ven 23.10 12:00 · 2 j"
            ),
        ),
        (
            "lun 5j 121 LBI",
            (
                "2026-121 Commune de Marsens · Marquise entrée école · LBI · "
                "lun 26.10 06:30 → ven 30.10 12:00 · 5 j"
            ),
        ),
        (
            "lun aprem ½j LBI",
            "Montage chantier · LBI · lun 26.10 13:00 → lun 26.10 16:30 · ½ j",
        ),
        # The length its bar will show: not whole half days, so in hours.
        (
            "lun à 10h 1j LBI",
            "Montage chantier · LBI · lun 26.10 10:00 → lun 26.10 16:30 · 5 h 30",
        ),
        (
            "lun à 10h 5h30 LBI",
            "Montage chantier · LBI · lun 26.10 10:00 → lun 26.10 16:30 · 5 h 30",
        ),
        # Both end on Friday at noon, a whole day: the confirmation says what the bar shows.
        ("jeu 1j½ LBI", "Montage chantier · LBI · jeu 22.10 06:30 → ven 23.10 12:00 · 2 j"),
        ("ven ½j LBI", "Montage chantier · LBI · ven 23.10 06:30 → ven 23.10 12:00 · 1 j"),
        # An afternoon start is half of its day: 2 j from Thursday 13:00 end Monday noon.
        ("jeu apm 2j LBI", "Montage chantier · LBI · jeu 22.10 13:00 → lun 26.10 12:00 · 2 j"),
        ("lun apm 2j LBI", "Montage chantier · LBI · lun 26.10 13:00 → mer 28.10 12:00 · 2 j"),
        ("ven matin LBI", "Montage chantier · LBI · ven 23.10 06:30 → ven 23.10 12:00 · 1 j"),
        ("pose 1 j ½ LBI", "Pose · LBI · À planifier · 1 j ½"),
        ("lun 2 j ½ LBI", "Montage chantier · LBI · lun 26.10 06:30 → mer 28.10 12:00 · 2 j ½"),
        (
            "lun 7h30-11h LBI",
            "Montage chantier · LBI · lun 26.10 07:30 → lun 26.10 11:00 · 3 h 30",
        ),
        ("124 portail 1j½", "2026-124 Gérance Rhône SA · Portail · À planifier · 1 j ½"),
        ("124 portail 1j 4h", "2026-124 Gérance Rhône SA · Portail · À planifier · 1 j 4 h"),
        ("124 portail 4h30", "2026-124 Gérance Rhône SA · Portail · À planifier · 4 h 30"),
    ],
)
def test_interpretation(text: str, sentence: str) -> None:
    assert parse(text).interpretation == sentence


# ----------------------------------------------------------------- tokens ----


def test_tokens_carry_offsets_in_the_typed_text() -> None:
    entry = parse("2026-121 montage marquise jeu-ven LBI YCH")

    assert [(token.kind, token.text, token.start, token.end) for token in entry.tokens] == [
        ("project", "2026-121", 0, 8),
        ("day", "jeu-ven", 26, 33),
        ("person", "LBI", 34, 37),
        ("person", "YCH", 38, 41),
    ]
    assert [token.value for token in entry.tokens] == [
        "2026-121",
        "2026-10-22/2026-10-23",
        "lbi",
        "ych",
    ]


def test_token_offsets_follow_accents_and_combining_marks() -> None:
    composed = parse("Sébastien lun")
    decomposed = parse(f"Se{COMBINING_ACUTE}bastien lun")

    assert (composed.tokens[0].text, composed.tokens[0].end) == ("Sébastien", 9)
    assert decomposed.tokens[0].text == f"Se{COMBINING_ACUTE}bastien"
    assert decomposed.tokens[1].start == 11
    assert decomposed.resource_ids == ("sbe",)


def test_typographic_apostrophe_is_understood() -> None:
    entry = parse(f"aujourd{RIGHT_QUOTE}hui LBI")

    assert entry.tokens[0].text == f"aujourd{RIGHT_QUOTE}hui"
    assert instants(entry) == ("2026-10-22T06:30", "2026-10-22T16:30")


def test_flag_attached_to_a_word_is_its_own_token() -> None:
    entry = parse("S45? 121")

    assert [(token.kind, token.text) for token in entry.tokens] == [
        ("week", "S45"),
        ("provisional", "?"),
        ("project", "121"),
    ]


def test_bare_hour_token_becomes_time_or_duration() -> None:
    timed = parse("lun 7h30 LBI")
    counted = parse("pose 7h30")

    assert [(token.kind, token.value) for token in timed.tokens][1] == ("time", "07:30")
    assert [(token.kind, token.value) for token in counted.tokens] == [("duration", "450")]


@pytest.mark.parametrize(
    "text",
    [
        "»",
        "+",
        "@",
        "/",
        "<",
        "---",
        "...",
        "1/2",
        "00:00-24:00",
        "S53",
        "29.02",
        "99.99",
        "lun-",
        "«",
        '"',
        "½",
        "à",
        "+ +",
        "LBI+",
        "x 200",
        "avant",
        "du",
        "de 7h",
        "2026-",
        "-118",
        "S44-",
        "1,5",
        "ßtraße 12",
        "lun 3000j LBI",
        "/vac LBI 9999j",
        "31.12.9999 LBI",
        "31.12.9999 - 25.12",
        "<31.12.9999 121",
        "S52-S53",
        "200j 200j",
        "23h59 lun",
        'tube 2"',
    ],
)
def test_odd_input_never_raises(text: str) -> None:
    entry = parse(text)

    assert isinstance(entry, ParsedEntry)
    assert entry.destination in {"grid", "unassigned", "backlog", "absence", "invalid"}


# ---------------------------------------------------------------- helpers ----


@pytest.mark.parametrize(
    ("minutes", "text"),
    [
        (870, "14 h 30"),
        (540, "9 h"),
        (30, "30 min"),
        (90, "1 h 30"),
        (240, "4 h"),
        (570, "9 h 30"),
        (2490, "41 h 30"),
        (0, "0 min"),
        (-30, "0 min"),
    ],
)
def test_format_duration(minutes: int, text: str) -> None:
    assert format_duration(minutes) == text


@pytest.mark.parametrize(
    ("days", "text"),
    [(2, "2 j"), (1, "1 j"), (1.5, "1 j ½"), (0.5, "½ j"), (12.5, "12 j ½"), (0, "0 j")],
)
def test_format_days(days: float, text: str) -> None:
    assert format_days(days) == text


@pytest.mark.parametrize(
    ("start", "end", "text"),
    [
        ("2026-10-26T06:30", "2026-10-30T12:00", "5 j"),
        ("2026-10-22T06:30", "2026-10-23T12:00", "2 j"),
        ("2026-10-23T06:30", "2026-10-23T12:00", "1 j"),
        ("2026-10-26T06:30", "2026-10-26T12:00", "½ j"),
        ("2026-10-26T13:00", "2026-10-27T16:30", "1 j ½"),
        ("2026-10-26T10:00", "2026-10-26T16:30", "5 h 30"),
        ("2026-10-26T06:30", "2026-10-26T07:00", "30 min"),
        ("2026-12-24T06:30", "2026-12-28T16:30", "2 j"),
    ],
)
def test_format_span(start: str, end: str, text: str) -> None:
    assert format_span(CONTEXT.calendar, at(start), at(end)) == text


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Après-midi", "apres-midi"),
        (f"AUJOURD{RIGHT_QUOTE}HUI", "aujourd'hui"),
        ("Ø 150", "ø 150"),
        ("1j½", "1j½"),
        ("Gérance Rhône", "gerance rhone"),
    ],
)
def test_fold_text(text: str, folded: str) -> None:
    assert fold_text(text) == folded


# ------------------------------------------------------ review regressions ----

LUNCH_CELL = {
    "cell_start": at("2026-10-26T12:00"),
    "cell_end": at("2026-10-26T12:30"),
    "cell_resource_ids": ("kdu",),
}
CHRISTMAS_CELL = {
    "cell_start": at("2026-12-25T06:30"),
    "cell_end": at("2026-12-25T16:30"),
    "cell_resource_ids": ("kdu",),
}


@pytest.mark.parametrize(
    ("text", "title", "start", "estimate"),
    [
        ("124 débit 1.5 jours KDU lun", "débit", "2026-10-26T06:30", 870),
        ("124 débit 1.5 j KDU", "débit", None, 810),
        ("soudure 2.5 h lun", "soudure", "2026-10-26T06:30", 150),
        ("0.5 j 121", "Marquise entrée école", None, 270),
        ("pliage tôle 1.5 mm 124 KDU lun", "pliage tôle 1.5 mm", "2026-10-26T06:30", 540),
        ("débit barres 6.5 m 121 KDU", "débit barres 6.5 m", None, 0),
        ("main courante tube Ø 26.9 121 LBI", "main courante tube Ø 26.9", None, 0),
    ],
)
def test_decimal_with_a_dot_is_a_measure_not_a_date(
    text: str, title: str, start: str | None, estimate: int
) -> None:
    entry = parse(text)

    assert entry.title == title
    assert instants(entry)[0] == start
    assert entry.estimate_minutes == estimate
    assert entry.warnings == ()
    assert all(token.kind != "date" for token in entry.tokens)


def test_calendar_without_working_day_gives_an_invalid_entry() -> None:
    closed = WorkCalendar(closures=(TODAY + timedelta(days=offset) for offset in range(4000)))

    entry = parse("demain LBI", calendar=closed)

    assert entry.destination == "invalid"
    assert entry.warnings == (UNREADABLE_WARNING,)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("29-02.11 LBI", "2026-10-29T06:30", "2026-11-02T16:30"),
        ("28-05.01 LBI", "2026-12-28T06:30", "2027-01-05T16:30"),
        ("28.12-05.01 LBI", "2026-12-28T06:30", "2027-01-05T16:30"),
        ("S53-S1 LBI", "2026-12-28T06:30", "2027-01-08T12:00"),
        ("27.10-03.10 LBI", None, None),
        ("S45-S44 LBI", None, None),
    ],
)
def test_range_typed_backwards_wraps_only_when_short(
    text: str, start: str | None, end: str | None
) -> None:
    assert instants(parse(text)) == (start, end)


def test_short_range_absence_takes_the_month_before() -> None:
    entry = parse("/vac LBI 29-02.11")

    assert entry.absence == AbsenceDraft(
        ("lbi",), "vacances", date(2026, 10, 29), date(2026, 11, 2)
    )


@pytest.mark.parametrize(
    ("text", "changes", "first", "last", "part", "warnings"),
    [
        ("/vac LBI lun 5j", {}, date(2026, 10, 26), date(2026, 10, 30), "day", ()),
        ("/vac LBI ven 2j", {}, date(2026, 10, 23), date(2026, 10, 26), "day", ()),
        (
            "/absent LBI 1j",
            {"today": date(2026, 10, 23)},
            date(2026, 10, 23),
            date(2026, 10, 23),
            "day",
            (),
        ),
        ("/vac LBI lun ½j", {}, date(2026, 10, 26), date(2026, 10, 26), "morning", ()),
        ("/vac LBI lun aprem ½j", {}, date(2026, 10, 26), date(2026, 10, 26), "afternoon", ()),
        (
            "/vac LBI lun 4h",
            {},
            date(2026, 10, 26),
            date(2026, 10, 26),
            "morning",
            ("Absence comptée en demi-journée",),
        ),
        (
            "/vac LBI lun 1,5j",
            {},
            date(2026, 10, 26),
            date(2026, 10, 27),
            "day",
            ("Absence comptée en jours entiers : 2 j",),
        ),
        (
            "/vac LBI sam 2j",
            {},
            date(2026, 10, 26),
            date(2026, 10, 27),
            "day",
            ("sam 24.10 n'est pas travaillé : début le lun 26.10",),
        ),
        ("/vac 5j", DAY_CELL, date(2026, 10, 26), date(2026, 10, 30), "day", ()),
    ],
)
def test_absence_counts_working_days(
    text: str,
    changes: dict[str, Any],
    *,
    first: date,
    last: date,
    part: DayPart,
    warnings: tuple[str, ...],
) -> None:
    entry = parse(text, **changes)

    assert entry.absence is not None
    assert (entry.absence.first_day, entry.absence.last_day, entry.absence.part) == (
        first,
        last,
        part,
    )
    assert entry.warnings == warnings


def test_inch_mark_is_not_a_note() -> None:
    entry = parse('débit tube 2" galva 121 KDU lun')

    assert entry.title == 'débit tube 2" galva'
    assert entry.notes == ""
    assert entry.project_number == "2026-121"
    assert entry.resource_ids == ("kdu",)
    assert instants(entry) == ("2026-10-26T06:30", "2026-10-26T16:30")


def test_unclosed_quote_warns() -> None:
    assert parse('pose "non fermé, lun').warnings == (
        "Guillemet non fermé : la suite est une note",
    )


@pytest.mark.parametrize(
    ("text", "title", "end"),
    [
        (
            "thermolaquage RAL 7016 mat THL lun-mer",
            "thermolaquage RAL 7016 mat",
            "2026-10-28T16:30",
        ),
        ("thermolaquage RAL 9010 mat THL lun", "thermolaquage RAL 9010 mat", "2026-10-26T16:30"),
        ("thermolaquage RAL 9010 THL lun mat", "thermolaquage RAL 9010", "2026-10-26T12:00"),
    ],
)
def test_mat_after_a_colour_is_a_finish(text: str, title: str, end: str) -> None:
    entry = parse(text)

    assert entry.title == title
    assert instants(entry)[1] == end


@pytest.mark.parametrize(
    ("text", "start", "end", "warnings"),
    [
        (
            "ven 14h-15h LBI",
            "2026-10-26T06:30",
            "2026-10-26T07:30",
            (
                "Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30",
                "14:00-15:00 hors des heures de travail : durée de 1 h gardée",
            ),
        ),
        (
            "lun 12:00-13:00 LBI",
            "2026-10-26T13:00",
            "2026-10-26T14:00",
            ("12:00-13:00 hors des heures de travail : durée de 1 h gardée",),
        ),
        (
            "ven 13h LBI",
            "2026-10-26T06:30",
            "2026-10-26T07:00",
            ("Le vendredi finit à 12:00 : début reporté au lun 26.10 06:30",),
        ),
        (
            "lun 17h LBI",
            "2026-10-26T06:30",
            "2026-10-27T15:30",
            ("« 17h » compris comme une durée de 17 h",),
        ),
        (
            "soudure 2-3h lun",
            "2026-10-26T06:30",
            "2026-10-26T09:30",
            ("« 2-3h » compris comme une durée de 3 h",),
        ),
        (
            "lun-mer matin LBI",
            "2026-10-26T06:30",
            "2026-10-28T12:00",
            ("Plage continue du lun 26.10 06:30 au mer 28.10 12:00",),
        ),
    ],
)
def test_hours_outside_working_time_are_explained(
    text: str, start: str, end: str, warnings: tuple[str, ...]
) -> None:
    entry = parse(text)

    assert instants(entry) == (start, end)
    assert entry.warnings == warnings


def test_hour_range_without_unit_is_an_estimate() -> None:
    entry = parse("soudure 2 à 3h")

    assert (entry.start, entry.estimate_minutes, entry.destination) == (None, 180, "backlog")
    assert [token.kind for token in entry.tokens] == ["duration"]


@pytest.mark.parametrize("text", ["MDU soudure", "+zzz soudure", "OTI soudure"])
def test_named_but_unknown_person_does_not_fall_back_to_the_cell_row(text: str) -> None:
    entry = parse(text, **DAY_CELL)

    assert entry.resource_ids == ()
    assert entry.destination == "unassigned"


@pytest.mark.parametrize(
    ("cell", "start", "warning"),
    [
        (LUNCH_CELL, "2026-10-26T13:00", "Début reporté au lun 26.10 13:00"),
        (CHRISTMAS_CELL, "2026-12-28T06:30", "Début reporté au lun 28.12 06:30 (Noël)"),
    ],
)
def test_moved_cell_start_warns(cell: dict[str, Any], start: str, warning: str) -> None:
    entry = parse("124 débit", **cell)

    assert instants(entry)[0] == start
    assert entry.warnings == (warning,)


@pytest.mark.parametrize(
    ("text", "note", "sentence"),
    [
        ("/vac LBI lun ski", "ski", "Vacances · LBI · lun 26.10 · ski"),
        ('/vac LBI lun "chalet"', "chalet", "Vacances · LBI · lun 26.10 · chalet"),
        ("/vac LBI lun", "", "Vacances · LBI · lun 26.10"),
    ],
)
def test_absence_keeps_its_note(text: str, note: str, sentence: str) -> None:
    entry = parse(text)

    assert entry.absence is not None
    assert entry.absence.note == note
    assert entry.interpretation == sentence


@pytest.mark.parametrize(
    ("text", "warning"),
    [
        ("/vac LBI sam", "sam 24.10 n'est pas travaillé"),
        ("/vac LBI 25.12", "ven 25.12 n'est pas travaillé (Noël)"),
        ("/vac LBI sam-dim", "Aucun jour travaillé du sam 24.10 au dim 25.10"),
        ("/vac LBI 20.10", "Date passée : mar 20.10"),
        ("20.10 LBI", "Date passée : mar 20.10"),
        ("S40 LBI", "Date passée : lun 28.09"),
    ],
)
def test_odd_days_warn(text: str, warning: str) -> None:
    assert warning in parse(text).warnings


@pytest.mark.parametrize(
    ("text", "start"),
    [
        ("01.05 LBI", "2027-05-03T06:30"),
        ("15.09 LBI", "2027-09-15T06:30"),
        ("23.09 LBI", "2026-09-23T06:30"),
        ("S30 LBI", "2027-07-26T06:30"),
        ("S40 LBI", "2026-09-28T06:30"),
    ],
)
def test_dates_long_past_mean_next_year(text: str, start: str) -> None:
    assert instants(parse(text))[0] == start


def test_calendar_of_the_first_person_sets_the_holidays() -> None:
    geneva, vaud = WorkCalendar(cantons=("GE",)), WorkCalendar(cantons=("VD",))

    def calendar_for(resource_id: str | None) -> WorkCalendar:
        return vaud if resource_id == "lbi" else geneva

    in_vaud = parse("31.12 LBI KDU", calendar_for=calendar_for)
    in_geneva = parse("31.12 KDU LBI", calendar_for=calendar_for)

    assert instants(in_vaud)[0] == "2026-12-31T06:30"
    assert instants(in_geneva)[0] == "2027-01-04T06:30"


FUZZ_FRAGMENTS = (
    "lun",
    "ven",
    "sam",
    "demain",
    "jeu-mar",
    "27.10",
    "25.12",
    "29-02.11",
    "27.10-03.10",
    "S44",
    "S45-S44",
    "matin",
    "aprem",
    "mat",
    "7h",
    "16h30",
    "17h",
    "7h30-11h",
    "14h-15h",
    "12:00-13:00",
    "à 10h",
    "2 à 3h",
    "3j",
    "½j",
    "1.5 j",
    "4h",
    "3000j",
    "0h",
    "2p",
    "/vac",
    "/sou",
    "?",
    "<06.11",
    '"note',
    'tube 2"',
    "LBI",
    "MDU",
    "OTI",
    "+montage",
    "121",
    "RAL 7016",
    "1.5 mm",
    "pose",
    "31.12.9999",
)
FUZZ_CELLS: tuple[dict[str, Any], ...] = ({}, DAY_CELL, RANGE_CELL, HALF_HOUR_CELL, LUNCH_CELL)


def _fuzz_texts() -> list[str]:
    count = len(FUZZ_FRAGMENTS)
    pairs = [f"{first} {second}" for first in FUZZ_FRAGMENTS for second in FUZZ_FRAGMENTS]
    triples = [
        " ".join(FUZZ_FRAGMENTS[(index * step) % count] for step in (1, 7, 13))
        for index in range(count * 10)
    ]
    return pairs + triples


def test_fuzzed_entries_are_consistent() -> None:
    calendar = CONTEXT.calendar
    problems: list[str] = []
    for index, text in enumerate(_fuzz_texts()):
        cell = FUZZ_CELLS[index % len(FUZZ_CELLS)]
        entry = parse(text, **cell)
        checks = {
            "unreadable": UNREADABLE_WARNING not in entry.warnings,
            "grid without people": entry.destination != "grid" or bool(entry.resource_ids),
        }
        if entry.start is not None and entry.end is not None:
            checks |= {
                "end before start": entry.end > entry.start,
                "start not worked": calendar.is_worked(entry.start),
                "end not worked": calendar.is_worked(entry.end - 1),
                "off the half hour": not (minute_of_day(entry.start) % 30 or entry.end % 30),
                "estimate": entry.estimate_minutes > 0 and entry.estimate_minutes % 30 == 0,
            }
        if entry.absence is not None:
            checks["absence days"] = entry.absence.first_day <= entry.absence.last_day
        problems.extend(f"{name}: {text!r} {cell}" for name, ok in checks.items() if not ok)

    assert problems == []


def test_a_hand_written_suffix_finds_its_dossier() -> None:
    projects = {
        "2024-4493-A1": ProjectInfo("2024-4493-A1", "AIG", "", "Habillage lot A1", ""),
        "2026-5102-REV03": ProjectInfo("2026-5102-REV03", "Coop", "", "Auvent rév. 3", ""),
    }
    found = parse("2024-4493-a1 pose", find_project=projects.get)
    assert found.project_number == "2024-4493-A1"
    assert found.project is not None
    revised = parse("5102-rev03 pose", find_project=projects.get)
    assert revised.project_number == "2026-5102-REV03"
    assert parse("118-2 pose").project_number == "2026-118-2"
