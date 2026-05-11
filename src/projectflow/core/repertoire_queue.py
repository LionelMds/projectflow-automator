from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.exceptions import ProjectCreationError


@dataclass(frozen=True, slots=True)
class PendingRepertoireTransaction:
    id: str
    created_at: str
    project: ProjectInput
    force_overwrite: bool
    repertoire_date: date
    path: Path

    def age_seconds(self, *, now: datetime | None = None) -> float:
        reference = now or datetime.now(tz=UTC)
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError:
            return float("inf")
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return max(0.0, (reference - created).total_seconds())


class RepertoireTransactionStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @classmethod
    def for_workbook(cls, workbook_path: Path) -> RepertoireTransactionStore:
        return cls(workbook_path.parent / "ProjectFlow pending repertoire")

    def create(
        self,
        project: ProjectInput,
        *,
        force_overwrite: bool,
        repertoire_date: date,
    ) -> PendingRepertoireTransaction:
        self._directory.mkdir(parents=True, exist_ok=True)
        transaction_id = f"{datetime.now(tz=UTC):%Y%m%d%H%M%S}-{project.number}-{uuid4().hex}"
        path = self._directory / f"{_safe_filename(transaction_id)}.json"
        transaction = PendingRepertoireTransaction(
            id=transaction_id,
            created_at=datetime.now(tz=UTC).isoformat(),
            project=project,
            force_overwrite=force_overwrite,
            repertoire_date=repertoire_date,
            path=path,
        )
        _write_json_atomic(path, _transaction_to_json(transaction))
        return transaction

    def list_pending(self) -> list[PendingRepertoireTransaction]:
        if not self._directory.exists():
            return []
        return [_transaction_from_json(path) for path in sorted(self._directory.glob("*.json"))]

    def delete(self, transaction: PendingRepertoireTransaction) -> None:
        transaction.path.unlink(missing_ok=True)

    @contextmanager
    def lock(self, *, timeout_seconds: float = 15.0) -> Iterator[None]:
        self._directory.mkdir(parents=True, exist_ok=True)
        lock_path = self._directory / ".repertoire.lock"
        deadline = time.monotonic() + timeout_seconds
        handle: int | None = None
        try:
            while handle is None:
                try:
                    handle = os.open(
                        lock_path,
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    os.write(handle, str(os.getpid()).encode("ascii"))
                except FileExistsError as exc:
                    if _is_stale_lock(lock_path):
                        lock_path.unlink(missing_ok=True)
                        continue
                    if time.monotonic() >= deadline:
                        raise ProjectCreationError(
                            "Le repertoire chantier est deja en cours de synchronisation. "
                            "Reessayez dans quelques secondes.",
                        ) from exc
                    time.sleep(0.2)
            yield
        finally:
            if handle is not None:
                os.close(handle)
                lock_path.unlink(missing_ok=True)


def _transaction_to_json(transaction: PendingRepertoireTransaction) -> dict[str, Any]:
    project = transaction.project
    return {
        "version": 1,
        "id": transaction.id,
        "created_at": transaction.created_at,
        "force_overwrite": transaction.force_overwrite,
        "repertoire_date": transaction.repertoire_date.isoformat(),
        "project": {
            "number": str(project.number),
            "designation": project.designation,
            "societe": project.societe,
            "contact": project.contact,
            "localisation": project.localisation,
            "gere_par": project.gere_par,
        },
    }


def _transaction_from_json(path: Path) -> PendingRepertoireTransaction:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        project_payload = payload["project"]
        project = ProjectInput(
            number=parse_project_number(str(project_payload["number"])),
            designation=str(project_payload.get("designation", "")),
            societe=str(project_payload.get("societe", "")),
            contact=str(project_payload.get("contact", "")),
            localisation=str(project_payload.get("localisation", "")),
            gere_par=str(project_payload.get("gere_par", "")),
        )
        return PendingRepertoireTransaction(
            id=str(payload["id"]),
            created_at=str(payload["created_at"]),
            project=project,
            force_overwrite=bool(payload.get("force_overwrite", False)),
            repertoire_date=date.fromisoformat(str(payload["repertoire_date"])),
            path=path,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectCreationError(f"Transaction repertoire invalide: {path}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)


def _is_stale_lock(path: Path, *, stale_after_seconds: float = 300.0) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    return age > stale_after_seconds
