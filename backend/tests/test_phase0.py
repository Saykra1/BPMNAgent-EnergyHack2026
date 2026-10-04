"""Phase 0: LLM adapter, IR, deterministic builder, layout, validation, self-repair, integration."""
import json
import re
from pathlib import Path

import httpx
import pytest
from lxml import etree

from app.bpmn.xsd import validate_xsd
from app.config import Settings
from app.ir.lint import autofix, lint
from app.llm import prompts
from app.llm.client import (GeminiClient, LLMError, OpenAICompatibleClient, ScriptedClient, YandexClient,
                            clean_text, inline_refs, make_client)
from app.llm.plan import Plan, PlanError, ir_json_schema, parse_plan
from app.pipeline import Pipeline, build_ir

ROOT = Path(__file__).resolve().parents[2]
NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "di": "http://www.omg.org/spec/BPMN/20100524/DI",
      "dc": "http://www.omg.org/spec/DD/20100524/DC", "ddi": "http://www.omg.org/spec/DD/20100524/DI"}


def example_plan(name: str) -> dict:
    return json.loads((ROOT / "examples" / name / "plan.json").read_text("utf-8"))


def example_text(name: str) -> str:
    return (ROOT / "examples" / name / "input.txt").read_text("utf-8")


# =============================================================================== LLM adapter
@pytest.mark.parametrize("provider,cls,extra", [
    ("openai", OpenAICompatibleClient, {"llm_base_url": "http://localhost:8000/v1", "llm_model": "openai/gpt-oss-120b"}),
    ("vllm", OpenAICompatibleClient, {"llm_base_url": "http://gpu:8000/v1", "llm_model": "Qwen/Qwen3.6-35B-A3B"}),
    ("yandex", YandexClient, {"llm_api_key": "AQVN-key", "yandex_folder_id": "b1gfolder",
                              "llm_model": "gpt-oss-120b/latest"}),
    ("gemini", GeminiClient, {"llm_api_key": "k"}),
])
def test_model_is_switched_by_configuration_only(provider, cls, extra):
    client = make_client(Settings(llm_provider=provider, **extra))
    assert isinstance(client, cls)


def test_unknown_provider_and_onprem_guard():
    with pytest.raises(LLMError):
        make_client(Settings(llm_provider="nope"))
    with pytest.raises(LLMError, match="ONPREM"):
        make_client(Settings(llm_provider="yandex", llm_api_key="k", yandex_folder_id="f", llm_onprem_only=True))
    ok = make_client(Settings(llm_provider="openai", llm_base_url="http://127.0.0.1:11434/v1",
                              llm_model="qwen3", llm_onprem_only=True))
    assert isinstance(ok, OpenAICompatibleClient)


def test_yandex_model_uri_and_auth_headers():
    c = YandexClient(Settings(llm_provider="yandex", llm_api_key="AQVNkey", yandex_folder_id="b1g",
                              llm_model="qwen3-235b-a22b-fp8/latest"))
    assert c.model == "gpt://b1g/qwen3-235b-a22b-fp8/latest"
    assert c.url == "https://llm.api.cloud.yandex.net/v1/chat/completions"
    assert c.headers()["Authorization"] == "Api-Key AQVNkey" and c.headers()["OpenAI-Project"] == "b1g"
    iam = YandexClient(Settings(llm_provider="yandex", llm_api_key="t1.token", llm_model="gpt://b1g/yandexgpt/latest"))
    assert iam.headers()["Authorization"].startswith("Bearer ")


def _openai_client(handler, **kw):
    s = Settings(llm_provider="openai", llm_base_url="http://llm/v1", llm_model="m", **kw)
    return OpenAICompatibleClient(s, transport=httpx.MockTransport(handler), sleep=lambda _: None)


def _chat(text):
    return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 3}})


def test_openai_structured_output_and_graceful_downgrade():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, text="response_format json_schema is not supported")
        return _chat('<think>reasoning…</think>{"a": 1}')

    c = _openai_client(handler)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    r = c.complete([{"role": "system", "content": "s"}, {"role": "user", "content": "x"}], schema)
    assert r.text == '{"a": 1}' and r.mode == "json_object"
    assert bodies[0]["response_format"]["type"] == "json_schema"
    assert bodies[1]["response_format"]["type"] == "json_object"
    c.complete([{"role": "user", "content": "y"}], schema)          # remembered: no second json_schema try
    assert bodies[2]["response_format"]["type"] == "json_object"
    assert bodies[0]["messages"][0] == {"role": "system", "content": "s"}


