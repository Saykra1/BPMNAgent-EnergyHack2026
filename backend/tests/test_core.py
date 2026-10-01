import json
from pathlib import Path

import pytest

from app.bpmn.importer import bpmn_to_code
from app.bpmn.validator import normalize, validate
from app.bpmn.xsd import validate_xsd
from app.llm.client import ScriptedClient
from app.llm.plan import PlanError, compile_plan, parse_plan
from app.pipeline import Pipeline, build
from app.sandbox import SandboxError, run_code

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "examples").glob("*/plan.json"))

SIMPLE = """
pool, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['Клиент', 'Менеджер'], 'Компания')
a = DIAGRAM.add_user_task('Подать заявку', lanes[0])
b = DIAGRAM.add_task('Проверить заявку', lanes[1])
g = DIAGRAM.add_exclusive_gateway('Заявка корректна?', lanes[1])
r = DIAGRAM.add_end_event('Заявка отклонена', lanes[1])
DIAGRAM.add_link(ROOT_START_TASK_ID, a)
DIAGRAM.add_link(a, b)
DIAGRAM.add_link(b, g)
DIAGRAM.add_link(g, ROOT_END_TASK_ID, 'Да')
DIAGRAM.add_link(g, r, 'Нет')
"""


# ----------------------------------------------------------------- sandbox
@pytest.mark.parametrize("code", [
    "import os",
    "__import__('os').system('ls')",
    "x = DIAGRAM.__class__",
    "DIAGRAM.nodes.clear()",
    "for i in range(10**9):\n    pass",
    "x = open('/etc/passwd')",
    "x = DIAGRAM.add_task(f'{1}', ROOT_PROCESS_ID)",
    "def f():\n    pass",
    "x = [DIAGRAM.add_task('a', ROOT_PROCESS_ID) for _ in range(3)]",
    "x = DIAGRAM.add_task('a', ROOT_PROCESS_ID).upper()",
    "ROOT_PROCESS_ID = 'x'",
])
def test_sandbox_rejects_unsafe(code):
    with pytest.raises(SandboxError):
        run_code(code)


def test_sandbox_reports_line_of_runtime_error():
    with pytest.raises(SandboxError) as e:
        run_code("a = DIAGRAM.add_task('A', ROOT_PROCESS_ID)\nDIAGRAM.add_link(a, missing)")
    assert e.value.line == 2


def test_sandbox_api_misuse_message():
    with pytest.raises(SandboxError) as e:
        run_code("p, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['A', 'B'])\nt = DIAGRAM.add_task('X', lanes)")
    assert "lanes[0]" in e.value.message


# ----------------------------------------------------------------- build
def test_simple_build_is_xsd_valid():
    r = build(SIMPLE)
    assert r.ok, r.errors_for_llm()
    assert validate_xsd(r.xml) == []
    assert r.stats["lanes"] == 2


def test_validator_detects_dangling_and_deadlock():
    code = """
a = DIAGRAM.add_task('A', ROOT_PROCESS_ID)
g = DIAGRAM.add_exclusive_gateway('?', ROOT_PROCESS_ID)
b = DIAGRAM.add_task('B', ROOT_PROCESS_ID)
c = DIAGRAM.add_task('C', ROOT_PROCESS_ID)
j = DIAGRAM.add_parallel_gateway('', ROOT_PROCESS_ID)
d = DIAGRAM.add_task('D', ROOT_PROCESS_ID)
DIAGRAM.add_link(ROOT_START_TASK_ID, a)
DIAGRAM.add_link(a, g)
DIAGRAM.add_link(g, b, 'x')
DIAGRAM.add_link(g, c, 'y')
DIAGRAM.add_link(b, j)
DIAGRAM.add_link(c, j)
DIAGRAM.add_link(j, ROOT_END_TASK_ID)
"""
    d = run_code(code).diagram
    normalize(d)
    codes = {i.code for i in validate(d)}
    assert "E_DEADLOCK" in codes
    assert "E_DANGLING_IN" in codes and "E_DANGLING_OUT" in codes


def test_cross_pool_sequence_flow_becomes_message_flow():
    code = """
p1, l1 = DIAGRAM.add_pool(ROOT_PROCESS_ID, [], 'Компания')
p2, l2 = DIAGRAM.add_pool(ROOT_PROCESS_ID, [], 'Банк')
a = DIAGRAM.add_task('Отправить запрос', p1)
s = DIAGRAM.add_start_event('', p2, 'message')
b = DIAGRAM.add_task('Обработать запрос', p2)
DIAGRAM.add_link(ROOT_START_TASK_ID, a)
DIAGRAM.add_link(a, ROOT_END_TASK_ID)
DIAGRAM.add_link(a, s)
DIAGRAM.add_link(s, b)
"""
    r = build(code)
    assert r.ok, r.errors_for_llm()
    assert any(i.code == "F_SEQ_TO_MSG" for i in r.issues)
    assert r.stats["message_flows"] == 1


