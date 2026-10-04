"""Accounts, teams, configurable roles, invitations and project saves checked against roles.
Based on the team tests of the Asya_feature_MakeTeam branch."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.collab import routes
from app.collab.routes import init_store
from app.collab.security import hash_password, verify_password
from app.config import Settings
from app.llm.plan import parse_plan
from app.pipeline import build_ir

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = init_store(tmp_path / "app.db")
    monkeypatch.setattr(main, "get_settings", lambda: Settings(runs_dir=tmp_path, require_login=True))
    c = TestClient(main.app)
    c.store = store
    return c


def H(user):
    return {"Authorization": f"Bearer {user['token']}"}


def register(c, login, password="secret-pass-1"):
    r = c.post("/api/auth/register", json={"login": login, "email": f"{login}@example.com", "name": login.title(),
                                           "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def team_of(c, owner, name="Сеть"):
    r = c.post("/api/teams", json={"name": name}, headers=H(owner))
    assert r.status_code == 200, r.text
    return r.json()


def role_id(team, name):
    return next(r["id"] for r in team["roles"] if r["name"] == name)


def join(c, owner, team, user, role_names):
    team = c.get(f"/api/teams/{team['id']}", headers=H(owner)).json()
    r = c.post(f"/api/teams/{team['id']}/invites", json={"who": user["user"]["login"],
                                                          "role_ids": [role_id(team, n) for n in role_names]}, headers=H(owner))
    assert r.status_code == 200, r.text
    inv = c.get("/api/invites", headers=H(user)).json()[0]
    assert c.post(f"/api/invites/{inv['id']}/accept", headers=H(user)).status_code == 200


def xml(name="05_outage", mutate=None):
    p = json.loads((ROOT / "examples" / name / "plan.json").read_text("utf-8"))
    if mutate:
        mutate(p)
    return build_ir(parse_plan(p)).xml


# ================================================================== accounts
def test_password_is_hashed_with_salt():
    h1, h2 = hash_password("секрет-123"), hash_password("секрет-123")
    assert h1 != h2 and h1.startswith("scrypt$") and "секрет" not in h1
    assert verify_password("секрет-123", h1) and not verify_password("секрет-124", h1)


def test_register_login_logout_and_database_has_no_plain_secrets(client):
    u = register(client, "asya", "very-secret-77")
    assert len(u["user"]["id"]) == 8 and u["user"]["login"] == "asya"
    assert client.get("/api/auth/me", headers=H(u)).json()["user"]["email"] == "asya@example.com"
    raw = (Path(client.store.path)).read_bytes()
    assert b"very-secret-77" not in raw and u["token"].encode() not in raw
    # login by login, e-mail or personal id
    for who in ("asya", "ASYA@example.com", u["user"]["id"]):
        assert client.post("/api/auth/login", json={"login": who, "password": "very-secret-77"}).status_code == 200
    assert client.post("/api/auth/login", json={"login": "asya", "password": "wrong-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"login": "nobody", "password": "wrong-pass"}).status_code == 401
    client.post("/api/auth/logout", headers=H(u))
    assert client.get("/api/auth/me", headers=H(u)).status_code == 401


def test_registration_validation_and_duplicates(client):
    register(client, "boris")
    bad = [{"login": "b", "email": "x@example.com", "password": "12345678"},
           {"login": "boris2", "email": "not-an-email", "password": "12345678"},
           {"login": "boris3", "email": "b3@example.com", "password": "short"},
           {"login": "boris", "email": "other@example.com", "password": "12345678"},
           {"login": "boris4", "email": "boris@example.com", "password": "12345678"}]
    for body in bad:
        assert client.post("/api/auth/register", json={"name": "Б", **body}).status_code in (400, 409)


def test_login_throttling(client):
    register(client, "vera")
    for _ in range(5):
        client.post("/api/auth/login", json={"login": "vera", "password": "wrong-pass"})
    assert client.post("/api/auth/login", json={"login": "vera", "password": "secret-pass-1"}).status_code == 429


def test_api_requires_login_when_enabled(client):
    assert client.get("/api/examples").status_code == 401
    assert client.get("/api/auth/config").json()["require_login"] is True
    u = register(client, "gleb")
    assert client.get("/api/examples", headers=H(u)).status_code == 200


def test_guest_can_use_editor_but_not_teams_by_default(client, monkeypatch, tmp_path):
    assert Settings.model_fields["require_login"].default is False
    monkeypatch.setattr(main, "get_settings", lambda: Settings(runs_dir=tmp_path, require_login=False))
    assert client.get("/api/auth/config").json()["require_login"] is False
    assert client.get("/api/examples").status_code == 200
    assert client.post("/api/teams", json={"name": "Команда"}).status_code == 401


def test_change_password_closes_other_sessions(client):
    u = register(client, "dina")
    other = client.post("/api/auth/login", json={"login": "dina", "password": "secret-pass-1"}).json()
    assert client.post("/api/auth/password", json={"old_password": "nope-nope", "new_password": "new-pass-123"},
                       headers=H(u)).status_code == 403
    assert client.post("/api/auth/password", json={"old_password": "secret-pass-1", "new_password": "new-pass-123"},
                       headers=H(u)).status_code == 200
    assert client.get("/api/auth/me", headers=H(other)).status_code == 401
    assert client.get("/api/auth/me", headers=H(u)).status_code == 200


# ================================================================== invitations
def test_invite_by_login_email_id_accept_decline_and_pending_email(client):
    head = register(client, "head")
    team = team_of(client, head)
    a, b, c = register(client, "anna"), register(client, "bob"), register(client, "cat")
    for who in ("anna", "bob@example.com", "#" + c["user"]["id"][:4] + "-" + c["user"]["id"][4:]):
        r = client.post(f"/api/teams/{team['id']}/invites", json={"who": who, "role_ids": []}, headers=H(head))
        assert r.status_code == 200, (who, r.text)
    assert client.post(f"/api/teams/{team['id']}/invites", json={"who": "anna"}, headers=H(head)).status_code == 409
    assert client.post(f"/api/teams/{team['id']}/invites", json={"who": "ghost"}, headers=H(head)).status_code == 404
    inv_a = client.get("/api/invites", headers=H(a)).json()
    assert inv_a[0]["team_name"] == "Сеть" and inv_a[0]["invited_by"] == "Head"
    assert client.post(f"/api/invites/{inv_a[0]['id']}/accept", headers=H(a)).status_code == 200
    inv_b = client.get("/api/invites", headers=H(b)).json()
    assert client.post(f"/api/invites/{inv_b[0]['id']}/decline", headers=H(b)).status_code == 200
    # someone else cannot answer c's invitation
    inv_c = client.get("/api/invites", headers=H(c)).json()
    assert client.post(f"/api/invites/{inv_c[0]['id']}/accept", headers=H(a)).status_code == 404
    members = {m["login"] for m in client.get(f"/api/teams/{team['id']}", headers=H(head)).json()["members"]}
    assert members == {"head", "anna"}
    # an e-mail that is not registered yet: the invitation waits for registration
    r = client.post(f"/api/teams/{team['id']}/invites", json={"who": "new@example.com"}, headers=H(head)).json()
    assert "ещё не зарегистрирован" in r["notice"]
    new = client.post("/api/auth/register", json={"login": "newbie", "email": "new@example.com", "name": "Новичок",
                                                   "password": "secret-pass-1"}).json()
    assert [i["team_name"] for i in new["invites"]] == ["Сеть"]


def test_link_invite_needs_explicit_accept(client):
    head = register(client, "head2")
    team = team_of(client, head)
    link = client.post(f"/api/teams/{team['id']}/links", json={"role_ids": [role_id(team, "Наблюдатель")]},
                       headers=H(head)).json()
    u = register(client, "linker")
    info = client.get(f"/api/links/{link['token']}", headers=H(u)).json()
    assert info["team_name"] == "Сеть" and info["roles"] == ["Наблюдатель"] and not info["already_member"]
    assert client.get(f"/api/teams/{team['id']}", headers=H(u)).status_code == 404
    client.post(f"/api/links/{link['token']}/accept", headers=H(u))
    assert client.get(f"/api/teams/{team['id']}", headers=H(u)).json()["my_roles"] == [role_id(team, "Наблюдатель")]
    client.delete(f"/api/links/{link['token']}", headers=H(head))
    assert client.get(f"/api/links/{link['token']}", headers=H(u)).status_code == 404
    assert client.get("/join/whatever").status_code == 200        # the join page is the app itself


# ================================================================== roles and visibility
def test_member_without_roles_sees_nothing(client):
    head = register(client, "head3")
    team = team_of(client, head)
    proj = client.post(f"/api/teams/{team['id']}/projects", json={"title": "Авария"}, headers=H(head)).json()
    u = register(client, "norole")
    join(client, head, team, u, [])
    t = client.get(f"/api/teams/{team['id']}", headers=H(u)).json()
    assert t["no_access"] and t["projects"] == [] and t["members"] == [] and t["roles"] == []
    assert client.get(f"/api/projects/{proj['id']}", headers=H(u)).status_code == 404
    stranger = register(client, "stranger")
    assert client.get(f"/api/projects/{proj['id']}", headers=H(stranger)).status_code == 404


def test_custom_role_with_project_scope(client):
    head = register(client, "head4")
    team = team_of(client, head)
    p1 = client.post(f"/api/teams/{team['id']}/projects", json={"title": "Первый"}, headers=H(head)).json()
    p2 = client.post(f"/api/teams/{team['id']}/projects", json={"title": "Второй"}, headers=H(head)).json()
    t = client.post(f"/api/teams/{team['id']}/roles", json={"name": "Аудитор", "color": "#123456",
                                                            "permissions": ["run"], "scope": [p1["id"]]}, headers=H(head)).json()
    aud = next(r for r in t["roles"] if r["name"] == "Аудитор")
    assert aud["permissions"] == ["view", "run"] and aud["scope"] == [p1["id"]]   # run implies view
    u = register(client, "auditor")
    join(client, head, team, u, ["Аудитор"])
    seen = client.get(f"/api/teams/{team['id']}", headers=H(u)).json()["projects"]
    assert [p["title"] for p in seen] == ["Первый"]
    assert client.get(f"/api/projects/{p2['id']}", headers=H(u)).status_code == 404
    # the head edits the role: all projects now
    client.put(f"/api/roles/{aud['id']}", json={"name": "Аудитор", "permissions": ["view", "run"], "scope": None}, headers=H(head))
    assert len(client.get(f"/api/teams/{team['id']}", headers=H(u)).json()["projects"]) == 2


def test_only_head_hands_out_managing_rights(client):
    head = register(client, "head5")
    team = team_of(client, head)
    t = client.post(f"/api/teams/{team['id']}/roles", json={"name": "Кадровик", "permissions": ["manage_members"]},
                    headers=H(head)).json()
    mgr = register(client, "mgr")
    join(client, head, team, mgr, ["Кадровик"])
    # the manager invites and gives ordinary roles, but cannot create or give managing roles
    other = register(client, "other")
    r = client.post(f"/api/teams/{team['id']}/invites", json={"who": "other", "role_ids": [role_id(t, "Кадровик")]}, headers=H(mgr))
    assert r.status_code == 403
    r = client.post(f"/api/teams/{team['id']}/invites", json={"who": "other", "role_ids": [role_id(t, "Наблюдатель")]}, headers=H(mgr))
    assert r.status_code == 200
    assert client.post(f"/api/teams/{team['id']}/roles", json={"name": "X", "permissions": ["view"]}, headers=H(mgr)).status_code == 403
    assert client.put(f"/api/teams/{team['id']}/members/{mgr['user']['id']}/roles",
                      json={"role_ids": [role_id(t, "Кадровик"), role_id(t, "Разработчик")]}, headers=H(mgr)).status_code == 403
    assert client.delete(f"/api/teams/{team['id']}/members/{head['user']['id']}", headers=H(mgr)).status_code == 400
    del other


def test_saves_are_checked_against_roles(client):
    head = register(client, "head6")
    team = team_of(client, head)
    proj = client.post(f"/api/teams/{team['id']}/projects", json={"title": "Авария"}, headers=H(head)).json()
    base = xml()
    proj = client.put(f"/api/projects/{proj['id']}", json={"version": 1, "xml": base, "text": "описание"}, headers=H(head)).json()
    admin, dev, viewer = register(client, "admin1"), register(client, "dev1"), register(client, "viewer1")
    join(client, head, team, admin, ["Администратор"])
    join(client, head, team, dev, ["Разработчик"])
    join(client, head, team, viewer, ["Наблюдатель"])

    def rename(p):
        next(e for e in p["elements"] if e["id"] == "register")["name"] = "Зарегистрировать заявку срочно"
        next(e for e in p["elements"] if e["id"] == "register")["description"] = "Новое описание"

    def restructure(p):
        p["elements"].append({"id": "t_new", "type": "task", "name": "Новый шаг", "participant": "disp"})
        p["flows"].append({"from": "register", "to": "t_new"})
        p["flows"].append({"from": "t_new", "to": "end"})

    def add_code(p):
        e = next(e for e in p["elements"] if e["id"] == "register")
        e["type"], e["code"] = "service_task", "x = 1"

    def save(user, x, version, text="описание"):
        return client.put(f"/api/projects/{proj['id']}", json={"version": version, "xml": x, "text": text}, headers=H(user))

    v = proj["version"]
    assert save(viewer, xml(mutate=rename), v).status_code == 403
    r = save(admin, xml(mutate=rename), v)                     # administrator: names and descriptions
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    r = save(admin, xml(mutate=lambda p: (rename(p), restructure(p))), v)
    assert r.status_code == 403 and "структуру" in r.json()["detail"]
    r = save(admin, xml(mutate=rename), v, text="другое описание процесса")
    assert r.status_code == 200
    v = r.json()["version"]
    r = save(dev, xml(mutate=lambda p: (rename(p), restructure(p))), v)   # developer: structure
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    # code needs edit_code: developer has it, a custom role without it does not
    t = client.post(f"/api/teams/{team['id']}/roles", json={"name": "Схемщик", "permissions": ["edit_bpmn", "edit_content"]},
                    headers=H(head)).json()
    sch = register(client, "schemer")
    join(client, head, team, sch, ["Схемщик"])
    assert save(sch, xml(mutate=lambda p: (rename(p), restructure(p), add_code(p))), v, "другое описание процесса").status_code == 403
    assert save(dev, xml(mutate=lambda p: (rename(p), restructure(p), add_code(p))), v, "другое описание процесса").status_code == 200
    del t


def test_llm_and_run_endpoints_respect_project_roles(client):
    head = register(client, "head7")
    team = team_of(client, head)
    proj = client.post(f"/api/teams/{team['id']}/projects", json={"title": "П"}, headers=H(head)).json()
    admin = register(client, "admin2")
    join(client, head, team, admin, ["Администратор"])
    hdr = {**H(admin), "X-Project-Id": proj["id"]}
    r = client.post("/api/generate", json={"text": "Процесс согласования заявки"}, headers=hdr)
    assert r.status_code == 403 and "Генерация" in r.json()["detail"]
    r = client.post("/api/run/start", json={"xml": xml()}, headers=hdr)
    assert r.status_code == 403
    stranger = register(client, "stranger2")
    r = client.post("/api/generate", json={"text": "x" * 20}, headers={**H(stranger), "X-Project-Id": proj["id"]})
    assert r.status_code == 404


def test_version_conflict_and_delete_permission(client):
    head = register(client, "head8")
    team = team_of(client, head)
    proj = client.post(f"/api/teams/{team['id']}/projects", json={"title": "П"}, headers=H(head)).json()
    dev = register(client, "dev2")
    join(client, head, team, dev, ["Разработчик"])
    assert client.put(f"/api/projects/{proj['id']}", json={"version": 1, "xml": xml()}, headers=H(dev)).status_code == 200
    assert client.put(f"/api/projects/{proj['id']}", json={"version": 1, "xml": xml()}, headers=H(head)).status_code == 409
    assert client.delete(f"/api/projects/{proj['id']}", headers=H(dev)).status_code == 403
    assert client.delete(f"/api/projects/{proj['id']}", headers=H(head)).status_code == 200


def test_member_leaves_and_head_deletes_team(client):
    head = register(client, "head9")
    team = team_of(client, head)
    u = register(client, "leaver")
    join(client, head, team, u, ["Наблюдатель"])
    assert client.delete(f"/api/teams/{team['id']}/members/{u['user']['id']}", headers=H(u)).json()["left"]
    assert client.delete(f"/api/teams/{team['id']}", headers=H(u)).status_code == 404
    assert client.delete(f"/api/teams/{team['id']}", headers=H(head)).status_code == 200
    assert client.get("/api/auth/me", headers=H(head)).json()["teams"] == []
