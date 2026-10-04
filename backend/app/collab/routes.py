"""HTTP API команд. Генерация схем по-прежнему не требует входа."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from .store import CollabError, CollabStore

router = APIRouter()
_store: CollabStore | None = None

RoleName = Literal["owner", "editor", "viewer"]


def init_store(path: Path) -> CollabStore:
    global _store
    _store = CollabStore(path)
    return _store


def get_store() -> CollabStore:
    global _store
    if _store is None:
        from ..config import ROOT

        _store = CollabStore(ROOT / "data" / "collab.db")
    return _store


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except CollabError as exc:
        raise HTTPException(exc.status, exc.message) from exc


def _user_from_header(authorization: str | None, *, required: bool) -> dict | None:
    if not authorization:
        if required:
            raise HTTPException(401, "Сначала представьтесь")
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Сначала представьтесь")
    user = get_store().user_by_token(token.strip())
    if user is None:
        raise HTTPException(401, "Сессия не найдена. Представьтесь ещё раз")
    return user


def current_user(authorization: str | None = Header(default=None)) -> dict:
    return _user_from_header(authorization, required=True)  # type: ignore[return-value]


def _person_name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Укажите имя")
    if len(value) > 80:
        raise ValueError("Имя слишком длинное")
    return value


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _person_name(value)


class JoinBody(BaseModel):
    name: str | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _person_name(value)


class TeamBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class InviteBody(BaseModel):
    role: RoleName = "editor"


class RoleBody(BaseModel):
    role: RoleName


class ProjectBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SaveBody(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(default="", max_length=30000)
    xml: str = Field(default="", max_length=2_000_000)
    code: str = Field(default="", max_length=200_000)
    plan: dict | None = None


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "name": user["name"], "token": user["token"]}


@router.post("/api/session")
def create_session(body: NameBody):
    return _public_user(_run(get_store().create_user, body.name))


@router.get("/api/me")
def me(authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return {"id": user["id"], "name": user["name"], "teams": get_store().list_teams(user["id"])}


@router.post("/api/teams")
def create_team(body: TeamBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(get_store().create_team, user["id"], body.name)


@router.get("/api/teams/{team_id}")
def get_team(team_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(get_store().team_detail, user["id"], team_id)


@router.post("/api/teams/{team_id}/invites")
def create_invite(team_id: str, body: InviteBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    invite = _run(get_store().create_invite, user["id"], team_id, body.role)
    invite["path"] = f"/join/{invite['token']}"
    return invite


@router.delete("/api/invites/{token}")
def revoke_invite(token: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    _run(get_store().revoke_invite, user["id"], token)
    return {"ok": True}


@router.post("/api/join/{token}")
def join_team(token: str, body: JoinBody | None = None, authorization: str | None = Header(default=None)):
    if body is None:
        body = JoinBody()
    user = _user_from_header(authorization, required=False)
    if user is None:
        if not body.name:
            raise HTTPException(400, "Укажите, как вас представить команде")
        user = _run(get_store().create_user, body.name)
    elif body.name:
        user = _run(get_store().rename_user, user["id"], body.name)
    joined = _run(get_store().join, token, user)
    joined["user"] = _public_user(user)
    return joined


@router.patch("/api/teams/{team_id}/members/{user_id}")
def set_member_role(
    team_id: str, user_id: str, body: RoleBody, authorization: str | None = Header(default=None)
):
    user = current_user(authorization)
    return _run(get_store().set_role, user["id"], team_id, user_id, body.role)


@router.delete("/api/teams/{team_id}/members/{user_id}")
def remove_member(team_id: str, user_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(get_store().remove_member, user["id"], team_id, user_id)


@router.post("/api/teams/{team_id}/projects")
def create_project(team_id: str, body: ProjectBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(get_store().create_project, user["id"], team_id, body.title)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(get_store().get_project, user["id"], project_id)


@router.put("/api/projects/{project_id}")
def save_project(project_id: str, body: SaveBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    return _run(
        get_store().save_project,
        user["id"],
        project_id,
        version=body.version,
        title=body.title,
        text=body.text,
        xml=body.xml,
        code=body.code,
        plan=body.plan,
    )


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    _run(get_store().delete_project, user["id"], project_id)
    return {"ok": True}