def test_openai_retries_on_overload_and_reports_auth_errors():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else _chat("ok")

    assert _openai_client(handler).complete([{"role": "user", "content": "x"}]).text == "ok"
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        _openai_client(lambda r: httpx.Response(401, text="bad key")).complete([{"role": "user", "content": "x"}])


def test_clean_text_and_inline_refs():
    assert clean_text("<think>a\nb</think>\n{\"x\":1}") == '{"x":1}'
    schema = inline_refs(ir_json_schema())
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)


# =============================================================================== IR + prompts
def test_prompts_are_versioned_files_with_few_shot_examples():
    text = prompts.load("ir_extract.system")
    shots = re.findall(r"Ответ:\n(\{.*\})", text)
    assert len(shots) >= 2
    first = parse_plan(shots[0])
    assert len(first.participants) >= 3
    assert {e.type for e in first.elements} >= {"exclusive_gateway", "parallel_gateway"}
    assert prompts.used("ir_extract.system")["ir_extract.system"].startswith("v")


def test_ir_normalization_of_weak_model_output():
    messy = """Конечно! Вот JSON:
    {"title": "T", "participants": ["Клиент", "Менеджер"],
     "elements": [{"id": "a", "type": "userTask", "name": "Подать заявку", "participant": "Клиент"},
                  {"id": "g", "type": "XOR", "name": "Ок?", "participant": "p2"},
                  {"id": "b", "type": "Activity", "name": "Проверить", "participant": "p2", "documents": "Паспорт"},],
     "flows": [{"source": "start", "target": "a"}, {"from": "a", "to": "g"},
               {"from": "g", "to": "b", "condition": "Да"}, {"from": "g", "to": "end", "label": "Нет"},
               {"from": "b", "to": "end"},]}"""
    plan = parse_plan(messy)
    types = {e.id: e.type for e in plan.elements}
    assert types == {"a": "user_task", "g": "exclusive_gateway", "b": "task"}
    assert plan.elements[0].participant == "p1" and plan.elements[2].documents == ["Паспорт"]
    assert plan.flows[2].label == "Да"


def test_ir_reference_errors_are_reported_for_repair():
    with pytest.raises(PlanError, match="zzz"):
        parse_plan({"elements": [{"id": "a", "type": "task", "name": "A"}],
                    "flows": [{"from": "start", "to": "zzz"}]})
    with pytest.raises(PlanError, match="default"):
        parse_plan({"elements": [{"id": "a", "type": "task", "name": "A"}],
                    "flows": [{"from": "start", "to": "a", "default": True}, {"from": "a", "to": "end"}]})


# =============================================================================== builder + layout
def _parse(xml):
    return etree.fromstring(xml.encode())


def test_builder_emits_collaboration_lanes_default_flow_and_conditions():
    plan = parse_plan(example_plan("03_grid_connection"))
    plan, _ = autofix(plan, codes={"I_NO_DEFAULT"})
    r = build_ir(plan)
    assert r.ok, r.errors_for_llm()
    root = _parse(r.xml)
    assert root.find("b:collaboration/b:participant", NS) is not None
    assert len(root.findall(".//b:laneSet/b:lane", NS)) == 5
    assert root.findall(".//b:lane/b:flowNodeRef", NS)
    gw = root.find(".//b:exclusiveGateway[@id='gw_docs']", NS)     # IR ids are kept
    default = gw.get("default")
    assert default
    flow = root.find(f".//b:sequenceFlow[@id='{default}']", NS)
    assert flow.find("b:conditionExpression", NS) is None
    other = [f for f in root.findall(".//b:sequenceFlow[@sourceRef='gw_docs']", NS) if f.get("id") != default]
    assert all(f.find("b:conditionExpression", NS) is not None for f in other)
    assert validate_xsd(r.xml) == []


