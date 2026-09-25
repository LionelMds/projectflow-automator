"""SolidWorks file access seen by ProjectFlow, and the custom property rules.

The COM implementation lives in :mod:`projectflow.cad.document_manager`; tests and the demo
mode replace it with fakes that satisfy the protocols below.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol

from projectflow.config import CadPropertyNames

TEMPLATE_MARKER = "20XX-XXXX"
TEMPLATE_MARKER_RE = re.compile(re.escape(TEMPLATE_MARKER), re.IGNORECASE)
SOLIDWORKS_SUFFIXES = frozenset({".sldprt", ".sldasm", ".slddrw"})
# Files that point to other SolidWorks documents and must never keep a template link.
SOLIDWORKS_LINKED_SUFFIXES = frozenset({".sldasm", ".slddrw"})
DESCRIPTION_FILE_SUFFIX = "-ENS-100"


class SolidWorksDocument(Protocol):
    def custom_properties(self) -> dict[str, str]:
        """Return file-level custom properties (not configuration-specific ones)."""

    def set_custom_property(self, name: str, value: str) -> None:
        """Create or update one file-level text custom property."""

    def external_references(self, search_paths: Sequence[Path] = ()) -> list[str]:
        """Return the paths of the documents referenced by this document.

        ``search_paths`` are folders where the referenced documents can be found (the template
        folder): some Document Manager versions only list the references they can find.
        """

    def reference_report(self) -> str:
        """Describe what each reference source returned during the last read (for the log)."""

    def replace_reference(self, old_path: str, new_path: str) -> None:
        """Point one external reference to another file."""

    def save(self) -> None:
        """Save pending changes."""


class SolidWorksDocumentManager(Protocol):
    def open_document(
        self,
        path: Path,
        *,
        read_only: bool = False,
    ) -> AbstractContextManager[SolidWorksDocument]:
        """Open a SolidWorks document and close it when the context exits."""


DocumentManagerFactory = Callable[[str], AbstractContextManager[SolidWorksDocumentManager]]


def replace_marker(value: str, replacement: str) -> str:
    return TEMPLATE_MARKER_RE.sub(replacement, value)


def contains_marker(value: str) -> bool:
    return TEMPLATE_MARKER_RE.search(value) is not None


def is_description_file(path: Path, number: str) -> bool:
    return path.stem.casefold() == f"{number}{DESCRIPTION_FILE_SUFFIX}".casefold()


def compute_property_updates(
    existing: Mapping[str, str],
    *,
    number: str,
    societe: str,
    initials: str,
    designation: str,
    write_description: bool,
    names: CadPropertyNames,
) -> dict[str, str]:
    """Return the file-level properties to write; other properties stay untouched."""
    updates: dict[str, str] = {}
    for name, value in existing.items():
        if contains_marker(value):
            updates[name] = replace_marker(value, number)

    def current(name: str) -> str:
        return updates.get(name, existing.get(name, "")).strip()

    def assign(name: str, value: str) -> None:
        if name and value.strip() and current(name) != value.strip():
            updates[name] = value.strip()

    if names.projet and not current(names.projet):
        assign(names.projet, number)
    assign(names.client, societe)
    assign(names.auteur, initials)
    if write_description:
        assign(names.description, designation)
    if names.revision and not current(names.revision):
        assign(names.revision, names.revision_defaut)
    return updates
