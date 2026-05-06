from __future__ import annotations

from dataclasses import dataclass

from projectflow.core.numero import ProjectNumber


@dataclass(frozen=True, slots=True)
class ProjectInput:
    number: ProjectNumber
    designation: str = ""
    societe: str = ""
    contact: str = ""
    localisation: str = ""
    gere_par: str = ""

    @property
    def is_subproject(self) -> bool:
        return self.number.is_subproject


@dataclass(frozen=True, slots=True)
class ProjectCreationResult:
    project_dir_created: bool
    project_dir: str
    fiche_path: str | None
    outlook_folder_created: bool = False
