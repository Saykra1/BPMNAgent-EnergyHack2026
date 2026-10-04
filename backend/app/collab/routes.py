"""HTTP API: registration and login, teams, configurable roles, invitations, shared projects.

Plus an access guard for the rest of the API (installed as middleware in main):
- with REQUIRE_LOGIN (default on) every /api call except login/registration needs a session;
- calls made inside a team project (header X-Project-Id) are checked against the member's roles:
  LLM endpoints need "generate", process-run actions need "run".
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import permissions as P
from .store import CollabError, CollabStore

router = APIRouter()
_store: CollabStore | None = None


def init_store(path: Path) -> CollabStore:
    global _store
    _store = CollabStore(path)
    return _store


def get_store() -> CollabStore:
    global _store
    if _store is None:
        from ..config import ROOT
        _store = CollabStore(ROOT / "data" / "app.db")
    return _store


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except CollabError as exc:
        raise HTTPException(exc.status, exc.message) from exc


def _token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def current_user(authorization: str | None) -> dict:
    token = _token(authorization)
    if not token:
        raise HTTPException(401, "Войдите в систему")
    user = get_store().user_by_token(token)
    if user is None:
        raise HTTPException(401, "Сессия истекла. Войдите снова")
    return user


# ----------------------------------------------------------------------------- access guard
OPEN_PATHS = {"/api/auth/login", "/api/auth/register", "/api/auth/config", "/api/health"}
GENERATE_PATHS = {"/api/generate", "/api/refine", "/api/prepare", "/api/estimate", "/api/recommend",
                  "/api/explain", "/api/jev-review", "/api/jev-audit"}
RUN_ACTIONS = ("/start", "/complete", "/choose", "/retry")


def require_login() -> bool:
    from .. import main
    return bool(getattr(main.get_settings(), "require_login", True))


def required_permission(path: str, method: str) -> str | None:
    if path in GENERATE_PATHS:
        return "generate"
    if method == "POST" and path.startswith("/api/run/") and path.endswith(RUN_ACTIONS):
        return "run"
    return None


async def guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in OPEN_PATHS:
        return await call_next(request)
    auth = request.headers.get("authorization")
    user = None
    if _token(auth):
        user = get_store().user_by_token(_token(auth))
    if user is None and require_login() and path not in OPEN_PATHS:
        return JSONResponse({"detail": "Войдите в систему"}, status_code=401)
    project_id = request.headers.get("x-project-id")
    perm = required_permission(path, request.method)
    if project_id and perm:
        if user is None:
            return JSONResponse({"detail": "Войдите в систему"}, status_code=401)
        try:
            perms = get_store().project_access(user["id"], project_id)
        except CollabError as e:
            return JSONResponse({"detail": e.message}, status_code=e.status)
        if perm not in perms:
            what = next(p["name"] for p in P.PERMISSIONS if p["id"] == perm)
            return JSONResponse({"detail": f"Ваши роли в этом проекте не дают права «{what}»"}, status_code=403)
    return await call_next(request)


# ----------------------------------------------------------------------------- auth
class RegisterBody(BaseModel):
    login: str = Field(max_length=64)
    email: str = Field(max_length=254)
    name: str = Field(max_length=120)
    password: str = Field(max_length=200)


class LoginBody(BaseModel):
    login: str = Field(max_length=254)
    password: str = Field(max_length=200)


class ProfileBody(BaseModel):
    name: str = Field(max_length=120)


class PasswordBody(BaseModel):
    old_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def _me(user: dict) -> dict:
    store = get_store()
    return {"user": store.user(user["id"]), "teams": store.list_teams(user["id"]), "invites": store.my_invites(user["id"])}


@router.get("/api/auth/config")
def auth_config():
    return {"require_login": require_login(), "permissions": P.PERMISSIONS}


@router.post("/api/auth/register")
def register(body: RegisterBody):
    user, token = _run(get_store().register, body.login, body.email, body.name, body.password)
    return {"token": token, **_me(user)}


@router.post("/api/auth/login")
def login(body: LoginBody):
    user, token = _run(get_store().login, body.login, body.password)
    return {"token": token, **_me(user)}


@router.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    if _token(authorization):
        get_store().logout(_token(authorization))
    return {"ok": True}


@router.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    return _me(current_user(authorization))


@router.patch("/api/auth/me")
def update_me(body: ProfileBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    _run(get_store().update_profile, user["id"], body.name)
    return _me(user)


@router.post("/api/auth/password")
def change_password(body: PasswordBody, authorization: str | None = Header(default=None)):
    user = current_user(authorization)
    _run(get_store().change_password, user["id"], body.old_password, body.new_password, _token(authorization))
    return {"ok": True}


# ----------------------------------------------------------------------------- teams
class NameBody(BaseModel):
    name: str = Field(max_length=120)


class RoleBody(BaseModel):
    name: str = Field(max_length=120)
    color: str = "#66746f"
    permissions: list[str] = Field(default_factory=list)
    scope: list[str] | None = None          # None = all projects


class MemberRolesBody(BaseModel):
    role_ids: list[str] = Field(default_factory=list, max_length=50)


class InviteBody(BaseModel):
    who: str = Field(max_length=254)          # e-mail, login or personal id
    role_ids: list[str] = Field(default_factory=list, max_length=50)


class LinkBody(BaseModel):
    role_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post("/api/teams")
def create_team(body: NameBody, authorization: str | None = Header(default=None)):
    return _run(get_store().create_team, current_user(authorization)["id"], body.name)


@router.get("/api/teams/{team_id}")
def get_team(team_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().team_detail, current_user(authorization)["id"], team_id)


@router.patch("/api/teams/{team_id}")
def rename_team(team_id: str, body: NameBody, authorization: str | None = Header(default=None)):
    return _run(get_store().rename_team, current_user(authorization)["id"], team_id, body.name)


@router.delete("/api/teams/{team_id}")
def delete_team(team_id: str, authorization: str | None = Header(default=None)):
    _run(get_store().delete_team, current_user(authorization)["id"], team_id)
    return {"ok": True}


@router.post("/api/teams/{team_id}/roles")
def create_role(team_id: str, body: RoleBody, authorization: str | None = Header(default=None)):
    return _run(get_store().create_role, current_user(authorization)["id"], team_id, body.name, body.color,
                body.permissions, body.scope)


@router.put("/api/roles/{role_id}")
def update_role(role_id: str, body: RoleBody, authorization: str | None = Header(default=None)):
    return _run(get_store().update_role, current_user(authorization)["id"], role_id, body.name, body.color,
                body.permissions, body.scope)


@router.delete("/api/roles/{role_id}")
def delete_role(role_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().delete_role, current_user(authorization)["id"], role_id)


@router.put("/api/teams/{team_id}/members/{user_id}/roles")
def set_member_roles(team_id: str, user_id: str, body: MemberRolesBody, authorization: str | None = Header(default=None)):
    return _run(get_store().set_member_roles, current_user(authorization)["id"], team_id, user_id, body.role_ids)


@router.delete("/api/teams/{team_id}/members/{user_id}")
def remove_member(team_id: str, user_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().remove_member, current_user(authorization)["id"], team_id, user_id)


# ----------------------------------------------------------------------------- invitations
@router.post("/api/teams/{team_id}/invites")
def invite(team_id: str, body: InviteBody, authorization: str | None = Header(default=None)):
    return _run(get_store().invite, current_user(authorization)["id"], team_id, body.who, body.role_ids)


@router.get("/api/invites")
def my_invites(authorization: str | None = Header(default=None)):
    return get_store().my_invites(current_user(authorization)["id"])


@router.post("/api/invites/{invite_id}/accept")
def accept(invite_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().respond, current_user(authorization)["id"], invite_id, True)


@router.post("/api/invites/{invite_id}/decline")
def decline(invite_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().respond, current_user(authorization)["id"], invite_id, False)


@router.delete("/api/invites/{invite_id}")
def cancel_invite(invite_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().cancel_invite, current_user(authorization)["id"], invite_id)


@router.post("/api/teams/{team_id}/links")
def create_link(team_id: str, body: LinkBody, authorization: str | None = Header(default=None)):
    return _run(get_store().create_link, current_user(authorization)["id"], team_id, body.role_ids)


@router.delete("/api/links/{token}")
def revoke_link(token: str, authorization: str | None = Header(default=None)):
    _run(get_store().revoke_link, current_user(authorization)["id"], token)
    return {"ok": True}


@router.get("/api/links/{token}")
def link_info(token: str, authorization: str | None = Header(default=None)):
    return _run(get_store().link_info, current_user(authorization)["id"], token)


@router.post("/api/links/{token}/accept")
def accept_link(token: str, authorization: str | None = Header(default=None)):
    return _run(get_store().accept_link, current_user(authorization)["id"], token)


# ----------------------------------------------------------------------------- projects
class ProjectBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SaveBody(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    text: str = Field(default="", max_length=200_000)
    xml: str = Field(default="", max_length=5_000_000)
    code: str = Field(default="", max_length=200_000)
    plan: dict | None = None


@router.post("/api/teams/{team_id}/projects")
def create_project(team_id: str, body: ProjectBody, authorization: str | None = Header(default=None)):
    return _run(get_store().create_project, current_user(authorization)["id"], team_id, body.title)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, authorization: str | None = Header(default=None)):
    return _run(get_store().get_project, current_user(authorization)["id"], project_id)


@router.put("/api/projects/{project_id}")
def save_project(project_id: str, body: SaveBody, authorization: str | None = Header(default=None)):
    return _run(get_store().save_project, current_user(authorization)["id"], project_id, version=body.version,
                title=body.title, text=body.text, xml=body.xml, code=body.code, plan=body.plan)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, authorization: str | None = Header(default=None)):
    _run(get_store().delete_project, current_user(authorization)["id"], project_id)
    return {"ok": True}