@pytest.mark.parametrize("name", ["01_credit", "02_eshop", "03_grid_connection", "04_hiring", "05_outage"])
def test_layout_has_complete_di_without_overlaps(name):
    r = build_ir(parse_plan(example_plan(name)))
    root = _parse(r.xml)
    plane = root.find(".//di:BPMNPlane", NS)
    shapes = {s.get("bpmnElement"): s.find("dc:Bounds", NS) for s in plane.findall("di:BPMNShape", NS)}
    nodes = [el.get("id") for el in root.iter() if el.get("id") and etree.QName(el).localname.endswith(
        ("Task", "task", "Gateway", "Event", "subProcess")) and etree.QName(el).localname != "messageEventDefinition"
        and not etree.QName(el).localname.endswith("Definition")]
    top_nodes = [n for n in nodes if n in shapes]
    assert len(top_nodes) >= 10
    boxes = []
    for n in top_nodes:
        b = shapes[n]
        boxes.append((n, float(b.get("x")), float(b.get("y")), float(b.get("width")), float(b.get("height"))))
    for i, (n1, x1, y1, w1, h1) in enumerate(boxes):            # no two flow nodes overlap
        for n2, x2, y2, w2, h2 in boxes[i + 1:]:
            assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, (n1, n2)
    for edge in plane.findall("di:BPMNEdge", NS):              # orthogonal routing
        pts = [(float(p.get("x")), float(p.get("y"))) for p in edge.findall("ddi:waypoint", NS)]
        assert len(pts) >= 2
        if "MessageFlow" in edge.get("bpmnElement") or "Association" in edge.get("bpmnElement"):
            continue
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            assert ax == bx or ay == by, edge.get("bpmnElement")
    lanes = {s.get("bpmnElement"): s.find("dc:Bounds", NS) for s in plane.findall("di:BPMNShape", NS)
             if s.get("bpmnElement", "").startswith("Lane")}
    for lane in root.findall(".//b:lane", NS):                 # every node is drawn inside its lane
        lb = lanes[lane.get("id")]
        ly, lh = float(lb.get("y")), float(lb.get("height"))
        for ref in lane.findall("b:flowNodeRef", NS):
            b = shapes.get(ref.text)
            if b is not None:
                assert ly <= float(b.get("y")) and float(b.get("y")) + float(b.get("height")) <= ly + lh


# =============================================================================== linter
def _plan(elements, flows, participants=None):
    return Plan.model_validate({"title": "T", "participants": participants or [], "elements": elements,
                                "flows": flows})


def test_linter_finds_typical_modelling_errors():
    p = _plan(
        [{"id": "a", "type": "task", "name": "Шаг A"},
         {"id": "x", "type": "exclusive_gateway", "name": "Ок?"},
         {"id": "b", "type": "task", "name": "Шаг B"},
         {"id": "c", "type": "task", "name": "Шаг B"},
         {"id": "j", "type": "parallel_gateway", "name": ""},
         {"id": "d", "type": "task", "name": "Тупик"},
         {"id": "lonely", "type": "task", "name": "Одинокий"}],
        [{"from": "start", "to": "a"}, {"from": "a", "to": "x"}, {"from": "x", "to": "b"},
         {"from": "x", "to": "c", "label": "Нет"}, {"from": "b", "to": "j"}, {"from": "c", "to": "j"},
         {"from": "j", "to": "d"}])
    codes = {(i.code, i.element) for i in lint(p)}
    assert ("E_DEADLOCK", "j") in codes
    assert ("E_DEAD_END", "d") in codes
    assert ("E_NO_INCOMING", "lonely") in codes
    assert ("W_NO_CONDITION", "x") in codes
    assert ("I_DUP_NAME", "b") in codes
    assert ("E_NO_END", None) in codes
    fixed, applied = autofix(p)
    left = {i.code for i in lint(fixed) if i.level == "error"}
    assert not left, left
    assert any("deadlock" in a for a in applied)
    assert build_ir(fixed).ok


def test_linter_and_split_without_join_is_fixed():
    p = _plan(
        [{"id": "s", "type": "parallel_gateway", "name": ""}, {"id": "a", "type": "task", "name": "A"},
         {"id": "b", "type": "task", "name": "B"}, {"id": "n", "type": "task", "name": "Дальше"}],
        [{"from": "start", "to": "s"}, {"from": "s", "to": "a"}, {"from": "s", "to": "b"},
         {"from": "a", "to": "n"}, {"from": "b", "to": "n"}, {"from": "n", "to": "end"}])
    assert "W_AND_NO_JOIN" in {i.code for i in lint(p)}
    fixed, _ = autofix(p)
    assert "W_AND_NO_JOIN" not in {i.code for i in lint(fixed)}
    assert any(e.type == "parallel_gateway" and e.id.startswith("gw_join") for e in fixed.elements)


