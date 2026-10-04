import json
from pathlib import Path

import pytest
from lxml import etree
from fastapi.testclient import TestClient

from app.bpmn.importer import bpmn_to_code
from app.bpmn.xsd import validate_xsd
from app.insights import inspect_xml
from app.llm.client import ScriptedClient
from app.llm.plan import parse_plan, compile_plan, PlanError
from app.pipeline import Pipeline, build
from app.runlog import RunLog
from app.sandbox import run_code
from app import main

TEXT = 'Заявитель предоставляет документы в течение 20 рабочих дней с получения уведомления.'


def sample_plan():
    return {
        'title': 'Документы для присоединения',
        'participants': [{'id': 'applicant', 'name': 'Заявитель'}],
        'elements': [{'id': 'supply', 'type': 'user_task', 'name': 'Предоставить документы',
                      'participant': 'applicant', 'source_quote': TEXT,
                      'deadline': '20 рабочих дней с получения уведомления', 'documents': ['Заявка']}],
        'flows': [{'from': 'start', 'to': 'supply'}, {'from': 'supply', 'to': 'end'}],
        'questions': ['Что делать при истечении срока?'],
    }


def test_details_survive_xml_import_and_are_xsd_valid():
    result = build(compile_plan(parse_plan(sample_plan())))
    assert result.ok and validate_xsd(result.xml) == []
    rebuilt = build(bpmn_to_code(result.xml))
    assert rebuilt.ok and validate_xsd(rebuilt.xml) == []
    cards = inspect_xml(rebuilt.xml, TEXT)['cards']
    card = next(c for c in cards if c['name'] == 'Предоставить документы')
    assert card['documents'] == ['Заявка']
    assert card['deadline'].startswith('20 рабочих')
    assert card['source_found']
    assert TEXT[card['source_start']:card['source_end']] == TEXT


def test_interview_then_compile_does_not_make_a_second_llm_call():
    llm = ScriptedClient([json.dumps(sample_plan(), ensure_ascii=False)])
    pipeline = Pipeline(llm)
    prepared = pipeline.prepare(TEXT)
    assert prepared['ok'] and prepared['plan']['questions']
    result = pipeline.from_plan(parse_plan(prepared['plan']), TEXT)
    assert result.ok and result.source == 'reviewed_plan'
    assert len(llm.calls) == 1


def test_invented_quote_is_not_presented_as_evidence():
    plan = sample_plan()
    plan['elements'][0]['source_quote'] = 'Закон требует 10 дней'
    pipeline = Pipeline(ScriptedClient([json.dumps(plan)]))
    prepared = pipeline.prepare(TEXT)
    element = prepared['plan']['elements'][0]
    assert element['source_quote'] == ''
    assert 'не подтверждено' in element['assumption']


def test_inspection_preserves_original_ids_and_does_not_fix_dangling_step():
    result = build(compile_plan(parse_plan(sample_plan())))
    root = etree.fromstring(result.xml.encode())
    ns = {'b': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}
    task = root.find('.//b:userTask', ns)
    task_id = task.get('id')
    for flow in root.findall('.//b:sequenceFlow', ns):
        if flow.get('sourceRef') == task_id:
            flow.getparent().remove(flow)
    report = inspect_xml(etree.tostring(root).decode(), TEXT)
    issue = next(i for i in report['issues'] if i['code'] == 'E_DANGLING_OUT')
    assert issue['elements'] == [task_id]
    assert issue['title'] == 'Процесс обрывается'


def test_deadline_exception_is_question_not_automatic_compliance_claim():
    xml = build(compile_plan(parse_plan(sample_plan()))).xml
    report = inspect_xml(xml, TEXT)
    check = next(c for c in report['energy_checks'] if c['id'] == 'deadline')
    assert check['status'] == 'review'
    assert 'кто' in check['question']
    no_source = inspect_xml(xml)['cards']
    assert all(not c['source_found'] for c in no_source)


