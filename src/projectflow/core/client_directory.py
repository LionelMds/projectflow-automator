from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClientRecord:
    societe: str
    contact: str


@dataclass(frozen=True, slots=True)
class ClientDirectory:
    records: tuple[ClientRecord, ...] = ()

    @classmethod
    def from_repertoire_rows(cls, rows: Iterable[Sequence[object]]) -> ClientDirectory:
        companies: dict[str, str] = {}
        contacts: dict[str, str] = {}
        records: list[ClientRecord] = []
        seen_records: set[tuple[str, str]] = set()

        for row in rows:
            societe = _cell_text(row, 2)
            contact = _cell_text(row, 3)
            company_key = normalized_client_key(societe)
            contact_key = normalized_client_key(contact)
            if company_key:
                societe = companies.setdefault(company_key, societe)
            if contact_key:
                contact = contacts.setdefault(contact_key, contact)
            record_key = (company_key, contact_key)
            if record_key == ("", "") or record_key in seen_records:
                continue
            seen_records.add(record_key)
            records.append(ClientRecord(societe=societe, contact=contact))

        return cls(records=tuple(records))

    @property
    def companies(self) -> tuple[str, ...]:
        return _unique_sorted(record.societe for record in self.records if record.societe)

    def contacts_for_company(self, societe: str) -> tuple[str, ...]:
        company_key = normalized_client_key(societe)
        if company_key and any(
            normalized_client_key(record.societe) == company_key for record in self.records
        ):
            values = (
                record.contact
                for record in self.records
                if record.contact and normalized_client_key(record.societe) == company_key
            )
            return _unique_sorted(values)
        return _unique_sorted(record.contact for record in self.records if record.contact)

    def canonical_company(self, value: str) -> str | None:
        key = normalized_client_key(value)
        return next(
            (company for company in self.companies if normalized_client_key(company) == key),
            None,
        )

    def canonical_contact(self, value: str, *, societe: str = "") -> str | None:
        key = normalized_client_key(value)
        return next(
            (
                contact
                for contact in self.contacts_for_company(societe)
                if normalized_client_key(contact) == key
            ),
            None,
        )


def normalized_client_key(value: str) -> str:
    compact = " ".join(value.split())
    decomposed = unicodedata.normalize("NFKD", compact)
    return "".join(
        character for character in decomposed if not unicodedata.combining(character)
    ).casefold()


def _cell_text(row: Sequence[object], index: int) -> str:
    if index >= len(row) or row[index] is None:
        return ""
    return " ".join(str(row[index]).split())


def _unique_sorted(values: Iterable[str]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for value in values:
        key = normalized_client_key(value)
        if key:
            unique.setdefault(key, value)
    return tuple(sorted(unique.values(), key=normalized_client_key))
