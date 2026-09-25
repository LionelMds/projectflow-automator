"""Document Manager stand-in for the demo mode and the tests.

Demo "SolidWorks" files are small JSON files holding their custom properties and their
external references, so the whole CAD flow can be exercised without SolidWorks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from projectflow.exceptions import CadError, CadUnavailableError

DEMO_LICENSE_KEY = "demo-document-manager"


class JsonDocument:
    def __init__(self, path: Path, *, read_only: bool) -> None:
        self._path = path
        self._read_only = read_only
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CadError(f"{path.name}: fichier SolidWorks de demonstration illisible.") from exc
        self._properties = {str(key): str(value) for key, value in data["properties"].items()}
        self._references = [str(reference) for reference in data["references"]]

    def custom_properties(self) -> dict[str, str]:
        return dict(self._properties)

    def set_custom_property(self, name: str, value: str) -> None:
        self._properties[name] = value

    def external_references(self, search_paths: Sequence[Path] = ()) -> list[str]:
        del search_paths
        return list(self._references)

    def reference_report(self) -> str:
        return f"fichier de demonstration : {len(self._references)}"

    def replace_reference(self, old_path: str, new_path: str) -> None:
        self._references = [new_path if item == old_path else item for item in self._references]

    def save(self) -> None:
        if self._read_only:
            raise CadError(f"{self._path.name}: document ouvert en lecture seule.")
        write_demo_document(self._path, self._properties, self._references)


class JsonDocumentManager:
    @contextmanager
    def open_document(self, path: Path, *, read_only: bool = False) -> Iterator[JsonDocument]:
        yield JsonDocument(path, read_only=read_only)


@contextmanager
def open_json_document_manager(license_key: str) -> Iterator[JsonDocumentManager]:
    if license_key != DEMO_LICENSE_KEY:
        raise CadUnavailableError("cle de licence Document Manager refusee")
    yield JsonDocumentManager()


def write_demo_document(
    path: Path,
    properties: Mapping[str, str],
    references: Sequence[str] = (),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"properties": dict(properties), "references": list(references)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def read_demo_document(path: Path) -> tuple[dict[str, str], list[str]]:
    document = JsonDocument(path, read_only=True)
    return document.custom_properties(), document.external_references()
