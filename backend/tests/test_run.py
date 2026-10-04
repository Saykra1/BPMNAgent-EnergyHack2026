"""Process runs: block code interpreter (safety + semantics), gateway checks, human steps with
documents and attachments, parallel branches, subprocesses, manual choice, error → fix → retry."""
import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.ir.from_diagram import xml_to_plan
from app.llm.plan import parse_plan
from app.pipeline import build_ir
from app.run import api as run_api
from app.run.script import ScriptError, ScriptFail, eval_check, render_template, run_script


# ================================================================== interpreter
def test_script_semantics():
    v, logs = run_script(
        'итого = 0\nfor p in позиции:\n    итого += p["цена"] * p["кол"]\n'
        'скидка = 0.1 if итого > 1000 else 0\nк_оплате = round(итого * (1 - скидка), 2)\n'
        'дорогие = [p["имя"] for p in позиции if p["цена"] > 100]\nсрок = add_workdays("2026-10-02", 3)\n'
        'log(f"к оплате {к_оплате:.2f}")',
        {"позиции": [{"имя": "кабель", "цена": 600, "кол": 2}, {"имя": "болт", "цена": 5, "кол": 1}]})
    assert v["к_оплате"] == 1084.5 and v["дорогие"] == ["кабель"] and v["срок"] == "2026-10-07"
    assert logs == ["к оплате 1084.50"]
    assert eval_check('к_оплате > 1000 and "кабель" in дорогие', v) is True
    assert render_template("Счёт на {к_оплате} руб., до {срок}. {нет_такой}", v) == \
        "Счёт на 1 084.5 руб., до 2026-10-07. ‹нет_такой: не задано›"


@pytest.mark.parametrize("code", [
    "import os", "open('x')", "().__class__", "x = [].__class__.__mro__", "'{0.__class__}'.format(1)",
    "x = '%0999999d' % 1", "x = f'{1:>99999999}'", "while True:\n    pass", "x = 'a' * 10**9",
    "x = 2 ** 100000", "a = [1]\nwhile True:\n    a = a + a", "def f():\n    pass", "x = lambda: 1",
    "try:\n    pass\nexcept Exception:\n    pass", "exec('1')", "eval('1')", "getattr(1, 'real')",
    "__builtins__", "globals()", "x = 1\nfor i in range(10000):\n    x = x * 99999999",
])
def test_script_rejects_unsafe_or_runaway_code(code):
    with pytest.raises(ScriptError):
        run_script(code, {})


def test_fail_is_a_business_error():
    with pytest.raises(ScriptFail, match="лимит превышен"):
        run_script('if сумма > 10:\n    fail("лимит превышен")', {"сумма": 20})


# ================================================================== engine through the API
def plan_dict():
    return {
        "title": "Оплата счёта",
        "participants": [{"id": "acc", "name": "Бухгалтерия"}, {"id": "sys", "name": "Система"},
                         {"id": "boss", "name": "Руководитель"}],
        "performers": [{"id": "ivanova", "name": "Иванова А. А.", "role": "boss", "position": "директор"}],
        "elements": [
            {"id": "calc", "type": "service_task", "name": "Рассчитать сумму", "participant": "sys",
             "code": 'сумма = sum([p["цена"] * p["кол"] for p in позиции])\nlog(f"сумма {сумма}")',
             "report": "Сумма к оплате: {сумма} руб."},
            {"id": "gw", "type": "exclusive_gateway", "name": "Сумма больше лимита?", "participant": "sys"},
            {"id": "approve", "type": "user_task", "name": "Согласовать оплату", "participant": "boss",
             "performer": "ivanova", "fields": ["решение"], "report": "Укажите основание"},
            {"id": "gw_ok", "type": "exclusive_gateway", "name": "Согласовано?", "participant": "boss"},
            {"id": "merge", "type": "exclusive_gateway", "participant": "sys"},
            {"id": "split", "type": "parallel_gateway", "participant": "sys"},
            {"id": "pay", "type": "service_task", "name": "Провести платёж", "participant": "sys",
             "code": 'номер_платежа = "П-" + str(сумма)'},
            {"id": "notify", "type": "send_task", "name": "Уведомить поставщика", "participant": "acc"},
            {"id": "join", "type": "parallel_gateway", "participant": "sys"},
            {"id": "archive", "type": "manual_task", "name": "Подшить документы", "participant": "acc"},
            {"id": "end_reject", "type": "end_event", "name": "Оплата отклонена", "participant": "boss"},
        ],
        "flows": [
            {"from": "start", "to": "calc"}, {"from": "calc", "to": "gw"},
            {"from": "gw", "to": "approve", "label": "Да", "check": "сумма > лимит"},
            {"from": "gw", "to": "merge", "label": "Нет", "default": True},
            {"from": "approve", "to": "gw_ok"},
            {"from": "gw_ok", "to": "merge", "label": "Да", "check": "решение == 'да' or решение is True"},
            {"from": "gw_ok", "to": "end_reject", "label": "Нет", "default": True},
            {"from": "merge", "to": "split"}, {"from": "split", "to": "pay"}, {"from": "split", "to": "notify"},
            {"from": "pay", "to": "join"}, {"from": "notify", "to": "join"},
            {"from": "join", "to": "archive"}, {"from": "archive", "to": "end"},
        ],
    }