def test_linter_questions_for_missing_performer_and_branch():
    p = _plan([{"id": "a", "type": "task", "name": "Проверить", "participant": "p1"},
               {"id": "b", "type": "task", "name": "Согласовать"},
               {"id": "x", "type": "exclusive_gateway", "name": "Согласовано?"}],
              [{"from": "start", "to": "a"}, {"from": "a", "to": "b"}, {"from": "b", "to": "x"},
               {"from": "x", "to": "end", "label": "Да"}],
              [{"id": "p1", "name": "Юрист"}])
    from app.ir.lint import questions
    qs = {q["code"]: q for q in questions(p)}
    assert "W_NO_PERFORMER" in qs and qs["W_NO_PERFORMER"]["blocking"]
    assert "W_SINGLE_BRANCH" in qs


# =============================================================================== integration (mocked LLM)
@pytest.mark.parametrize("name", ["01_credit", "03_grid_connection", "05_outage"])
def test_text_to_valid_bpmn_with_mocked_llm(name):
    llm = ScriptedClient([json.dumps(example_plan(name), ensure_ascii=False)])
    res = Pipeline(llm, None).generate(example_text(name))
    assert res.ok and res.xml, res.message or res.residual
    assert validate_xsd(res.xml) == []
    assert res.source in ("ir", "ir_autofix")
    assert llm.calls[0]["schema"] is not None                      # structured output requested
    assert len(llm.calls) == 1                                     # IR → deterministic builder, no code LLM


def test_self_repair_loop_sends_linter_errors_back_to_llm():
    bad = example_plan("05_outage")
    # make an unrecoverable-by-rules error: a cycle island not reachable from the start
    bad["elements"] += [{"id": "i1", "type": "task", "name": "Остров 1"}, {"id": "i2", "type": "task", "name": "Остров 2"}]
    bad["flows"] += [{"from": "i1", "to": "i2"}, {"from": "i2", "to": "i1"}]
    good = example_plan("05_outage")
    llm = ScriptedClient([json.dumps(bad, ensure_ascii=False), json.dumps(good, ensure_ascii=False)])
    res = Pipeline(llm, None, max_repairs=3).generate(example_text("05_outage"))
    assert res.ok and res.source == "ir_repaired"
    repair_prompt = llm.calls[1]["messages"][-1]["content"]
    assert "E_UNREACHABLE" in repair_prompt and "i1" in repair_prompt


def test_self_repair_gives_honest_report_when_exhausted():
    bad = example_plan("05_outage")
    bad["elements"] += [{"id": "i1", "type": "task", "name": "Остров 1"}, {"id": "i2", "type": "task", "name": "Остров 2"}]
    bad["flows"] += [{"from": "i1", "to": "i2"}, {"from": "i2", "to": "i1"}]
    answer = json.dumps(bad, ensure_ascii=False)
    llm = ScriptedClient([answer] * 4)
    res = Pipeline(llm, None, max_repairs=3).generate(example_text("05_outage"))
    assert not res.ok and res.xml                                  # draft is still delivered
    assert res.residual and any("E_UNREACHABLE" in r for r in res.residual)
    assert len(llm.calls) == 4                                     # 1 extraction + 3 repairs


def test_invalid_json_is_repaired_then_retried():
    good = json.dumps(example_plan("05_outage"), ensure_ascii=False)
    llm = ScriptedClient(["Извините, не могу.", "```json\n" + good[:-1] + ",}\n```"])
    res = Pipeline(llm, None).generate(example_text("05_outage"))
    assert res.ok and len(llm.calls) == 2


@pytest.mark.parametrize("text,msg", [("", "пустое"), ("   ", "пустое"), ("абв", "короткое"),
                                      ("12345 67890 !!!", "короткое")])
def test_bad_input_gets_a_clear_message(text, msg):
    res = Pipeline(ScriptedClient([]), None).generate(text)
    assert not res.ok and msg in res.message


def test_llm_failure_is_reported_not_raised():
    res = Pipeline(ScriptedClient([LLMError("Модель перегружена")]), None).generate(example_text("05_outage"))
    assert not res.ok and "перегружена" in res.message


def test_acceptance_grid_connection():
    """Acceptance: «заявка на техприсоединение» — 3+ participants, XOR and AND, valid XSD, readable layout."""
    llm = ScriptedClient([json.dumps(example_plan("03_grid_connection"), ensure_ascii=False)])
    res = Pipeline(llm, None).generate(example_text("03_grid_connection"))
    assert res.ok
    root = _parse(res.xml)
    assert len(root.findall(".//b:lane", NS)) >= 3
    assert root.findall(".//b:exclusiveGateway", NS) and root.findall(".//b:parallelGateway", NS)
    assert root.find(".//di:BPMNDiagram", NS) is not None
    assert validate_xsd(res.xml) == []