def test_subprocess_gets_implicit_start_end():
    code = """
sp = DIAGRAM.create_subprocess('Подготовка', ROOT_PROCESS_ID)
x = DIAGRAM.add_task('Шаг 1', sp)
y = DIAGRAM.add_task('Шаг 2', sp)
DIAGRAM.add_link(x, y)
DIAGRAM.add_link(ROOT_START_TASK_ID, sp)
DIAGRAM.add_link(sp, ROOT_END_TASK_ID)
"""
    r = build(code)
    assert r.ok, r.errors_for_llm()
    assert r.xml.count("<bpmndi:BPMNDiagram") == 2   # drill-down plane for the subprocess


# ----------------------------------------------------------------- plans / examples
@pytest.mark.parametrize("plan_path", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_examples_compile_and_validate(plan_path):
    plan = parse_plan(plan_path.read_text("utf-8"))
    r = build(compile_plan(plan), plan.title)
    assert r.ok, r.errors_for_llm()
    assert not [i for i in r.issues if i.level == "error"]


@pytest.mark.parametrize("plan_path", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_import_roundtrip(plan_path):
    plan = parse_plan(plan_path.read_text("utf-8"))
    r1 = build(compile_plan(plan), plan.title)
    r2 = build(bpmn_to_code(r1.xml))
    assert r2.ok, r2.errors_for_llm()
    for k in ("sequence_flows", "message_flows", "pools", "lanes"):
        assert r1.stats[k] == r2.stats[k], k


def test_plan_errors_are_reported():
    with pytest.raises(PlanError) as e:
        parse_plan({"elements": [{"id": "a", "type": "task", "name": "A"}],
                    "flows": [{"from": "start", "to": "zzz"}]})
    assert "zzz" in str(e.value)


# ----------------------------------------------------------------- pipeline with scripted LLM
PLAN = json.dumps({
    "title": "Тест", "participants": [{"id": "c", "name": "Клиент"}, {"id": "m", "name": "Менеджер"}],
    "elements": [{"id": "a", "type": "task", "name": "Подать заявку", "participant": "c"}],
    "flows": [{"from": "start", "to": "a"}, {"from": "a", "to": "end"}],
}, ensure_ascii=False)

BROKEN = """
pool, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['Клиент', 'Менеджер'], 'Компания')
a = DIAGRAM.add_task('Подать заявку', lanes[0])
DIAGRAM.add_link(ROOT_START_TASK_ID, a)
"""


def test_pipeline_repair_loop():
    llm = ScriptedClient(["Вот план:\n```json\n" + PLAN + "\n```", BROKEN, "```python\n" + SIMPLE + "```"])
    res = Pipeline(llm, None, max_repairs=3).generate("Клиент подаёт заявку, менеджер проверяет")
    assert res.ok and res.source == "llm_repaired"
    assert "E_DANGLING_OUT" in llm.calls[2]["messages"][-1]["content"]


def test_pipeline_plan_repair_and_compiler_fallback():
    bad_plan = '{"elements": [], "flows": []}'
    llm = ScriptedClient([bad_plan, PLAN] + [BROKEN] * 4)
    res = Pipeline(llm, None, max_repairs=3).generate("Клиент подаёт заявку")
    assert res.ok and res.source == "plan_compiler"
    assert [a["stage"] for a in res.attempts][:2] == ["plan", "plan"]


def test_refine_summary():
    llm = ScriptedClient(["# Изменения: добавлен шаг\n" + SIMPLE])
    res = Pipeline(llm, None).refine("text", SIMPLE, "добавь шаг")
    assert res.ok and res.summary == "добавлен шаг"


# ----------------------------------------------------------------- gemini client (mocked HTTP)
def test_gemini_client_request_and_parse():
    import httpx

    from app.config import Settings
    from app.llm.client import GeminiClient

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "думаю", "thought": True}, {"text": '{"ok": 1}'}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    s = Settings(llm_provider="gemini", llm_api_key="k", llm_model="gemini-2.5-flash",
                 llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai")
    c = GeminiClient(s, transport=httpx.MockTransport(handler))
    r = c.complete("sys", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                           {"role": "user", "content": "c"}], json_mode=True)
    assert r.text == '{"ok": 1}'
    assert seen["url"].endswith("/v1beta/models/gemini-2.5-flash:generateContent")
    assert seen["key"] == "k"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_client_error_message():
    import httpx

    from app.config import Settings
    from app.llm.client import GeminiClient, LLMError

    def handler(request):
        return httpx.Response(400, json={"error": {"message": "API key not valid."}})

    c = GeminiClient(Settings(llm_provider="gemini", llm_api_key="bad"), transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as e:
        c.complete("s", [{"role": "user", "content": "x"}])
    assert "LLM_API_KEY" in str(e.value)


def test_env_file_parsing_windows_variants(tmp_path):
    from app.config import read_env_file

    content = ("# Провайдер LLM: используем нативный API Gemini\nLLM_PROVIDER=gemini\n"
               "LLM_API_KEY=\"AQ.abc\"  \nLLM_MODEL=gemini-2.5-flash # комментарий\nLLM_EFFORT=low\n")
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        f = tmp_path / f"env_{enc}"
        f.write_bytes(content.encode(enc))
        v = read_env_file(f)
        assert v["llm_provider"] == "gemini" and v["llm_api_key"] == "AQ.abc", enc
        assert v["llm_model"] == "gemini-2.5-flash"
