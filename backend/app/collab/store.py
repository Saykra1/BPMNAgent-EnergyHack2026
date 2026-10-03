"""SQLite-хранилище команд. Приглашение действует, пока владелец его не отзовёт."""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROLES = ("owner", "editor", "viewer")
EDIT_ROLES = ("owner", "editor")


class CollabError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id() -> str:
    return secrets.token_urlsafe(12)


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 80:
        raise CollabError(400, "Укажите имя не длиннее 80 символов")
    return name


class CollabStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS members (
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'editor', 'viewer')),
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (team_id, user_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS invites (
                    token TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner', 'editor', 'viewer')),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (team_id) REFERENCES teams(id)
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    xml TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL DEFAULT '',
                    plan_json TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT,
                    FOREIGN KEY (team_id) REFERENCES teams(id)
                );
                """
            )

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

    def create_user(self, name: str) -> dict:
        name = clean_name(name)
        user = {"id": _id(), "name": name, "token": secrets.token_urlsafe(24), "created_at": _now()}
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, name, token, created_at) VALUES (?, ?, ?, ?)",
                (user["id"], user["name"], user["token"], user["created_at"]),
            )
        return user

    def user_by_token(self, token: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT id, name, token FROM users WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None

    def rename_user(self, user_id: str, name: str) -> dict:
        name = clean_name(name)
        with self._conn() as conn:
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
            row = conn.execute("SELECT id, name, token FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise CollabError(404, "Пользователь не найден")
        return dict(row)

    def list_teams(self, user_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.name, m.role
                FROM teams t
                JOIN members m ON m.team_id = t.id
                WHERE m.user_id = ?
                ORDER BY t.name COLLATE NOCASE
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_team(self, user_id: str, name: str) -> dict:
        name = " ".join((name or "").split())
        if not name or len(name) > 80:
            raise CollabError(400, "Укажите название команды не длиннее 80 символов")
        team_id = _id()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO teams (id, name, created_at) VALUES (?, ?, ?)",
                (team_id, name, _now()),
            )
            conn.execute(
                "INSERT INTO members (team_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
                (team_id, user_id, _now()),
            )
        return self.team_detail(user_id, team_id)

    def team_detail(self, user_id: str, team_id: str) -> dict:
        with self._conn() as conn:
            member = self._member(conn, team_id, user_id)
            if member is None:
                raise CollabError(404, "Команда не найдена")
            team = conn.execute("SELECT id, name FROM teams WHERE id = ?", (team_id,)).fetchone()
            members = conn.execute(
                """
                SELECT m.user_id, u.name, m.role
                FROM members m JOIN users u ON u.id = m.user_id
                WHERE m.team_id = ?
                ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END, u.name
                """,
                (team_id,),
            ).fetchall()
            projects = conn.execute(
                """
                SELECT p.id, p.title, p.version, p.updated_at, COALESCE(u.name, '') AS updated_by_name
                FROM projects p
                LEFT JOIN users u ON u.id = p.updated_by
                WHERE p.team_id = ?
                ORDER BY p.updated_at DESC
                """,
                (team_id,),
            ).fetchall()
            invites: list[sqlite3.Row] = []
            if member["role"] == "owner":
                invites = conn.execute(
                    """
                    SELECT token, role, created_at FROM invites
                    WHERE team_id = ? AND revoked = 0
                    ORDER BY created_at DESC
                    """,
                    (team_id,),
                ).fetchall()
        return {
            "id": team["id"],
            "name": team["name"],
            "role": member["role"],
            "members": [dict(r) for r in members],
            "projects": [dict(r) for r in projects],
            "invites": [dict(r) for r in invites],
        }

    def set_role(self, actor_id: str, team_id: str, user_id: str, role: str) -> dict:
        self._check_role(role)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, team_id, actor_id)
            target = self._member(conn, team_id, user_id)
            if target is None:
                raise CollabError(404, "Участник не найден")
            if target["role"] == "owner" and role != "owner" and self._owner_count(conn, team_id) <= 1:
                raise CollabError(409, "В команде должен остаться хотя бы один владелец")
            conn.execute(
                "UPDATE members SET role = ? WHERE team_id = ? AND user_id = ?",
                (role, team_id, user_id),
            )
        return self.team_detail(actor_id, team_id)

    def remove_member(self, actor_id: str, team_id: str, user_id: str) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, team_id, actor_id)
            target = self._member(conn, team_id, user_id)
            if target is None:
                raise CollabError(404, "Участник не найден")
            if target["role"] == "owner" and self._owner_count(conn, team_id) <= 1:
                raise CollabError(409, "В команде должен остаться хотя бы один владелец")
            conn.execute("DELETE FROM members WHERE team_id = ? AND user_id = ?", (team_id, user_id))
        if actor_id == user_id:
            return {"left": True, "id": team_id}
        return self.team_detail(actor_id, team_id)

    def create_invite(self, actor_id: str, team_id: str, role: str) -> dict:
        self._check_role(role)
        token = secrets.token_urlsafe(18)
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_owner(conn, team_id, actor_id)
            conn.execute(
                """
                INSERT INTO invites (token, team_id, role, created_by, created_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (token, team_id, role, actor_id, _now()),
            )
        return {"token": token, "role": role}

    def revoke_invite(self, actor_id: str, token: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            invite = conn.execute("SELECT team_id, revoked FROM invites WHERE token = ?", (token,)).fetchone()
            if invite is None or invite["revoked"]:
                raise CollabError(404, "Приглашение не найдено")
            self._require_owner(conn, invite["team_id"], actor_id)
            conn.execute("UPDATE invites SET revoked = 1 WHERE token = ?", (token,))

    def join(self, token: str, user: dict) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            invite = conn.execute(
                "SELECT team_id, role, revoked FROM invites WHERE token = ?",
                (token,),
            ).fetchone()
            if invite is None or invite["revoked"]:
                raise CollabError(404, "Приглашение недействительно")
            existing = self._member(conn, invite["team_id"], user["id"])
            if existing is None:
                conn.execute(
                    "INSERT INTO members (team_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)",
                    (invite["team_id"], user["id"], invite["role"], _now()),
                )
                role = invite["role"]
            else:
                role = existing["role"]
            team_id = invite["team_id"]
        return {"team_id": team_id, "role": role, "user": user}

    def create_project(self, actor_id: str, team_id: str, title: str) -> dict:
        title = " ".join((title or "").split())
        if not title or len(title) > 200:
            raise CollabError(400, "Укажите название проекта не длиннее 200 символов")
        project_id = _id()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            member = self._require_member(conn, team_id, actor_id)
            if member["role"] not in EDIT_ROLES:
                raise CollabError(403, "Наблюдатель не может создавать проекты")
            conn.execute(
                """
                INSERT INTO projects
                    (id, team_id, title, text, xml, code, plan_json, version, updated_at, updated_by)
                VALUES (?, ?, ?, '', '', '', '', 1, ?, ?)
                """,
                (project_id, team_id, title, _now(), actor_id),
            )
        return self.get_project(actor_id, project_id)

    def get_project(self, user_id: str, project_id: str) -> dict:
        with self._conn() as conn:
            project = self._project_row(conn, project_id)
            member = self._member(conn, project["team_id"], user_id)
            if member is None:
                raise CollabError(404, "Проект не найден")
            return self._project_payload(conn, project, member["role"])

    def save_project(
        self,
        actor_id: str,
        project_id: str,
        *,
        version: int,
        title: str | None,
        text: str,
        xml: str,
        code: str,
        plan: dict | None,
    ) -> dict:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._project_row(conn, project_id)
            member = self._member(conn, project["team_id"], actor_id)
            if member is None:
                raise CollabError(404, "Проект не найден")
            if member["role"] not in EDIT_ROLES:
                raise CollabError(403, "Наблюдатель не может сохранять схему")
            if project["version"] != version:
                raise CollabError(
                    409,
                    "Кто-то сохранил более новую версию. Загрузите её, прежде чем сохранять свою.",
                )
            next_title = " ".join((title or "").split()) or project["title"]
            if len(next_title) > 200:
                raise CollabError(400, "Название проекта слишком длинное")
            conn.execute(
                """
                UPDATE projects
                SET title = ?, text = ?, xml = ?, code = ?, plan_json = ?,
                    version = ?, updated_at = ?, updated_by = ?
                WHERE id = ?
                """,
                (
                    next_title,
                    text,
                    xml,
                    code,
                    json.dumps(plan, ensure_ascii=False) if plan else "",
                    project["version"] + 1,
                    _now(),
                    actor_id,
                    project_id,
                ),
            )
            saved = self._project_row(conn, project_id)
            return self._project_payload(conn, saved, member["role"])

    def delete_project(self, actor_id: str, project_id: str) -> None:
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project = self._project_row(conn, project_id)
            member = self._member(conn, project["team_id"], actor_id)
            if member is None:
                raise CollabError(404, "Проект не найден")
            if member["role"] != "owner":
                raise CollabError(403, "Удалять проект может только владелец")
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def _member(self, conn: sqlite3.Connection, team_id: str, user_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT team_id, user_id, role FROM members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id),
        ).fetchone()

    def _require_member(self, conn: sqlite3.Connection, team_id: str, user_id: str) -> sqlite3.Row:
        member = self._member(conn, team_id, user_id)
        if member is None:
            raise CollabError(404, "Команда не найдена")
        return member

    def _require_owner(self, conn: sqlite3.Connection, team_id: str, user_id: str) -> sqlite3.Row:
        member = self._require_member(conn, team_id, user_id)
        if member["role"] != "owner":
            raise CollabError(403, "Это может сделать только владелец")
        return member

    def _owner_count(self, conn: sqlite3.Connection, team_id: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM members WHERE team_id = ? AND role = 'owner'",
            (team_id,),
        ).fetchone()
        return int(row["n"])

    def _project_row(self, conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise CollabError(404, "Проект не найден")
        return row

    def _project_payload(self, conn: sqlite3.Connection, project: sqlite3.Row, role: str) -> dict:
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
        return {
            "id": project["id"],
            "team_id": project["team_id"],
            "title": project["title"],
            "text": project["text"],
            "xml": project["xml"],
            "code": project["code"],
            "plan": plan,
            "version": project["version"],
            "updated_at": project["updated_at"],
            "updated_by": project["updated_by"],
            "updated_by_name": author,
            "role": role,
        }

    @staticmethod
    def _check_role(role: str) -> None:
        if role not in ROLES:
            raise CollabError(400, "Неизвестная роль")
