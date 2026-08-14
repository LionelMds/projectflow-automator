from __future__ import annotations

from projectflow.core.client_directory import ClientDirectory


def test_client_directory_deduplicates_names_case_and_accent_insensitively() -> None:
    directory = ClientDirectory.from_repertoire_rows(
        [
            ("2026-5000", "", "Métal SA", "Élodie Martin", "Projet"),
            ("2026-5001", "", "metal  sa", "elodie martin", "Projet"),
            ("2026-5002", "", "Autre Client", "Jean Dupont", "Projet"),
        ]
    )

    assert directory.companies == ("Autre Client", "Métal SA")
    assert directory.contacts_for_company("METAL SA") == ("Élodie Martin",)
    assert directory.canonical_company("metal sa") == "Métal SA"
    assert directory.canonical_contact("elodie martin", societe="Métal SA") == "Élodie Martin"


def test_client_directory_offers_all_contacts_until_company_is_known() -> None:
    directory = ClientDirectory.from_repertoire_rows(
        [
            ("2026-5000", "", "Client A", "Alice", "Projet"),
            ("2026-5001", "", "Client B", "Bruno", "Projet"),
        ]
    )

    assert directory.contacts_for_company("") == ("Alice", "Bruno")
    assert directory.contacts_for_company("Nouveau") == ("Alice", "Bruno")
    assert directory.contacts_for_company("Client A") == ("Alice",)
