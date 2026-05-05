from __future__ import annotations

import httpx
import pytest

from projectflow.auth.msal_client import TokenResult
from projectflow.auth.onboarding_service import MicrosoftOnboardingService, onedrive_relative_path
from projectflow.exceptions import AuthError


class FakeAuthClient:
    def __init__(self, *, has_token: bool = True) -> None:
        self.interactive_called = False
        self.has_token = has_token

    def acquire_token_interactive(self) -> TokenResult:
        self.interactive_called = True
        return TokenResult(access_token="interactive")

    def acquire_token_silent(self) -> TokenResult | None:
        if not self.has_token:
            return None
        return TokenResult(access_token="silent")


def test_onedrive_relative_path_normalizes_relative_path() -> None:
    assert onedrive_relative_path(r"Entreprise\Rep.xlsx") == "Entreprise/Rep.xlsx"


def test_onedrive_relative_path_rejects_blank_path() -> None:
    with pytest.raises(AuthError):
        onedrive_relative_path(" ")


def test_connect_user_reads_graph_profile() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer silent"
        return httpx.Response(
            200,
            json={
                "id": "user-id",
                "displayName": "Lionel Mottiez",
                "mail": "lionel@balzmetal.ch",
            },
        )

    auth = FakeAuthClient()
    service = MicrosoftOnboardingService(
        auth,  # type: ignore[arg-type]
        graph_base_url="https://graph.example/v1.0",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    profile = service.connect_user()

    assert auth.interactive_called is True
    assert profile.to_config().email == "lionel@balzmetal.ch"


def test_resolve_repertoire_reads_drive_and_item_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://graph.example/v1.0/me/drive/root:/Entreprise/Rep.xlsx"
        return httpx.Response(
            200,
            json={
                "id": "item-id",
                "parentReference": {"driveId": "drive-id"},
            },
        )

    service = MicrosoftOnboardingService(
        FakeAuthClient(),  # type: ignore[arg-type]
        graph_base_url="https://graph.example/v1.0",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    repertoire = service.resolve_repertoire("Entreprise/Rep.xlsx")

    assert repertoire.drive_id == "drive-id"
    assert repertoire.item_id == "item-id"
    assert repertoire.display_path == "Entreprise/Rep.xlsx"


def test_list_plans_and_buckets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://graph.example/v1.0/me/planner/plans":
            return httpx.Response(200, json={"value": [{"id": "plan", "title": "Projets"}]})
        if str(request.url) == "https://graph.example/v1.0/planner/plans/plan/buckets":
            return httpx.Response(
                200,
                json={"value": [{"id": "bucket", "name": "Dossiers", "planId": "plan"}]},
            )
        return httpx.Response(404, json={"error": {"message": "not found"}})

    service = MicrosoftOnboardingService(
        FakeAuthClient(),  # type: ignore[arg-type]
        graph_base_url="https://graph.example/v1.0",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    plans = service.list_plans()
    buckets = service.list_buckets("plan")

    assert plans[0].title == "Projets"
    assert buckets[0].name == "Dossiers"
