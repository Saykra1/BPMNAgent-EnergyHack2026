"""Performers directory, roles, block description and branch probability: stored in .bpmn, kept on
BPMN → IR round trip, used in SOP/RACI, and never sent to the model in clear text."""
import json
from pathlib import Path

from app.bpmn.xsd import validate_xsd
from app.ir.analytics import raci
from app.ir.diff import diff_plans
from app.ir.from_diagram import xml_to_plan
from app.ir.sop import build_sop, to_markdown
from app.llm.client import ScriptedClient
from app.llm.plan import PlanError, parse_plan
from app.pipeline import Pipeline, build_ir

import pytest

ROOT = Path(__file__).resolve().parents[2]


def plan_with_people():
    p = json.loads((ROOT / "examples" / "05_outage" / "plan.json").read_text("utf-8"))
    role = p["participants"][0]["id"]
    p["performers"] = [{"id": "ivanov", "name": "Пётр Иванов", "role": role, "position": "диспетчер",
                        "contacts": "+7 900 000-00-00"}]
    step = next(e for e in p["elements"] if e.get("participant") == role and e["type"].endswith("task"))
    step["performer"] = "ivanov"
    step["description"] = "Первая строка.\nВторая строка."
    step["sla_hours"] = 4
    return p, step["id"]


def test_round_trip_keeps_performers_description_and_probability():
    data, sid = plan_with_people()
    gw = next(f for f in data["flows"] if sum(1 for g in data["flows"] if g["from"] == f["from"]) > 1)
    gw["probability"] = 0.3
    plan = parse_plan(data)
    xml = build_ir(plan).xml
    assert validate_xsd(xml) == []
    assert "BPMN_AGENT_PERFORMERS" in xml
    back = xml_to_plan(xml)
    d = diff_plans(plan, back)
    assert not d["added"] and not d["removed"] and not d["changed"]
    step = next(e for e in back.elements if e.id == sid)
    assert step.performer == "ivanov" and step.description == "Первая строка.\nВторая строка."
    assert back.performers[0].name == "Пётр Иванов" and back.performers[0].position == "диспетчер"
    assert any(f.probability == 0.3 for f in back.flows)


def test_performer_references_are_checked():
    data, _ = plan_with_people()
    data["performers"][0]["role"] = "nope"
    with pytest.raises(PlanError):
        parse_plan(data)
    data, sid = plan_with_people()
    next(e for e in data["elements"] if e["id"] == sid)["performer"] = "ghost"
    with pytest.raises(PlanError):
        parse_plan(data)


def test_sop_and_raci_show_performer():
    data, sid = plan_with_people()
    plan = parse_plan(data)
    md = to_markdown(build_sop(plan))
    assert "Пётр Иванов, диспетчер" in md and "Первая строка." in md
    assert "| Пётр Иванов | диспетчер |" in md
    rows = raci(plan)["rows"]
    assert any(r.get("performer") == "Пётр Иванов" for r in rows)


def test_dialog_edit_never_sends_people_to_model(tmp_path):
    data, sid = plan_with_people()
    xml = build_ir(parse_plan(data)).xml
    answer = json.loads(json.dumps(data))
    for p in answer["performers"]:                       # the model echoes the anonymised directory
        p.update(name="Исполнитель 1", position="", contacts="")
    answer["elements"].append({"id": "t_new", "type": "task", "name": "Новый шаг",
                               "participant": data["participants"][0]["id"]})
    llm = ScriptedClient([json.dumps(answer, ensure_ascii=False)])
    res = Pipeline(llm, tmp_path, 3).refine("", "добавь шаг", xml=xml)
    sent = json.dumps([c["messages"] for c in llm.calls], ensure_ascii=False)
    assert "Иванов" not in sent and "900" not in sent and "диспетчер" not in sent
    assert res.ok
    back = xml_to_plan(res.xml)
    assert back.performers[0].name == "Пётр Иванов" and back.performers[0].contacts == "+7 900 000-00-00"
    assert next(e for e in back.elements if e.id == sid).performer == "ivanov"
