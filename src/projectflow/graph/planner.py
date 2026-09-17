from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from projectflow.config import PlannerConfig
from projectflow.core.models import ProjectInput
from projectflow.exceptions import ConfigError
from projectflow.graph.client import GraphClient


@dataclass(frozen=True, slots=True)
class PlannerPlan:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class PlannerBucket:
    id: str
    name: str
    plan_id: str


@dataclass(frozen=True, slots=True)
class PlannerMember:
    id: str
    display_name: str
    email: str

    @property
    def label(self) -> str:
        if self.display_name and self.email:
            return f"{self.display_name} <{self.email}>"
        return self.display_name or self.email or self.id


@dataclass(frozen=True, slots=True)
class PlannerTask:
    id: str
    title: str
    bucket_id: str
    etag: str
    assignments: frozenset[str]
    due_date_time: str = ""


@dataclass(frozen=True, slots=True)
class PlannerTaskResult:
    task_id: str
    created: bool
    updated: bool


class GraphPlannerClient:
    def __init__(self, *, graph: GraphClient) -> None:
        self._graph = graph
        self._current_user_id: str = ""

    async def aclose(self) -> None:
        """Release the HTTP connection pool owned by this Planner client."""
        await self._graph.aclose()

    async def list_plans(self) -> list[PlannerPlan]:
        payloads = await self._collect_pages("/me/planner/plans")
        return [
            PlannerPlan(id=item_id, title=title)
            for payload in payloads
            if (item_id := _string(payload.get("id"))) and (title := _string(payload.get("title")))
        ]

    async def list_buckets(self, *, plan_id: str) -> list[PlannerBucket]:
        normalized_plan_id = plan_id.strip()
        if not normalized_plan_id:
            raise ConfigError("Plan Planner non configure.")
        payloads = await self._collect_pages(f"/planner/plans/{normalized_plan_id}/buckets")
        return [
            PlannerBucket(id=item_id, name=name, plan_id=task_plan_id)
            for payload in payloads
            if (item_id := _string(payload.get("id")))
            and (name := _string(payload.get("name")))
            and (task_plan_id := _string(payload.get("planId")))
        ]

    async def list_members(self, *, plan_id: str) -> list[PlannerMember]:
        normalized_plan_id = plan_id.strip()
        if not normalized_plan_id:
            raise ConfigError("Plan Planner non configure.")
        plan = await self._graph.get(
            f"/planner/plans/{normalized_plan_id}?$select=id,title,container,owner",
        )
        group_id = _planner_plan_group_id(plan)
        if not group_id:
            raise ConfigError("Impossible d'identifier le groupe Microsoft 365 du plan Planner.")
        payloads = await self._collect_pages(
            f"/groups/{group_id}/members/microsoft.graph.user"
            "?$select=id,displayName,mail,userPrincipalName",
        )
        members: list[PlannerMember] = []
        for payload in payloads:
            member_id = _string(payload.get("id"))
            if not member_id:
                continue
            display_name = _string(payload.get("displayName"))
            email = _string(payload.get("mail")) or _string(payload.get("userPrincipalName"))
            members.append(
                PlannerMember(
                    id=member_id,
                    display_name=display_name,
                    email=email,
                ),
            )
        if members and all(not member.display_name and not member.email for member in members):
            raise ConfigError(
                "Microsoft Graph ne renvoie que les identifiants des membres Planner. "
                "Ajoutez l'autorisation deleguee User.ReadBasic.All a ProjectFlow, "
                "accordez le consentement administrateur, puis reconnectez le compte Microsoft.",
            )
        return members

    async def current_user_id(self) -> str:
        if self._current_user_id:
            return self._current_user_id
        payload = await self._graph.get("/me?$select=id")
        user_id = _string(payload.get("id"))
        if not user_id:
            raise ConfigError("Impossible d'identifier l'utilisateur Microsoft connecte.")
        self._current_user_id = user_id
        return user_id

    async def ensure_project_task(
        self,
        project: ProjectInput,
        config: PlannerConfig,
    ) -> PlannerTaskResult:
        plan_id = config.target_plan_id
        bucket_id = project.planner.bucket_id.strip() or config.target_bucket_id
        if not plan_id or not bucket_id:
            raise ConfigError("Planner actif mais plan ou bucket non configure.")

        assignee_ids = tuple(user_id for user_id in project.planner.assignee_ids if user_id.strip())
        if not assignee_ids:
            assignee_ids = (await self.current_user_id(),)
        due_date = _due_date(project.planner.due_days)
        title = planner_task_title(project)
        existing = await self._find_project_task(project, plan_id=plan_id)
        if existing is None:
            task = await self._create_task(
                plan_id=plan_id,
                bucket_id=bucket_id,
                title=title,
                assignee_ids=assignee_ids,
                due_date=due_date,
            )
            return PlannerTaskResult(task_id=task.id, created=True, updated=False)

        patch: dict[str, Any] = {}
        if existing.title != title:
            patch["title"] = title
        if existing.bucket_id != bucket_id:
            patch["bucketId"] = bucket_id
        missing_assignees = [
            assignee_id for assignee_id in assignee_ids if assignee_id not in existing.assignments
        ]
        if missing_assignees:
            patch["assignments"] = _assignments_payload(missing_assignees)
        if due_date and existing.due_date_time != due_date:
            patch["dueDateTime"] = due_date

        if patch:
            await self._graph.patch(
                f"/planner/tasks/{existing.id}",
                json=patch,
                headers={"If-Match": existing.etag},
            )
            return PlannerTaskResult(task_id=existing.id, created=False, updated=True)
        return PlannerTaskResult(task_id=existing.id, created=False, updated=False)

    async def delete_project_tasks(
        self,
        project: ProjectInput,
        config: PlannerConfig,
    ) -> int:
        plan_id = config.target_plan_id
        if not plan_id:
            raise ConfigError("Planner actif mais plan non configure.")
        project_number = str(project.number)
        matches = [
            task
            for task in await self._list_tasks(plan_id=plan_id)
            if _task_matches_project(task.title, project_number)
        ]
        for task in matches:
            await self._graph.delete(
                f"/planner/tasks/{task.id}",
                headers={"If-Match": task.etag},
            )
        return len(matches)

    async def _find_project_task(
        self,
        project: ProjectInput,
        *,
        plan_id: str,
    ) -> PlannerTask | None:
        tasks = await self._list_tasks(plan_id=plan_id)
        project_number = str(project.number)
        for task in tasks:
            if _task_matches_project(task.title, project_number):
                return task
        return None

    async def _list_tasks(self, *, plan_id: str) -> list[PlannerTask]:
        payloads = await self._collect_pages(f"/planner/plans/{plan_id}/tasks")
        tasks: list[PlannerTask] = []
        for payload in payloads:
            task_id = _string(payload.get("id"))
            title = _string(payload.get("title"))
            bucket_id = _string(payload.get("bucketId"))
            etag = _string(payload.get("@odata.etag"))
            if not task_id or not title or not etag:
                continue
            tasks.append(
                PlannerTask(
                    id=task_id,
                    title=title,
                    bucket_id=bucket_id,
                    etag=etag,
                    assignments=frozenset(_assignment_ids(payload.get("assignments"))),
                    due_date_time=_string(payload.get("dueDateTime")),
                ),
            )
        return tasks

    async def _create_task(
        self,
        *,
        plan_id: str,
        bucket_id: str,
        title: str,
        assignee_ids: Sequence[str],
        due_date: str,
    ) -> PlannerTask:
        body: dict[str, Any] = {
            "planId": plan_id,
            "bucketId": bucket_id,
            "title": title,
            "assignments": _assignments_payload(assignee_ids),
        }
        if due_date:
            body["dueDateTime"] = due_date
        payload = await self._graph.post("/planner/tasks", json=body)
        task_id = _string(payload.get("id"))
        title = _string(payload.get("title")) or title
        etag = _string(payload.get("@odata.etag")) or "*"
        if not task_id:
            raise ConfigError("Planner n'a pas retourne l'identifiant de la tache creee.")
        return PlannerTask(
            id=task_id,
            title=title,
            bucket_id=_string(payload.get("bucketId")) or bucket_id,
            etag=etag,
            assignments=frozenset(_assignment_ids(payload.get("assignments"))),
            due_date_time=_string(payload.get("dueDateTime")),
        )

    async def _collect_pages(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_path = path
        while next_path:
            payload = await self._graph.get(next_path)
            raw_items = payload.get("value", [])
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, dict))
            next_link = payload.get("@odata.nextLink")
            next_path = next_link if isinstance(next_link, str) else ""
        return items


def planner_task_title(project: ProjectInput) -> str:
    designation = " ".join(project.designation.split())
    if not designation:
        return str(project.number)
    return f"{project.number} - {designation}"


def _task_matches_project(title: str, project_number: str) -> bool:
    normalized = title.strip()
    return normalized == project_number or normalized.startswith(f"{project_number} ")


def _assignments_payload(user_ids: Sequence[str]) -> dict[str, dict[str, str]]:
    return {
        user_id: {
            "@odata.type": "#microsoft.graph.plannerAssignment",
            "orderHint": " !",
        }
        for user_id in user_ids
    }


def _due_date(due_days: int | None) -> str:
    if due_days is None or due_days <= 0:
        return ""
    due = datetime.now(UTC) + timedelta(days=due_days)
    return due.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _assignment_ids(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    return [key for key, assignment in value.items() if isinstance(key, str) and assignment]


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _planner_plan_group_id(payload: dict[str, Any]) -> str:
    container = payload.get("container")
    if isinstance(container, dict):
        container_type = _string(container.get("type")).casefold()
        container_id = _string(container.get("containerId"))
        if container_id and (not container_type or container_type == "group"):
            return container_id
    return _string(payload.get("owner"))
