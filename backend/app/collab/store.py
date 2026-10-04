"""SQLite storage: users and sessions, teams, configurable roles, invitations, shared projects.

Based on the team prototype from the Asya_feature_MakeTeam branch (teams, invite links, versioned
project saves); extended with registration and login (scrypt password hashes), invitations by
e-mail / login / personal id with accept and decline, and roles that the team head creates and
configures permission by permission, including which projects the role sees.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import permissions as P
from .security import DUMMY_HASH, hash_password, new_token, public_id, token_hash, verify_password

SESSION_DAYS = 30
LOGIN_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class CollabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id() -> str:
    return secrets.token_urlsafe(12)


def _clean(value: str, what: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if not value or len(value) > limit:
        raise CollabError(400, f"Укажите {what} не длиннее {limit} символов")
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,                -- personal id people can share: 8 chars
    login TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#66746f',
    permissions TEXT NOT NULL DEFAULT '[]',
    scope TEXT,                         -- NULL = all projects, else JSON list of project ids
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (team_id, user_id)
);
CREATE TABLE IF NOT EXISTS member_roles (
    team_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (team_id, user_id, role_id),
    FOREIGN KEY (team_id, user_id) REFERENCES members(team_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS invites (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    invited_by TEXT NOT NULL REFERENCES users(id),
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,  -- known user, or
    email TEXT,                                            -- e-mail of someone not registered yet
    role_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','cancelled')),
    created_at TEXT NOT NULL,
    responded_at TEXT
);
CREATE TABLE IF NOT EXISTS links (
    token TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    role_ids TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    xml TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    plan_json TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
"""


class CollabStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fails: dict[str, list[float]] = {}
        self._fails_lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ================================================================== users and sessions
    def register(self, login: str, email: str, name: str, password: str) -> tuple[dict, str]:
        login = (login or "").strip().lower()
        email = (email or "").strip().lower()
        if not LOGIN_RE.match(login):
            raise CollabError(400, "Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание")
        if not EMAIL_RE.match(email) or len(email) > 254:
            raise CollabError(400, "Укажите корректную почту")
        name = _clean(name, "имя", 80)
        if len(password or "") < 8 or len(password) > 200:
            raise CollabError(400, "Пароль: не короче 8 символов")
        if password.lower() in (login, email):
            raise CollabError(400, "Пароль не должен совпадать с логином или почтой")
        pw = hash_password(password)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
                raise CollabError(409, "Такой логин уже занят")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise CollabError(409, "Эта почта уже зарегистрирована")
            uid = public_id()
            while conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
                uid = public_id()
            conn.execute("INSERT INTO users (id, login, email, name, password_hash, created_at) VALUES (?,?,?,?,?,?)",
                         (uid, login, email, name, pw, _now()))
            # invitations sent to this e-mail before registration now belong to the user
            conn.execute("UPDATE invites SET user_id = ? WHERE email = ? AND user_id IS NULL AND status = 'pending'",
                         (uid, email))
            token = self._new_session(conn, uid)
        return self.user(uid), token

    def login(self, who: str, password: str) -> tuple[dict, str]:
        who = (who or "").strip().lower()
        self._check_throttle(who)
        with self._conn() as conn:
            row = conn.execute("SELECT id, password_hash FROM users WHERE login = ? OR email = ? OR id = ?",
                               (who, who, who.upper().replace("#", "").replace("-", ""))).fetchone()
            ok = verify_password(password or "", row["password_hash"] if row else DUMMY_HASH)
            if not row or not ok:
                self._note_fail(who)
                raise CollabError(401, "Неверный логин или пароль")
            token = self._new_session(conn, row["id"])
        with self._fails_lock:
            self._fails.pop(who, None)
        return self.user(row["id"]), token

    def _check_throttle(self, who: str):
        with self._fails_lock:
            recent = [t for t in self._fails.get(who, []) if time.time() - t < 300]
            self._fails[who] = recent
        if len(recent) >= 5:
            raise CollabError(429, "Слишком много неудачных попыток. Подождите несколько минут")

    def _note_fail(self, who: str):
        with self._fails_lock:
            self._fails.setdefault(who, []).append(time.time())

    def _new_session(self, conn, user_id: str) -> str:
        token = new_token()
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                     (token_hash(token), user_id, _now(), expires))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
        return token

    def user_by_token(self, token: str) -> dict | None:
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute("""SELECT u.id, u.login, u.email, u.name FROM sessions s JOIN users u ON u.id = s.user_id
                                  WHERE s.token_hash = ? AND s.expires_at > ?""", (token_hash(token), _now())).fetchone()
        return dict(row) if row else None

    def logout(self, token: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def user(self, user_id: str) -> dict:
        with self._conn() as conn:
            row = conn.execute("SELECT id, login, email, name, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise CollabError(404, "Пользователь не найден")
        return dict(row)

    def update_profile(self, user_id: str, name: str) -> dict:
        name = _clean(name, "имя", 80)
        with self._conn() as conn:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        return self.user(user_id)

    def change_password(self, user_id: str, old: str, new: str, keep_token: str):
        if len(new or "") < 8 or len(new) > 200:
            raise CollabError(400, "Новый пароль: не короче 8 символов")
        with self._conn() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if not row or not verify_password(old or "", row["password_hash"]):
                raise CollabError(403, "Текущий пароль указан неверно")
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))
            # other sessions are closed: a stolen session does not survive a password change
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, token_hash(keep_token)))

    # ================================================================== access model
    def _team(self, conn, team_id: str):
        team = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
        if team is None:
            raise CollabError(404, "Команда не найдена")
        return team

    def _roles_of(self, conn, team_id: str, user_id: str) -> list[sqlite3.Row]:
        return conn.execute("""SELECT r.* FROM member_roles mr JOIN roles r ON r.id = mr.role_id
                               WHERE mr.team_id = ? AND mr.user_id = ?""", (team_id, user_id)).fetchall()

    def _access(self, conn, team_id: str, user_id: str) -> dict:
        """{'owner': bool, 'team': set, 'roles': rows} or 404 for non-members."""
        team = self._team(conn, team_id)
        member = conn.execute("SELECT 1 FROM members WHERE team_id = ? AND user_id = ?", (team_id, user_id)).fetchone()
        if member is None:
            raise CollabError(404, "Команда не найдена")
        owner = team["owner_id"] == user_id
        roles = self._roles_of(conn, team_id, user_id)
        team_perms = set(P.ALL) if owner else {p for r in roles for p in json.loads(r["permissions"])}
        return {"owner": owner, "team": team_perms, "roles": roles, "team_row": team}

    def _project_perms(self, access: dict, project_id: str) -> set[str]:
        if access["owner"]:
            return set(P.ALL)
        out: set[str] = set()
        for r in access["roles"]:
            scope = json.loads(r["scope"]) if r["scope"] else None
            if scope is None or project_id in scope:
                out |= set(json.loads(r["permissions"]))
        return out & P.PROJECT_PERMS if "view" in out else set()

    def _require(self, access: dict, perm: str, message: str):
        if perm not in access["team"]:
            raise CollabError(403, message)

    def project_access(self, user_id: str, project_id: str) -> set[str]:
        """Permissions of a user on a project (used by the API guard for LLM and run endpoints)."""
        with self._conn() as conn:
            project = conn.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project is None:
                raise CollabError(404, "Проект не найден")
            try:
                access = self._access(conn, project["team_id"], user_id)
            except CollabError:
                raise CollabError(404, "Проект не найден")
            perms = self._project_perms(access, project_id)
        if "view" not in perms:
            raise CollabError(404, "Проект не найден")
        return perms

    # ================================================================== teams
    def list_teams(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""SELECT t.id, t.name, t.owner_id FROM teams t JOIN members m ON m.team_id = t.id
                                   WHERE m.user_id = ? ORDER BY t.name COLLATE NOCASE""", (user_id,)).fetchall()
            out = []
            for t in rows:
                roles = self._roles_of(conn, t["id"], user_id)
                out.append({"id": t["id"], "name": t["name"], "is_owner": t["owner_id"] == user_id,
                            "roles": [r["name"] for r in roles]})
        return out

    def create_team(self, user_id: str, name: str) -> dict:
        name = _clean(name, "название команды", 80)
        team_id = _id()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO teams (id, name, owner_id, created_at) VALUES (?,?,?,?)", (team_id, name, user_id, _now()))
            conn.execute("INSERT INTO members (team_id, user_id, joined_at) VALUES (?,?,?)", (team_id, user_id, _now()))
            for r in P.DEFAULT_ROLES:
                conn.execute("INSERT INTO roles (id, team_id, name, color, permissions, scope, created_at) VALUES (?,?,?,?,?,NULL,?)",
                             (_id(), team_id, r["name"], r["color"], json.dumps(P.normalize(r["permissions"])), _now()))
        return self.team_detail(user_id, team_id)

    def rename_team(self, user_id: str, team_id: str, name: str) -> dict:
        name = _clean(name, "название команды", 80)
        with self._conn() as conn:
            access = self._access(conn, team_id, user_id)
            if not access["owner"]:
                raise CollabError(403, "Переименовать команду может только её глава")
            conn.execute("UPDATE teams SET name = ? WHERE id = ?", (name, team_id))
        return self.team_detail(user_id, team_id)

    def delete_team(self, user_id: str, team_id: str):
        with self._conn() as conn:
            access = self._access(conn, team_id, user_id)
            if not access["owner"]:
                raise CollabError(403, "Удалить команду может только её глава")
            conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))

    def team_detail(self, user_id: str, team_id: str) -> dict:
        with self._conn() as conn:
            access = self._access(conn, team_id, user_id)
            team = access["team_row"]
            my_roles = [r["id"] for r in access["roles"]]
            base = {"id": team["id"], "name": team["name"], "owner_id": team["owner_id"], "is_owner": access["owner"],
                    "my_roles": my_roles, "permissions": sorted(access["team"]), "catalog": P.PERMISSIONS}
            if not access["owner"] and not access["roles"]:
                # no role: the member sees nothing but the fact of membership
                return {**base, "members": [], "roles": [], "projects": [], "invites": [], "links": [], "no_access": True}
            roles = conn.execute("SELECT * FROM roles WHERE team_id = ? ORDER BY created_at", (team_id,)).fetchall()
            members = conn.execute("""SELECT u.id, u.name, u.login, m.joined_at FROM members m JOIN users u ON u.id = m.user_id
                                      WHERE m.team_id = ? ORDER BY u.name COLLATE NOCASE""", (team_id,)).fetchall()
            member_roles: dict[str, list[str]] = {}
            for mr in conn.execute("SELECT user_id, role_id FROM member_roles WHERE team_id = ?", (team_id,)):
                member_roles.setdefault(mr["user_id"], []).append(mr["role_id"])
            projects = []
            for p in conn.execute("""SELECT p.id, p.title, p.version, p.updated_at, COALESCE(u.name, '') AS updated_by_name
                                     FROM projects p LEFT JOIN users u ON u.id = p.updated_by
                                     WHERE p.team_id = ? ORDER BY p.updated_at DESC""", (team_id,)):
                perms = self._project_perms(access, p["id"])
                if "view" in perms:
                    projects.append({**dict(p), "permissions": sorted(perms)})
            manage = "manage_members" in access["team"]
            invites, links = [], []
            if manage:
                invites = [self._invite_payload(conn, i) for i in conn.execute(
                    "SELECT * FROM invites WHERE team_id = ? AND status = 'pending' ORDER BY created_at DESC", (team_id,))]
                links = [{"token": l["token"], "role_ids": json.loads(l["role_ids"]), "created_at": l["created_at"]}
                         for l in conn.execute("SELECT * FROM links WHERE team_id = ? AND revoked = 0 ORDER BY created_at DESC",
                                               (team_id,))]
        return {**base, "no_access": False,
                "members": [{**dict(m), "is_owner": m["id"] == team["owner_id"], "role_ids": member_roles.get(m["id"], [])}
                            for m in members],
                "roles": [self._role_payload(r) for r in roles], "projects": projects,
                "invites": invites, "links": links}

    @staticmethod
    def _role_payload(r) -> dict:
        return {"id": r["id"], "name": r["name"], "color": r["color"], "permissions": json.loads(r["permissions"]),
                "scope": json.loads(r["scope"]) if r["scope"] else None}

    # ================================================================== roles
    def _check_role_input(self, conn, access: dict, team_id: str, name: str, color: str, perms, scope):
        name = _clean(name, "название роли", 60)
        color = color if isinstance(color, str) and COLOR_RE.match(color) else "#66746f"
        perms = P.normalize(perms)
        if set(perms) & P.MANAGING and not access["owner"]:
            raise CollabError(403, "Права на управление участниками и ролями выдаёт только глава команды")
        if scope is not None:
            if not isinstance(scope, list):
                raise CollabError(400, "Область видимости — список проектов или «все проекты»")
            known = {r["id"] for r in conn.execute("SELECT id FROM projects WHERE team_id = ?", (team_id,))}
            scope = [p for p in dict.fromkeys(scope) if p in known]
        return name, color, perms, scope

    def create_role(self, user_id: str, team_id: str, name: str, color: str, perms, scope) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, user_id)
            self._require(access, "manage_roles", "Нет права настраивать роли")
            name, color, perms, scope = self._check_role_input(conn, access, team_id, name, color, perms, scope)
            if conn.execute("SELECT 1 FROM roles WHERE team_id = ? AND name = ?", (team_id, name)).fetchone():
                raise CollabError(409, "Роль с таким названием уже есть")
            conn.execute("INSERT INTO roles (id, team_id, name, color, permissions, scope, created_at) VALUES (?,?,?,?,?,?,?)",
                         (_id(), team_id, name, color, json.dumps(perms), json.dumps(scope) if scope is not None else None, _now()))
        return self.team_detail(user_id, team_id)

    def update_role(self, user_id: str, role_id: str, name: str, color: str, perms, scope) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            role = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
            if role is None:
                raise CollabError(404, "Роль не найдена")
            access = self._access(conn, role["team_id"], user_id)
            self._require(access, "manage_roles", "Нет права настраивать роли")
            if set(json.loads(role["permissions"])) & P.MANAGING and not access["owner"]:
                raise CollabError(403, "Управляющие роли меняет только глава команды")
            name, color, perms, scope = self._check_role_input(conn, access, role["team_id"], name, color, perms, scope)
            if conn.execute("SELECT 1 FROM roles WHERE team_id = ? AND name = ? AND id != ?",
                            (role["team_id"], name, role_id)).fetchone():
                raise CollabError(409, "Роль с таким названием уже есть")
            conn.execute("UPDATE roles SET name = ?, color = ?, permissions = ?, scope = ? WHERE id = ?",
                         (name, color, json.dumps(perms), json.dumps(scope) if scope is not None else None, role_id))
        return self.team_detail(user_id, role["team_id"])

    def delete_role(self, user_id: str, role_id: str) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            role = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
            if role is None:
                raise CollabError(404, "Роль не найдена")
            access = self._access(conn, role["team_id"], user_id)
            self._require(access, "manage_roles", "Нет права настраивать роли")
            if set(json.loads(role["permissions"])) & P.MANAGING and not access["owner"]:
                raise CollabError(403, "Управляющие роли удаляет только глава команды")
            conn.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        return self.team_detail(user_id, role["team_id"])

    def _valid_role_ids(self, conn, access: dict, team_id: str, role_ids) -> list[str]:
        if not isinstance(role_ids, list):
            raise CollabError(400, "Роли — список")
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM roles WHERE team_id = ?", (team_id,))}
        out = []
        for rid in dict.fromkeys(role_ids):
            if rid not in rows:
                raise CollabError(400, "Неизвестная роль")
            if set(json.loads(rows[rid]["permissions"])) & P.MANAGING and not access["owner"]:
                raise CollabError(403, "Управляющие роли выдаёт только глава команды")
            out.append(rid)
        return out

    # ================================================================== members
    def set_member_roles(self, actor_id: str, team_id: str, user_id: str, role_ids) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, actor_id)
            self._require(access, "manage_members", "Нет права выдавать роли")
            if conn.execute("SELECT 1 FROM members WHERE team_id = ? AND user_id = ?", (team_id, user_id)).fetchone() is None:
                raise CollabError(404, "Участник не найден")
            if user_id == access["team_row"]["owner_id"]:
                raise CollabError(400, "Глава команды может всё — роли ему не нужны")
            ids = self._valid_role_ids(conn, access, team_id, role_ids)
            managing_now = {r["id"] for r in self._roles_of(conn, team_id, user_id)
                            if set(json.loads(r["permissions"])) & P.MANAGING}
            if managing_now - set(ids) and not access["owner"]:
                raise CollabError(403, "Управляющие роли снимает только глава команды")
            conn.execute("DELETE FROM member_roles WHERE team_id = ? AND user_id = ?", (team_id, user_id))
            for rid in ids:
                conn.execute("INSERT INTO member_roles (team_id, user_id, role_id) VALUES (?,?,?)", (team_id, user_id, rid))
        return self.team_detail(actor_id, team_id)

    def remove_member(self, actor_id: str, team_id: str, user_id: str) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, actor_id)
            if user_id == access["team_row"]["owner_id"]:
                raise CollabError(400, "Глава не может покинуть команду — её можно только удалить")
            if actor_id != user_id:
                self._require(access, "manage_members", "Нет права исключать участников")
                target_roles = self._roles_of(conn, team_id, user_id)
                if any(set(json.loads(r["permissions"])) & P.MANAGING for r in target_roles) and not access["owner"]:
                    raise CollabError(403, "Участника с управляющей ролью исключает только глава")
            if conn.execute("DELETE FROM members WHERE team_id = ? AND user_id = ?", (team_id, user_id)).rowcount == 0:
                raise CollabError(404, "Участник не найден")
        if actor_id == user_id:
            return {"left": True, "id": team_id}
        return self.team_detail(actor_id, team_id)

    # ================================================================== invitations
    def find_user(self, conn, who: str):
        who = (who or "").strip()
        if not who:
            raise CollabError(400, "Укажите почту, логин или ID")
        if "@" in who:
            return conn.execute("SELECT * FROM users WHERE email = ?", (who.lower(),)).fetchone(), who.lower()
        pid = who.upper().lstrip("#").replace("-", "")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (pid,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM users WHERE login = ?", (who.lower(),)).fetchone()
        return row, None

    def invite(self, actor_id: str, team_id: str, who: str, role_ids) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, actor_id)
            self._require(access, "manage_members", "Нет права приглашать участников")
            ids = self._valid_role_ids(conn, access, team_id, role_ids or [])
            user, email = self.find_user(conn, who)
            if user is None and not email:
                raise CollabError(404, "Пользователь с таким логином или ID не найден. Пригласите его по почте")
            if email and not EMAIL_RE.match(email):
                raise CollabError(400, "Укажите корректную почту")
            if user is not None:
                if conn.execute("SELECT 1 FROM members WHERE team_id = ? AND user_id = ?", (team_id, user["id"])).fetchone():
                    raise CollabError(409, "Этот человек уже в команде")
                dup = conn.execute("SELECT 1 FROM invites WHERE team_id = ? AND user_id = ? AND status = 'pending'",
                                   (team_id, user["id"])).fetchone()
            else:
                dup = conn.execute("SELECT 1 FROM invites WHERE team_id = ? AND email = ? AND status = 'pending'",
                                   (team_id, email)).fetchone()
            if dup:
                raise CollabError(409, "Приглашение уже отправлено и ждёт ответа")
            conn.execute("""INSERT INTO invites (id, team_id, invited_by, user_id, email, role_ids, status, created_at)
                            VALUES (?,?,?,?,?,?,'pending',?)""",
                         (_id(), team_id, actor_id, user["id"] if user else None, None if user else email, json.dumps(ids), _now()))
        detail = self.team_detail(actor_id, team_id)
        detail["notice"] = (f"Приглашение отправлено: {user['name']} увидит его в разделе «Команды»." if user else
                            f"{email} ещё не зарегистрирован: приглашение появится после регистрации с этой почтой.")
        return detail

    def _invite_payload(self, conn, i) -> dict:
        team = conn.execute("SELECT name FROM teams WHERE id = ?", (i["team_id"],)).fetchone()
        by = conn.execute("SELECT name FROM users WHERE id = ?", (i["invited_by"],)).fetchone()
        target = conn.execute("SELECT name, login FROM users WHERE id = ?", (i["user_id"],)).fetchone() if i["user_id"] else None
        role_ids = json.loads(i["role_ids"])
        roles = [r["name"] for r in conn.execute(
            f"SELECT name FROM roles WHERE id IN ({','.join('?' * len(role_ids))})", role_ids)] if role_ids else []
        return {"id": i["id"], "team_id": i["team_id"], "team_name": team["name"] if team else "",
                "invited_by": by["name"] if by else "", "to": (f"{target['name']} ({target['login']})" if target else i["email"]),
                "roles": roles, "role_ids": role_ids, "status": i["status"], "created_at": i["created_at"]}

    def my_invites(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()["email"]
            rows = conn.execute("""SELECT * FROM invites WHERE status = 'pending' AND (user_id = ? OR (user_id IS NULL AND email = ?))
                                   ORDER BY created_at DESC""", (user_id, email)).fetchall()
            return [self._invite_payload(conn, i) for i in rows]

    def respond(self, user_id: str, invite_id: str, accept: bool) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            email = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()["email"]
            inv = conn.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
            if inv is None or inv["status"] != "pending" or not (inv["user_id"] == user_id or (inv["user_id"] is None and inv["email"] == email)):
                raise CollabError(404, "Приглашение не найдено или уже обработано")
            conn.execute("UPDATE invites SET status = ?, responded_at = ?, user_id = ? WHERE id = ?",
                         ("accepted" if accept else "declined", _now(), user_id, invite_id))
            if accept:
                self._add_member(conn, inv["team_id"], user_id, json.loads(inv["role_ids"]))
        return {"team_id": inv["team_id"], "accepted": accept}

    def _add_member(self, conn, team_id: str, user_id: str, role_ids: list[str]):
        if conn.execute("SELECT 1 FROM members WHERE team_id = ? AND user_id = ?", (team_id, user_id)).fetchone():
            return
        conn.execute("INSERT INTO members (team_id, user_id, joined_at) VALUES (?,?,?)", (team_id, user_id, _now()))
        valid = {r["id"] for r in conn.execute("SELECT id FROM roles WHERE team_id = ?", (team_id,))}
        for rid in role_ids:
            if rid in valid:
                conn.execute("INSERT OR IGNORE INTO member_roles (team_id, user_id, role_id) VALUES (?,?,?)", (team_id, user_id, rid))

    def cancel_invite(self, actor_id: str, invite_id: str) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            inv = conn.execute("SELECT * FROM invites WHERE id = ? AND status = 'pending'", (invite_id,)).fetchone()
            if inv is None:
                raise CollabError(404, "Приглашение не найдено")
            access = self._access(conn, inv["team_id"], actor_id)
            self._require(access, "manage_members", "Нет права отзывать приглашения")
            conn.execute("UPDATE invites SET status = 'cancelled', responded_at = ? WHERE id = ?", (_now(), invite_id))
        return self.team_detail(actor_id, inv["team_id"])

    # invite links (from the original prototype): the person sees the team and accepts or declines
    def create_link(self, actor_id: str, team_id: str, role_ids) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, actor_id)
            self._require(access, "manage_members", "Нет права приглашать участников")
            ids = self._valid_role_ids(conn, access, team_id, role_ids or [])
            token = secrets.token_urlsafe(18)
            conn.execute("INSERT INTO links (token, team_id, role_ids, created_by, created_at) VALUES (?,?,?,?,?)",
                         (token, team_id, json.dumps(ids), actor_id, _now()))
        return {"token": token, "path": f"/join/{token}"}

    def revoke_link(self, actor_id: str, token: str):
        with self._conn() as conn:
            link = conn.execute("SELECT * FROM links WHERE token = ? AND revoked = 0", (token,)).fetchone()
            if link is None:
                raise CollabError(404, "Ссылка не найдена")
            access = self._access(conn, link["team_id"], actor_id)
            self._require(access, "manage_members", "Нет права отзывать ссылки")
            conn.execute("UPDATE links SET revoked = 1 WHERE token = ?", (token,))

    def link_info(self, user_id: str, token: str) -> dict:
        with self._conn() as conn:
            link = conn.execute("SELECT * FROM links WHERE token = ? AND revoked = 0", (token,)).fetchone()
            if link is None:
                raise CollabError(404, "Приглашение недействительно или отозвано")
            team = self._team(conn, link["team_id"])
            by = conn.execute("SELECT name FROM users WHERE id = ?", (link["created_by"],)).fetchone()
            ids = json.loads(link["role_ids"])
            roles = [r["name"] for r in conn.execute(
                f"SELECT name FROM roles WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
            member = conn.execute("SELECT 1 FROM members WHERE team_id = ? AND user_id = ?", (team["id"], user_id)).fetchone()
        return {"team_id": team["id"], "team_name": team["name"], "invited_by": by["name"] if by else "",
                "roles": roles, "already_member": bool(member)}

    def accept_link(self, user_id: str, token: str) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            link = conn.execute("SELECT * FROM links WHERE token = ? AND revoked = 0", (token,)).fetchone()
            if link is None:
                raise CollabError(404, "Приглашение недействительно или отозвано")
            self._add_member(conn, link["team_id"], user_id, json.loads(link["role_ids"]))
        return {"team_id": link["team_id"], "accepted": True}

    # ================================================================== projects
    def create_project(self, actor_id: str, team_id: str, title: str) -> dict:
        title = _clean(title, "название проекта", 200)
        project_id = _id()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            access = self._access(conn, team_id, actor_id)
            self._require(access, "create_projects", "Нет права создавать проекты")
            conn.execute("""INSERT INTO projects (id, team_id, title, version, updated_at, updated_by)
                            VALUES (?,?,?,1,?,?)""", (project_id, team_id, title, _now(), actor_id))
            # a role limited to some projects still sees the projects its members create
            for r in access["roles"]:
                if r["scope"] and "create_projects" in json.loads(r["permissions"]):
                    scope = json.loads(r["scope"]) + [project_id]
                    conn.execute("UPDATE roles SET scope = ? WHERE id = ?", (json.dumps(scope), r["id"]))
        return self.get_project(actor_id, project_id)

    def _project(self, conn, user_id: str, project_id: str):
        project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if project is None:
            raise CollabError(404, "Проект не найден")
        try:
            access = self._access(conn, project["team_id"], user_id)
        except CollabError:
            raise CollabError(404, "Проект не найден")
        perms = self._project_perms(access, project_id)
        if "view" not in perms:
            raise CollabError(404, "Проект не найден")
        return project, perms

    def get_project(self, user_id: str, project_id: str) -> dict:
        with self._conn() as conn:
            project, perms = self._project(conn, user_id, project_id)
            return self._project_payload(conn, project, perms)

    def save_project(self, actor_id: str, project_id: str, *, version: int, title: str | None, text: str, xml: str,
                     code: str, plan: dict | None) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project, perms = self._project(conn, actor_id, project_id)
            if project["version"] != version:
                raise CollabError(409, "Кто-то сохранил более новую версию. Загрузите её, прежде чем сохранять свою.")
            next_title = " ".join((title or "").split()) or project["title"]
            if len(next_title) > 200:
                raise CollabError(400, "Название проекта слишком длинное")
            parts = P.changed_parts(project["xml"], xml, project["text"], text)
            if code != project["code"]:
                parts.add("code")
            old_plan = json.loads(project["plan_json"]) if project["plan_json"] else None
            if plan != old_plan:
                parts.add("content")
            if next_title != project["title"]:
                parts.add("content")
            denied = P.forbidden_changes(parts, perms)
            if denied:
                raise CollabError(403, "Ваши роли не позволяют " + "; ".join(denied) + ". Изменения не сохранены.")
            if not parts:
                return self._project_payload(conn, project, perms)       # nothing to save
            conn.execute("""UPDATE projects SET title = ?, text = ?, xml = ?, code = ?, plan_json = ?, version = ?,
                            updated_at = ?, updated_by = ? WHERE id = ?""",
                         (next_title, text, xml, code, json.dumps(plan, ensure_ascii=False) if plan else "",
                          project["version"] + 1, _now(), actor_id, project_id))
            saved = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._project_payload(conn, saved, perms)

    def delete_project(self, actor_id: str, project_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _, perms = self._project(conn, actor_id, project_id)
            if "delete_projects" not in perms:
                raise CollabError(403, "Нет права удалять этот проект")
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def _project_payload(self, conn, project, perms: set[str]) -> dict:
        author = ""
        if project["updated_by"]:
            row = conn.execute("SELECT name FROM users WHERE id = ?", (project["updated_by"],)).fetchone()
            author = row["name"] if row else ""
        plan = None
        if project["plan_json"]:
            try:
                plan = json.loads(project["plan_json"])
            except json.JSONDecodeError:
                plan = None
        team = conn.execute("SELECT name FROM teams WHERE id = ?", (project["team_id"],)).fetchone()
        return {"id": project["id"], "team_id": project["team_id"], "team_name": team["name"] if team else "",
                "title": project["title"], "text": project["text"], "xml": project["xml"], "code": project["code"],
                "plan": plan, "version": project["version"], "updated_at": project["updated_at"],
                "updated_by": project["updated_by"], "updated_by_name": author, "permissions": sorted(perms)}
