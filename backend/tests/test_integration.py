"""Compatibility checks for the team and privacy modules integrated into stepa_1."""
from fastapi.testclient import TestClient

from app import main
from app.collab.routes import init_store
from app.config import Settings
from app.llm.client import LLMResponse
from app.privacy import PrivacyGuard
from app.privacy_client import ProtectedClient, protected_request
from app.conflict_review import review
from app.facts import FactChecker


def _user(client, login):
    response = client.post("/api/auth/register", json={"login": login, "email": f"{login}@example.org",
                                                      "name": login, "password": "long-password-123"})
    assert response.status_code == 200, response.text
    return response.json()


def _headers(user):
    return {"Authorization": f"Bearer {user['token']}"}


def test_team_invite_permissions_and_version_conflict(tmp_path, monkeypatch):
    init_store(tmp_path / "team.db")
    monkeypatch.setattr(main, "get_settings", lambda: Settings(runs_dir=tmp_path))
    client = TestClient(main.app)
    owner, guest = _user(client, "owner1"), _user(client, "guest1")
    team = client.post("/api/teams", json={"name": "Энергосеть"}, headers=_headers(owner)).json()
    project = client.post(f"/api/teams/{team['id']}/projects", json={"title": "Заявка"},
                          headers=_headers(owner)).json()
    assert client.get(f"/api/projects/{project['id']}", headers=_headers(guest)).status_code == 404
    detail = client.get(f"/api/teams/{team['id']}", headers=_headers(owner)).json()
    viewer = next(role for role in detail["roles"] if role["name"] == "Наблюдатель")
    invite = client.post(f"/api/teams/{team['id']}/invites",
                         json={"who": "guest1", "role_ids": [viewer["id"]]}, headers=_headers(owner))
    assert invite.status_code == 200, invite.text
    pending = client.get("/api/invites", headers=_headers(guest)).json()
    assert client.post(f"/api/invites/{pending[0]['id']}/accept", headers=_headers(guest)).status_code == 200
    assert client.get(f"/api/projects/{project['id']}", headers=_headers(guest)).status_code == 200
    blocked = client.put(f"/api/projects/{project['id']}", json={"version": 1, "text": "Новая версия"},
                         headers=_headers(guest))
    assert blocked.status_code == 403
    saved = client.put(f"/api/projects/{project['id']}", json={"version": 1, "text": "Новая версия"},
                       headers=_headers(owner))
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = client.put(f"/api/projects/{project['id']}", json={"version": 1, "text": "Ещё"},
                       headers=_headers(owner))
    assert stale.status_code == 409


def test_privacy_preview_and_llm_boundary():
    text = "Клиент Иван Петров звонит по номеру +7 912 345-67-89."
    guard = PrivacyGuard(text)
    assert "+7 912 345-67-89" not in guard.masked_text()

    class Echo:
        name = "echo"
        def __init__(self):
            self.sent = []
        def complete(self, system, messages, json_mode=False, max_tokens=8000):
            self.sent.append(messages)
            return LLMResponse(messages[-1]["content"], self.name, 0)

    model = Echo()
    with protected_request(text) as report:
        result = ProtectedClient(model).complete("system", [{"role": "user", "content": text}])
        assert report.report()["requests"] == 1
    assert "+7 912 345-67-89" not in model.sent[0][0]["content"]
    assert result.text == text


def test_fact_and_conflict_review_are_read_only():
    checker = FactChecker("Заявитель подаёт заявку в течение 5 дней.")
    assert checker.check("deadline", "7 дней")[0]["kind"] == "number"
    assert checker.check("deadline", "5 дней") == []
    text = "Договор подписывает только директор. Договор подписывает только бухгалтер."

    class Model:
        name = "fake"
        def complete(self, system, messages, json_mode=False, max_tokens=8000):
            return LLMResponse('{"conflicts":[{"topic":"Подпись",'
                               '"rule_a":"Договор подписывает только директор",'
                               '"rule_b":"Договор подписывает только бухгалтер"}]}', "fake", 0)

    result = review(text, ProtectedClient(Model()))
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["start_a"] == 0