def xml_of(d):
    return build_ir(parse_plan(d)).xml


@pytest.fixture
def client(monkeypatch, tmp_path):
    s = Settings(runs_dir=tmp_path)
    monkeypatch.setattr(run_api, "_settings", lambda: s)
    return TestClient(main.app)


def test_code_and_checks_survive_bpmn_round_trip():
    back = xml_to_plan(xml_of(plan_dict()))
    calc = next(e for e in back.elements if e.id == "calc")
    assert calc.code.startswith("сумма = sum(") and calc.report == "Сумма к оплате: {сумма} руб."
    assert next(e for e in back.elements if e.id == "approve").fields == ["решение"]
    assert {f.check for f in back.flows if f.check} == {"сумма > лимит", "решение == 'да' or решение is True"}


def test_full_run_with_human_step_documents_and_archive(client):
    r = client.post("/api/run/start", json={"xml": xml_of(plan_dict()),
                                            "variables": {"позиции": [{"цена": 700, "кол": 2}], "лимит": 1000}}).json()
    assert r["status"] == "waiting" and r["variables"]["сумма"] == 1400
    [task] = r["waiting"]
    assert task["node"] == "approve" and task["fields"] == ["решение"] and "Иванова" in task["actor"]
    assert r["documents"][0]["text"] == "Сумма к оплате: 1400 руб." and r["documents"][0]["logs"] == ["сумма 1400"]
    rid = r["id"]
    # a person must fill the fields and produce a document or attach a file
    bad = client.post(f"/api/run/{rid}/complete", json={"token": task["token"], "values": {"решение": "да"}})
    assert bad.status_code == 400 and "документ" in bad.json()["detail"]
    bad = client.post(f"/api/run/{rid}/complete", json={"token": task["token"], "text": "ок"})
    assert bad.status_code == 400 and "решение" in bad.json()["detail"]
    r = client.post(f"/api/run/{rid}/complete", json={
        "token": task["token"], "values": {"решение": "да"}, "text": "Согласовано в пределах бюджета",
        "file_name": "../../скан.pdf", "file_base64": base64.b64encode(b"%PDF-1.4 test").decode()}).json()
    # parallel branches ran, the join waited for both, now the manual archive step waits
    assert r["status"] == "waiting" and [w["node"] for w in r["waiting"]] == ["archive"]
    assert r["variables"]["решение"] is True and r["variables"]["номер_платежа"] == "П-1400"
    human = next(d for d in r["documents"] if d["node"] == "approve")
    assert human["kind"] == "human" and human["attachment"]["name"] == "скан.pdf"
    r = client.post(f"/api/run/{rid}/complete", json={"token": r["waiting"][0]["token"], "text": "Подшито в папку 12"}).json()
    assert r["status"] == "done"
    assert [d["node"] for d in r["documents"]] == ["calc", "approve", "pay", "notify", "archive"]
    docx = client.get(f"/api/run/{rid}/documents/2")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    assert client.get(f"/api/run/{rid}/attachments/2").content == b"%PDF-1.4 test"
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/run/{rid}/archive").content))
    names = z.namelist()
    assert "журнал.md" in names and "процесс.bpmn" in names and len([n for n in names if n.startswith("документы/")]) == 5
    assert any(n.startswith("приложения/") and n.endswith("скан.pdf") for n in names)
    assert json.loads(z.read("данные.json"))["сумма"] == 1400


