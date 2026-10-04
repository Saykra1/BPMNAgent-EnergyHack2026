from app.facts import FactChecker, text_numbers
from app.insights import inspect_xml
from app.pipeline import build

TEXT = ("Сетевая организация принимает заявку. Технический отдел готовит технические условия в течение десяти "
        "рабочих дней. Если мощность больше 15 кВт, главный инженер согласует условия. Инспектор оформляет акт "
        "о технологическом присоединении.")
CODE = """pool, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['Сетевая организация'], 'Сеть')
accept = DIAGRAM.add_task('Принять заявку', lanes[0])
DIAGRAM.set_details(accept, 'Сетевая организация принимает заявку', '', '', ['Заявка'])
prepare = DIAGRAM.add_task('Подготовить технические условия', lanes[0])
DIAGRAM.set_details(prepare, 'готовит технические условия в течение десяти рабочих дней', '', '15 рабочих дней по ПП № 861', ['Акт разграничения балансовой принадлежности'])
gw = DIAGRAM.add_exclusive_gateway('Мощность большая?', lanes[0])
agree = DIAGRAM.add_task('Согласовать условия', lanes[0])
DIAGRAM.set_details(agree, '', 'Не из описания: «5 дней».', '5 дней', [])
act = DIAGRAM.add_task('Оформить акт', lanes[0])
DIAGRAM.set_details(act, 'Инспектор оформляет акт', '', '', ['Акт о технологическом присоединении'])
extra = DIAGRAM.add_task('Уведомить заявителя', lanes[0])
DIAGRAM.add_link(ROOT_START_TASK_ID, accept)
DIAGRAM.add_link(accept, prepare)
DIAGRAM.add_link(prepare, gw)
DIAGRAM.add_link(gw, agree, 'Больше 150 кВт')
DIAGRAM.add_link(gw, act, 'Иначе')
DIAGRAM.add_link(agree, act)
DIAGRAM.add_link(act, extra)
DIAGRAM.add_link(extra, ROOT_END_TASK_ID)
"""


def inspect():
    result = build(CODE)
    assert result.ok, result.errors_for_llm()
    return inspect_xml(result.xml, TEXT)


def by_name(res):
    return {c["name"]: c for c in res["cards"]}


def test_one_card_per_value_names_every_missing_piece():
    fact, = FactChecker(TEXT).check("deadline", "20 рабочих дней по ПП № 861")
    assert fact["kind"] == "norm" and fact["details"] == ["20", "ПП № 861"]
    assert "Числа 20" in fact["reason"] and "«ПП № 861»" in fact["reason"]


def test_numbers_written_in_words_count_as_grounded():
    assert {"10", "15"} <= text_numbers(TEXT)
    assert text_numbers("в течение двадцати пяти дней") >= {"20", "5", "25"}
    assert FactChecker(TEXT).check("deadline", "10 рабочих дней") == []


def test_invented_deadline_norm_document_and_threshold_are_reported():
    facts = inspect()["facts"]
    found = {(f["field"], f["kind"], tuple(f["details"])) for f in facts if not f["acknowledged"]}
    assert ("deadline", "norm", ("ПП № 861",)) in found            # 15 is in the text (15 кВт)
    assert ("document", "document", ("Акт разграничения балансовой принадлежности",)) in found
    assert ("condition", "number", ("150",)) in found
    assert not any(f["value"] == "Акт о технологическом присоединении" for f in facts)
    condition = next(f for f in facts if f["field"] == "condition")
    assert condition["id"].startswith("Flow") and not condition["editable"]


def test_trust_levels_for_the_fact_check_mode():
    cards = by_name(inspect())
    assert cards["Принять заявку"]["trust"] == "grounded"
    assert cards["Подготовить технические условия"]["trust"] == "unbased"     # invented norm and document
    assert cards["Согласовать условия"]["trust"] == "assumption"              # acknowledged by the analyst
    assert cards["Уведомить заявителя"]["trust"] == "unbased"                 # no quote, no assumption
    assert cards["Мощность большая?"]["trust"] == "unbased"                   # 150 кВт is not in the text


def test_no_description_means_no_verdict():
    res = inspect_xml(build(CODE).xml, "")
    assert res["facts"] == [] and {c["trust"] for c in res["cards"]} == {"neutral"}
