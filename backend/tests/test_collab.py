"""Команды: приглашение, роли, изоляция и конфликт версий."""
import pytest
from fastapi.testclient import TestClient

from app.collab.routes import init_store
from app.main import app


@pytest.fixture
def client(tmp_path):
    init_store(tmp_path / "collab.db")
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _session(client: TestClient, name: str) -> dict:
    res = client.post("/api/session", json={"name": name})
    assert res.status_code == 200, res.text
    return res.json()


def _team(client: TestClient, token: str, name: str = "Сеть") -> dict:
    res = client.post("/api/teams", json={"name": name}, headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()


def _invite(client: TestClient, token: str, team_id: str, role: str = "editor") -> dict:
    res = client.post(f"/api/teams/{team_id}/invites", json={"role": role}, headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()


def _join(client: TestClient, invite_token: str, name: str) -> dict:
    res = client.post(f"/api/join/{invite_token}", json={"name": name})
    assert res.status_code == 200, res.text
    return res.json()


def test_join_save_viewer_forbidden_and_stranger_hidden(client: TestClient):
    owner = _session(client, "Ася")
    team = _team(client, owner["token"])
    editor_invite = _invite(client, owner["token"], team["id"], "editor")
    assert editor_invite["path"] == f"/join/{editor_invite['token']}"
    viewer_invite = _invite(client, owner["token"], team["id"], "viewer")

    editor = _join(client, editor_invite["token"], "Борис")
    viewer = _join(client, viewer_invite["token"], "Вера")
    stranger = _session(client, "Чужой")

    created = client.post(
        f"/api/teams/{team['id']}/projects",
        json={"title": "Авария"},
        headers=_auth(editor["user"]["token"]),
    )
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["version"] == 1

    saved = client.put(
        f"/api/projects/{project['id']}",
        json={"version": 1, "title": "Авария", "text": "Диспетчер гасит фидер", "xml": "<xml/>", "code": "c = 1", "plan": {"title": "Авария"}},
        headers=_auth(editor["user"]["token"]),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 2
    assert body["xml"] == "<xml/>"
    assert body["plan"]["title"] == "Авария"
    assert body["updated_by_name"] == "Борис"

    forbidden = client.put(
        f"/api/projects/{project['id']}",
        json={"version": 2, "text": "чужая правка", "xml": "<no/>"},
        headers=_auth(viewer["user"]["token"]),
    )
    assert forbidden.status_code == 403

    no_create = client.post(
        f"/api/teams/{team['id']}/projects",
        json={"title": "Нельзя"},
        headers=_auth(viewer["user"]["token"]),
    )
    assert no_create.status_code == 403

    hidden = client.get(f"/api/projects/{project['id']}", headers=_auth(stranger["token"]))
    assert hidden.status_code == 404
    assert "<xml" not in hidden.text
    hidden_team = client.get(f"/api/teams/{team['id']}", headers=_auth(stranger["token"]))
    assert hidden_team.status_code == 404

    loaded = client.get(f"/api/projects/{project['id']}", headers=_auth(viewer["user"]["token"]))
    assert loaded.status_code == 200
    assert loaded.json()["text"] == "Диспетчер гасит фидер"
    assert loaded.json()["role"] == "viewer"


def test_stale_version_is_rejected(client: TestClient):
    owner = _session(client, "Ася")
    team = _team(client, owner["token"])
    project = client.post(
        f"/api/teams/{team['id']}/projects",
        json={"title": "Заявка"},
        headers=_auth(owner["token"]),
    ).json()
    first = client.put(
        f"/api/projects/{project['id']}",
        json={"version": project["version"], "text": "первая", "xml": "<a/>"},
        headers=_auth(owner["token"]),
    )
    assert first.status_code == 200
    stale = client.put(
        f"/api/projects/{project['id']}",
        json={"version": project["version"], "text": "устарела", "xml": "<b/>"},
        headers=_auth(owner["token"]),
    )
    assert stale.status_code == 409
    fresh = client.get(f"/api/projects/{project['id']}", headers=_auth(owner["token"])).json()
    assert fresh["text"] == "первая"
    assert fresh["version"] == project["version"] + 1


def test_last_owner_cannot_be_demoted_or_removed(client: TestClient):
    owner = _session(client, "Ася")
    team = _team(client, owner["token"])
    editor = _join(client, _invite(client, owner["token"], team["id"])["token"], "Борис")

    demote = client.patch(
        f"/api/teams/{team['id']}/members/{owner['id']}",
        json={"role": "editor"},
        headers=_auth(owner["token"]),
    )
    assert demote.status_code == 409
    kick = client.delete(
        f"/api/teams/{team['id']}/members/{owner['id']}",
        headers=_auth(owner["token"]),
    )
    assert kick.status_code == 409

    editor_tries = client.patch(
        f"/api/teams/{team['id']}/members/{editor['user']['id']}",
        json={"role": "viewer"},
        headers=_auth(editor["user"]["token"]),
    )
    assert editor_tries.status_code == 403

    promoted = client.patch(
        f"/api/teams/{team['id']}/members/{editor['user']['id']}",
        json={"role": "owner"},
        headers=_auth(owner["token"]),
    )
    assert promoted.status_code == 200
    demoted = client.patch(
        f"/api/teams/{team['id']}/members/{owner['id']}",
        json={"role": "editor"},
        headers=_auth(editor["user"]["token"]),
    )
    assert demoted.status_code == 200
    assert any(m["user_id"] == owner["id"] and m["role"] == "editor" for m in demoted.json()["members"])


def test_invite_is_reusable_until_revoked_and_does_not_raise_role(client: TestClient):
    owner = _session(client, "Ася")
    team = _team(client, owner["token"])
    viewer_link = _invite(client, owner["token"], team["id"], "viewer")
    first = _join(client, viewer_link["token"], "Вера")
    second = _join(client, viewer_link["token"], "Глеб")
    assert first["role"] == "viewer"
    assert second["role"] == "viewer"

    editor_link = _invite(client, owner["token"], team["id"], "editor")
    again = client.post(
        f"/api/join/{editor_link['token']}",
        headers=_auth(first["user"]["token"]),
    )
    assert again.status_code == 200
    assert again.json()["role"] == "viewer"

    revoked = client.delete(f"/api/invites/{viewer_link['token']}", headers=_auth(owner["token"]))
    assert revoked.status_code == 200
    late = client.post(f"/api/join/{viewer_link['token']}", json={"name": "Поздний"})
    assert late.status_code == 404

    default = client.post(f"/api/teams/{team['id']}/invites", json={}, headers=_auth(owner["token"]))
    assert default.status_code == 200
    assert default.json()["role"] == "editor"

    detail = client.get(f"/api/teams/{team['id']}", headers=_auth(first["user"]["token"]))
    assert detail.json()["invites"] == []


def test_only_owner_deletes_project_and_join_page_is_the_app(client: TestClient):
    owner = _session(client, "Ася")
    team = _team(client, owner["token"])
    editor = _join(client, _invite(client, owner["token"], team["id"])["token"], "Борис")
    project = client.post(
        f"/api/teams/{team['id']}/projects",
        json={"title": "Ремонт"},
        headers=_auth(editor["user"]["token"]),
    ).json()
    denied = client.delete(f"/api/projects/{project['id']}", headers=_auth(editor["user"]["token"]))
    assert denied.status_code == 403
    deleted = client.delete(f"/api/projects/{project['id']}", headers=_auth(owner["token"]))
    assert deleted.status_code == 200
    missing = client.get(f"/api/projects/{project['id']}", headers=_auth(owner["token"]))
    assert missing.status_code == 404

    page = client.get("/join/some-token")
    assert page.status_code == 200
    assert "BPMN Agent" in page.text
    nameless = client.post("/api/join/missing", json={})
    assert nameless.status_code == 400
