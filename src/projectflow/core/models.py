from __future__ import annotations

from dataclasses import dataclass, field

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

    @property
    def is_subproject(self) -> bool:
        return self.number.is_subproject


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


@dataclass(frozen=True, slots=True)
class ProjectDeletionResult:
    numbers_released: tuple[str, ...]
    project_path_trashed: bool
    outlook_folders_deleted: int = 0
    planner_tasks_deleted: int = 0
