from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from projectflow.graph.client import JSON, GraphClient


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
class PlannerTask:
    id: str
    title: str


class PlannerClient:
    def __init__(self, graph: GraphClient) -> None:
        self._graph = graph

    async def list_plans_for_group(self, group_id: str) -> list[PlannerPlan]:
        payload = await self._graph.get(f"/groups/{group_id}/planner/plans")
        value = payload.get("value", [])
        return [_plan(item) for item in value if isinstance(item, dict)]

    async def list_buckets(self, plan_id: str) -> list[PlannerBucket]:
        payload = await self._graph.get(f"/planner/plans/{plan_id}/buckets")
        value = payload.get("value", [])
        return [_bucket(item) for item in value if isinstance(item, dict)]

    async def create_task(
        self,
        *,
        plan_id: str,
        bucket_id: str,
        title: str,
        due_days: int,
    ) -> PlannerTask:
        due_at = datetime.now(tz=UTC) + timedelta(days=due_days)
        payload = await self._graph.post(
            "/planner/tasks",
            json={
                "planId": plan_id,
                "bucketId": bucket_id,
                "title": title,
                "dueDateTime": due_at.isoformat(),
            },
        )
        return _task(payload)


def _plan(payload: JSON) -> PlannerPlan:
    return PlannerPlan(id=str(payload.get("id", "")), title=str(payload.get("title", "")))


def _bucket(payload: JSON) -> PlannerBucket:
    return PlannerBucket(
        id=str(payload.get("id", "")),
        name=str(payload.get("name", "")),
        plan_id=str(payload.get("planId", "")),
    )


def _task(payload: JSON) -> PlannerTask:
    return PlannerTask(id=str(payload.get("id", "")), title=str(payload.get("title", "")))
