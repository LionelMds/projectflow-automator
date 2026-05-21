from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from projectflow.config import PlannerConfig
from projectflow.core.models import ProjectInput
from projectflow.core.numero import parse_project_number
from projectflow.graph.client import GraphClient
from projectflow.graph.planner import GraphPlannerClient, planner_task_title


class FakeTokenProvider:
    async def access_token(self) -> str:
        return "token"


def _json(request: httpx.Request) -> dict[str, Any]:
    payload = json.loads(request.content.decode("utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_graph_planner_lists_plans_and_buckets() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me/planner/plans"):
            return httpx.Response(200, json={"value": [{"id": "plan-id", "title": "Projets"}]})
        if request.url.path.endswith("/planner/buckets"):
            return httpx.Response(
                200,
                json={"value": [{"id": "bucket-id", "name": "A faire", "planId": "plan-id"}]},
            )
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphPlannerClient(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )

        plans = await client.list_plans()
        buckets = await client.list_buckets(plan_id="plan-id")

    assert plans[0].title == "Projets"
    assert buckets[0].name == "A faire"
    assert "planId%20eq%20'plan-id'" in str(requests[1].url)


@pytest.mark.asyncio
async def test_graph_planner_creates_task_when_missing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "user-id"})
        if request.url.path.endswith("/planner/plans/plan-id/tasks"):
            return httpx.Response(200, json={"value": []})
        if request.url.path.endswith("/planner/tasks") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "task-id",
                    "title": "2026-4995 - Escalier",
                    "bucketId": "bucket-id",
                    "@odata.etag": "etag",
                    "assignments": {"user-id": {}},
                },
            )
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphPlannerClient(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        result = await client.ensure_project_task(
            ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
            PlannerConfig(enabled=True, plan_id="plan-id", bucket_id="bucket-id", due_days=7),
        )

    post_request = next(request for request in requests if request.method == "POST")
    body = _json(post_request)
    assert result.created is True
    assert result.task_id == "task-id"
    assert body["planId"] == "plan-id"
    assert body["bucketId"] == "bucket-id"
    assert body["title"] == "2026-4995 - Escalier"
    assert "user-id" in body["assignments"]
    assert body["dueDateTime"].endswith("Z")


@pytest.mark.asyncio
async def test_graph_planner_updates_existing_task_bucket_and_assignment() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "user-id"})
        if request.url.path.endswith("/planner/plans/plan-id/tasks"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "task-id",
                            "title": "2026-4995 - Ancien",
                            "bucketId": "old-bucket",
                            "@odata.etag": "etag",
                            "assignments": {},
                        },
                    ],
                },
            )
        if request.url.path.endswith("/planner/tasks/task-id") and request.method == "PATCH":
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "missing"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = GraphPlannerClient(
            graph=GraphClient(token_provider=FakeTokenProvider(), http_client=http_client),
        )
        result = await client.ensure_project_task(
            ProjectInput(number=parse_project_number("2026-4995"), designation="Escalier"),
            PlannerConfig(enabled=True, plan_id="plan-id", bucket_id="new-bucket", due_days=0),
        )

    patch_request = next(request for request in requests if request.method == "PATCH")
    body = _json(patch_request)
    assert result.created is False
    assert result.updated is True
    assert patch_request.headers["if-match"] == "etag"
    assert body["title"] == "2026-4995 - Escalier"
    assert body["bucketId"] == "new-bucket"
    assert "user-id" in body["assignments"]


def test_planner_task_title_uses_project_number_and_designation() -> None:
    assert (
        planner_task_title(
            ProjectInput(number=parse_project_number("2026-4995"), designation="  Escalier  "),
        )
        == "2026-4995 - Escalier"
    )