def test_inspection_examples_have_no_spurious_errors():
    root = Path(__file__).resolve().parents[2]
    for example in (root / 'examples').glob('*/result.bpmn'):
        report = inspect_xml(example.read_text('utf-8'))
        assert not [i for i in report['issues'] if i['level'] == 'error'], example.name


def test_grid_connection_quotes_are_exact_and_export_is_valid():
    root = Path(__file__).resolve().parents[2] / 'examples' / '03_grid_connection'
    source = (root / 'input.txt').read_text('utf-8')
    plan = json.loads((root / 'plan.json').read_text('utf-8'))
    quotes = [el['source_quote'] for el in plan['elements'] if el.get('source_quote')]
    assert len(quotes) >= 10
    assert all(quote in source for quote in quotes)
    xml = (root / 'result.bpmn').read_text('utf-8')
    assert validate_xsd(xml) == []
    cards = inspect_xml(xml, source)['cards']
    assert sum(bool(c['source_found']) for c in cards) >= 10


def test_details_reject_non_strings():
    with pytest.raises(Exception, match='списком строк'):
        run_code("DIAGRAM.set_details(ROOT_START_TASK_ID, '', '', '', [123])")


def test_generation_without_model_returns_clear_error(monkeypatch):
    pipeline = Pipeline(None)
    pipeline.llm_error = 'Не задан LLM_API_KEY'
    monkeypatch.setattr(main, '_pipeline', lambda: pipeline)
    with TestClient(main.app) as client:
        for path, body in (
            ('/api/prepare', {'text': TEXT}),
            ('/api/generate', {'text': TEXT, 'mode': 'direct'}),
            ('/api/refine', {'instruction': 'Добавь проверку', 'code': 'x'}),
        ):
            response = client.post(path, json=body)
            assert response.status_code == 503
            assert 'LLM_API_KEY' in response.json()['detail']


def test_run_journal_is_optional_when_directory_is_unwritable(tmp_path):
    occupied = tmp_path / 'not-a-directory'
    occupied.write_text('file', encoding='utf-8')
    journal = RunLog(occupied, 'test')
    assert journal.dir is None
    journal.write('input.txt', TEXT)
    journal.finish(ok=True)


def test_reviewed_plan_ids_are_data_not_python_identifiers():
    plan = parse_plan({'elements': [
        {'id': 'x-y', 'type': 'task', 'name': 'Первый'},
        {'id': 'x_y', 'type': 'task', 'name': 'Второй'},
        {'id': 'class', 'type': 'task', 'name': 'Третий'}],
        'flows': [{'from': 'start', 'to': 'x-y'}, {'from': 'x-y', 'to': 'x_y'},
                  {'from': 'x_y', 'to': 'class'}, {'from': 'class', 'to': 'end'}]})
    result = build(compile_plan(plan))
    assert result.ok and result.stats['by_kind']['task'] == 3


def test_circular_subprocess_plan_is_rejected_before_compilation():
    with pytest.raises(PlanError, match='циклическая'):
        parse_plan({'elements': [{'id': 'a', 'type': 'subprocess', 'name': 'A', 'parent': 'b'},
                                {'id': 'b', 'type': 'subprocess', 'name': 'B', 'parent': 'a'}], 'flows': []})


def test_api_interview_compose_and_inspect(monkeypatch):
    pipeline = Pipeline(ScriptedClient([json.dumps(sample_plan())]))
    monkeypatch.setattr(main, '_pipeline', lambda: pipeline)
    with TestClient(main.app) as client:
        prepared = client.post('/api/prepare', json={'text': TEXT}).json()
        response = client.post('/api/from-plan', json={'plan': prepared['plan'], 'text': TEXT})
        assert response.status_code == 200 and response.json()['ok']
        inspected = client.post('/api/inspect', json={'xml': response.json()['xml'], 'text': TEXT})
        assert inspected.status_code == 200 and len(inspected.json()['energy_checks']) == 5
        assert client.post('/api/inspect', json={'xml': '<broken'}).status_code == 400
        assert client.post('/api/from-plan', json={'plan': {'elements': []}}).status_code == 400
