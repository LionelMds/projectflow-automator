from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from projectflow.core.numero import ProjectNumber


@dataclass(frozen=True, slots=True)
class PlannerTaskInput:
    enabled: bool = False
    bucket_id: str = ""
    assignee_ids: tuple[str, ...] = ()
    due_days: int | None = None


@dataclass(frozen=True, slots=True)
class ProjectInput:
    number: ProjectNumber
    designation: str = ""
    societe: str = ""
    contact: str = ""
    localisation: str = ""
    gere_par: str = ""
    planner: PlannerTaskInput = field(default_factory=PlannerTaskInput)
    add_solidworks: bool = False
    add_autocad: bool = False

    @property
    def is_subproject(self) -> bool:
        return self.number.is_subproject


CadFileStatus = Literal["created", "skipped", "error"]


@dataclass(frozen=True, slots=True)
class CadFileResult:
    path: str
    status: CadFileStatus
    detail: str = ""

    @property
    def name(self) -> str:
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True)
class CadOutcome:
    files: tuple[CadFileResult, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProjectCreationResult:
    project_dir_created: bool
    project_dir: str
    fiche_path: str | None
    outlook_folder_created: bool = False
    planner_task_id: str | None = None
    planner_task_created: bool = False
    planner_task_updated: bool = False
    outlook_error: str | None = None
    planner_error: str | None = None
    cad_files: tuple[CadFileResult, ...] = ()
    cad_warnings: tuple[str, ...] = ()
    cad_error: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectDeletionResult:
    numbers_released: tuple[str, ...]
    project_path_trashed: bool
    outlook_folders_deleted: int = 0
    planner_tasks_deleted: int = 0
