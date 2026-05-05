from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from projectflow.auth.msal_client import MsalAuthClient
from projectflow.config import RepertoireChantierConfig, UserConfig
from projectflow.exceptions import AuthError, GraphError
from projectflow.platform.paths import detect_onedrive_balz_root

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


@dataclass(frozen=True, slots=True)
class MicrosoftUserProfile:
    tenant_id: str
    user_id: str
    display_name: str
    email: str

    def to_config(self) -> UserConfig:
        return UserConfig(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            display_name=self.display_name,
            email=self.email,
        )


@dataclass(frozen=True, slots=True)
class PlannerPlanChoice:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class PlannerBucketChoice:
    id: str
    name: str
    plan_id: str


class MicrosoftOnboardingService:
    def __init__(
        self,
        auth_client: MsalAuthClient,
        *,
        graph_base_url: str = GRAPH_BASE_URL,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._auth_client = auth_client
        self._graph_base_url = graph_base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=30)

    def connect_user(self) -> MicrosoftUserProfile:
        self._auth_client.acquire_token_interactive()
        payload = self._get_json("/me", params={"$select": "id,displayName,userPrincipalName,mail"})
        return _profile_from_me(payload)

    def resolve_repertoire(self, display_path: str) -> RepertoireChantierConfig:
        relative_path = onedrive_relative_path(display_path)
        encoded_path = quote(relative_path, safe="/")
        payload = self._get_json(f"/me/drive/root:/{encoded_path}")
        item_id = payload.get("id")
        parent_reference = payload.get("parentReference")
        drive_id = parent_reference.get("driveId") if isinstance(parent_reference, dict) else None
        if not isinstance(item_id, str) or not isinstance(drive_id, str):
            raise GraphError("Impossible de resoudre le fichier repertoire chantier dans OneDrive.")
        return RepertoireChantierConfig(
            drive_id=drive_id,
            item_id=item_id,
            display_path=relative_path,
        )

    def list_plans(self) -> list[PlannerPlanChoice]:
        payload = self._get_json("/me/planner/plans")
        raw_plans = payload.get("value", [])
        if not isinstance(raw_plans, list):
            raise GraphError("Graph n'a pas retourne de liste Planner valide.")
        return [_planner_plan(item) for item in raw_plans if isinstance(item, dict)]

    def list_buckets(self, plan_id: str) -> list[PlannerBucketChoice]:
        if plan_id.strip() == "":
            return []
        payload = self._get_json(f"/planner/plans/{plan_id}/buckets")
        raw_buckets = payload.get("value", [])
        if not isinstance(raw_buckets, list):
            raise GraphError("Graph n'a pas retourne de buckets Planner valides.")
        return [_planner_bucket(item) for item in raw_buckets if isinstance(item, dict)]

    def close(self) -> None:
        self._client.close()

    def _get_json(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = self._auth_client.acquire_token_silent()
        if token is None:
            raise AuthError("Session Microsoft expiree. Reconnectez-vous.")

        response = self._client.get(
            f"{self._graph_base_url}/{path.lstrip('/')}",
            params=params,
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphError("Graph a retourne une reponse non JSON.") from exc
        if not response.is_success:
            raise GraphError(_graph_error_message(payload), status_code=response.status_code)
        if not isinstance(payload, dict):
            raise GraphError("Graph a retourne une reponse inattendue.")
        return payload


def onedrive_relative_path(display_path: str) -> str:
    raw_path = display_path.strip()
    if raw_path == "":
        raise AuthError("Chemin du repertoire chantier manquant.")

    path = Path(raw_path).expanduser()
    if path.is_absolute():
        onedrive_root = detect_onedrive_balz_root()
        if onedrive_root is not None:
            try:
                return path.relative_to(onedrive_root).as_posix()
            except ValueError:
                pass
        return path.name
    return raw_path.replace("\\", "/").strip("/")


def _profile_from_me(payload: dict[str, Any]) -> MicrosoftUserProfile:
    user_id = payload.get("id")
    display_name = payload.get("displayName")
    mail = payload.get("mail") or payload.get("userPrincipalName")
    if (
        not isinstance(user_id, str)
        or not isinstance(display_name, str)
        or not isinstance(mail, str)
    ):
        raise AuthError("Profil Microsoft incomplet.")
    return MicrosoftUserProfile(
        tenant_id="",
        user_id=user_id,
        display_name=display_name,
        email=mail,
    )


def _planner_plan(payload: dict[str, Any]) -> PlannerPlanChoice:
    plan_id = payload.get("id")
    title = payload.get("title")
    if not isinstance(plan_id, str) or not isinstance(title, str):
        raise GraphError("Plan Planner invalide dans la reponse Graph.")
    return PlannerPlanChoice(id=plan_id, title=title)


def _planner_bucket(payload: dict[str, Any]) -> PlannerBucketChoice:
    bucket_id = payload.get("id")
    name = payload.get("name")
    plan_id = payload.get("planId")
    if not isinstance(bucket_id, str) or not isinstance(name, str) or not isinstance(plan_id, str):
        raise GraphError("Bucket Planner invalide dans la reponse Graph.")
    return PlannerBucketChoice(id=bucket_id, name=name, plan_id=plan_id)


def _graph_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return "Microsoft Graph a retourne une erreur."
