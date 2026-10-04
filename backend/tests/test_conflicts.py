import json

from fastapi.testclient import TestClient

from app import main
from app.bpmn.importer import bpmn_to_code
from app.llm.client import ScriptedClient
from app.llm.plan import compile_plan, parse_plan
from app.pipeline import Pipeline, build

RULE_A = 'Договор направляется заявителю в течение 10 рабочих дней.'
RULE_B = 'Договор направляется заявителю не позднее 30 дней с даты заявки.'
TEXT = 'Заявитель подаёт заявку. ' + RULE_A + ' Заявитель подписывает договор. ' + RULE_B


def plan(conflicts=()):
    return {
        'title': 'Договор присоединения',
        'participants': [{'id': 'net', 'name': 'Сетевая организация'}, {'id': 'app', 'name': 'Заявитель'}],
        'elements': [
            {'id': 'apply', 'type': 'user_task', 'name': 'Подать заявку', 'participant': 'app',
             'source_quote': 'Заявитель подаёт заявку.'},
            {'id': 'send', 'type': 'send_task', 'name': 'Направить договор', 'participant': 'net',
             'source_quote': 'Договор направляется заявителю в течение 10 рабочих дней'},
            {'id': 'sign', 'type': 'user_task', 'name': 'Подписать договор', 'participant': 'app',
             'source_quote': 'Заявитель подписывает договор.'},
        ],
        'flows': [{'from': 'start', 'to': 'apply'}, {'from': 'apply', 'to': 'send'},
                  {'from': 'send', 'to': 'sign'}, {'from': 'sign', 'to': 'end'}],
        'conflicts': list(conflicts),
    }


CONFLICT = {'topic': 'Срок направления договора', 'rule_a': RULE_A, 'rule_b': RULE_B}


def test_contradiction_stops_before_drawing():
    llm = ScriptedClient([json.dumps(plan([CONFLICT]), ensure_ascii=False)])
    res = Pipeline(llm).generate(TEXT)
    assert res.source == 'conflict' and res.xml is None and not res.ok
    assert res.conflicts == [{'id': 'c1', **CONFLICT}]
    assert len(llm.calls) == 1            # no code generation was attempted


def test_the_narrow_check_catches_what_the_planner_missed():
    llm = ScriptedClient([json.dumps(plan(), ensure_ascii=False)], conflicts=json.dumps({"conflicts": [CONFLICT]}, ensure_ascii=False))
    res = Pipeline(llm).generate(TEXT)
    assert res.source == 'conflict' and res.conflicts == [{'id': 'c1', **CONFLICT}]
    assert len(llm.conflict_calls) == 1 and len(llm.calls) == 1        # no code generation was attempted


def test_both_checks_reporting_one_pair_ask_once():
    llm = ScriptedClient([json.dumps(plan([CONFLICT]), ensure_ascii=False)],
                         conflicts=json.dumps({"conflicts": [{**CONFLICT, 'rule_a': RULE_B, 'rule_b': RULE_A}]}, ensure_ascii=False))
    assert len(Pipeline(llm).generate(TEXT).conflicts) == 1


def test_a_broken_check_answer_does_not_break_generation():
    llm = ScriptedClient([json.dumps(plan(), ensure_ascii=False), compile_plan(parse_plan(plan()))], conflicts="не JSON")
    assert Pipeline(llm).generate(TEXT).ok


def test_paraphrased_contradiction_does_not_block():
    fake = {'topic': 'Срок', 'rule_a': 'Договор отправляют за 10 дней', 'rule_b': RULE_B}
    same = {'topic': 'Срок', 'rule_a': RULE_A, 'rule_b': RULE_A[:-1]}
    pipeline = Pipeline(ScriptedClient([json.dumps(plan([fake, same]), ensure_ascii=False)]))
    prepared = pipeline.prepare(TEXT)
    assert prepared['ok'] and prepared['conflicts'] == []


def test_decision_reaches_planner_and_becomes_a_footnote():
    llm = ScriptedClient([json.dumps(plan([CONFLICT]), ensure_ascii=False), compile_plan(parse_plan(plan()))])
    decision = [{**CONFLICT, 'chosen': 'b'}]
    res = Pipeline(llm).generate(TEXT, resolutions=decision)
    assert res.ok and res.conflicts == []                  # the decided pair is not asked again
    assert 'Старшее правило: «' + RULE_B in llm.calls[0]['messages'][0]['content']
    assert res.resolutions == decision
    assert 'textAnnotation' in res.xml and 'Принято: «Договор направляется заявителю не позднее 30' in res.xml
    # The footnote survives a manual edit round-trip (XML -> code -> XML).
    assert 'Противоречие в описании' in build(bpmn_to_code(res.xml)).xml


def test_footnote_is_attached_to_the_grounded_step():
    decision = [{**CONFLICT, 'chosen': 'a'}]
    res = Pipeline(None).from_plan(parse_plan(plan()), TEXT, decision)
    assert res.ok
    code = res.code.splitlines()
    send_var = next(line.split(' = ')[0] for line in code if "'Направить договор'" in line)
    assert any(line.startswith('DIAGRAM.add_annotation(') and line.endswith(f', {send_var})') for line in code)


def test_both_rules_hold_adds_no_footnote():
    res = Pipeline(None).from_plan(parse_plan(plan()), TEXT, [{**CONFLICT, 'chosen': 'both'}])
    assert res.ok and 'textAnnotation' not in res.xml


def test_api_validates_resolution_choice():
    client = TestClient(main.app)
    bad = client.post('/api/from-plan', json={'plan': plan(), 'text': TEXT,
                                              'resolutions': [{**CONFLICT, 'chosen': 'c'}]})
    assert bad.status_code == 422
    ok = client.post('/api/from-plan', json={'plan': plan(), 'text': TEXT,
                                             'resolutions': [{**CONFLICT, 'chosen': 'a'}]})
    assert ok.status_code == 200 and 'textAnnotation' in ok.json()['xml']


def test_quote_with_other_whitespace_is_mapped_to_the_exact_fragment():
    text = TEXT.replace('в течение 10', 'в течение\n10')
    loose = {'topic': 'Срок', 'rule_a': '«' + RULE_A + '»', 'rule_b': '  ' + RULE_B.replace(' ', '  ')}
    prepared = Pipeline(ScriptedClient([json.dumps(plan([loose]), ensure_ascii=False)])).prepare(text)
    c, = prepared['conflicts']
    assert c['rule_a'] in text and c['rule_a'].startswith('Договор') and '\n' in c['rule_a']
    assert c['rule_b'] == RULE_B