def test_default_branch_skips_approval(client):
    r = client.post("/api/run/start", json={"xml": xml_of(plan_dict()),
                                            "variables": {"позиции": [{"цена": 10, "кол": 1}], "лимит": 1000}}).json()
    assert [w["node"] for w in r["waiting"]] == ["archive"]
    assert any(h["kind"] == "chosen" and "Нет" in h["text"] for h in r["history"])


def test_error_stops_run_then_fix_and_retry(client):
    d = plan_dict()
    r = client.post("/api/run/start", json={"xml": xml_of(d), "variables": {"лимит": 1000}}).json()
    assert r["status"] == "error" and r["error"]["node"] == "calc" and "позиции" in r["error"]["message"]
    rid = r["id"]
    assert client.post(f"/api/run/{rid}/complete", json={"token": "t1", "text": "x"}).status_code == 400
    next(e for e in d["elements"] if e["id"] == "calc")["code"] = "сумма = 50"
    r = client.post(f"/api/run/{rid}/retry", json={"xml": xml_of(d)}).json()
    assert r["status"] == "waiting" and r["variables"]["сумма"] == 50 and r["waiting"][0]["node"] == "archive"


def test_gateway_without_checks_asks_to_choose(client):
    d = plan_dict()
    for f in d["flows"]:
        f.pop("check", None)
    r = client.post("/api/run/start", json={"xml": xml_of(d), "variables": {"позиции": [], "лимит": 1}}).json()
    [w] = r["waiting"]
    assert w["kind"] == "choice" and w["options"] == ["Да", "Нет"]
    r = client.post(f"/api/run/{r['id']}/choose", json={"token": w["token"], "options": [1]}).json()
    assert [x["node"] for x in r["waiting"]] == ["archive"]


def test_subprocess_runs_inner_steps(client):
    d = {"title": "П", "participants": [{"id": "a", "name": "Отдел"}],
         "elements": [{"id": "sub", "type": "subprocess", "name": "Проверка", "participant": "a"},
                      {"id": "s1", "type": "service_task", "name": "Шаг 1", "parent": "sub", "code": "x = 1"},
                      {"id": "s2", "type": "service_task", "name": "Шаг 2", "parent": "sub", "code": "x = x + 1"},
                      {"id": "after", "type": "service_task", "name": "После", "participant": "a", "code": "y = x * 10"}],
         "flows": [{"from": "start", "to": "sub"}, {"from": "s1", "to": "s2"}, {"from": "sub", "to": "after"},
                   {"from": "after", "to": "end"}]}
    r = client.post("/api/run/start", json={"xml": xml_of(d)}).json()
    assert r["status"] == "done" and r["variables"] == {"x": 2, "y": 20}


def test_try_code_endpoint(client):
    ok = client.post("/api/run/code", json={"code": "y = x * 2", "variables": {"x": 4}}).json()
    assert ok["ok"] and ok["changed"] == {"y": 8}
    assert client.post("/api/run/code", json={"code": "x >", "mode": "check"}).json()["ok"] is False
    assert client.post("/api/run/code", json={"code": "x > 3", "mode": "check", "variables": {"x": 4}}).json()["result"] is True
    assert client.get("/api/run/../../etc").status_code == 404
    assert client.get("/api/run/zzzzzzzzzzzz").status_code == 404
