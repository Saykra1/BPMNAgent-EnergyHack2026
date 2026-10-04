"""Fact check battery: facts rewritten from the text are kept, facts from the model's memory are flagged."""
import pytest

from app.facts import FactChecker

# (text, field, value on the diagram, must be flagged)
CASES = [
    ("Срок — пятнадцать рабочих дней.", "deadline", "15 рабочих дней", False),
    ("Срок — в течение двадцати пяти дней.", "deadline", "25 дней", False),
    ("Ответ в течение полутора месяцев.", "deadline", "1,5 месяца", False),
    ("На первом этапе инженер проверяет схему.", "name", "Этап 1: проверка схемы", False),
    ("Договор заключается до 15 марта 2025 года.", "deadline", "до 15.03.2025", False),
    ("Стоимость 100 000 руб.", "name", "Оплатить 100000 руб.", False),
    ("Напряжение 0,4 кВ.", "condition", "Напряжение 0.4 кВ", False),
    ("Срок от 10 до 15 дней.", "deadline", "10–15 дней", False),
    ("Работы с 9:00 до 18:00.", "deadline", "с 9:00 до 18:00", False),
    ("В 2025 году заявки принимаются онлайн.", "name", "Принять заявку 2025", False),
    ("Скидка 50% для льготников.", "condition", "Скидка 50%", False),
    ("Договор направляется в установленный срок.", "deadline", "15 рабочих дней", True),
    ("Договор направляется в установленный срок.", "deadline", "в соответствии с ПП № 861", True),
    ("Мощность до 15 кВт.", "condition", "Больше 150 кВт", True),
    ("Работы завершаются до 15 марта 2025 года.", "deadline", "до 01.04.2025", True),
    ("Инспектор оформляет акт.", "document", "Акт разграничения балансовой принадлежности", True),
    ("Инспектор оформляет акт о технологическом присоединении.", "document", "Акт о технологическом присоединении", False),
    ("Заявитель прикладывает план расположения устройств.", "document", "План расположения энергопринимающих устройств", False),
    ("Срок рассмотрения — 30 дней по 59-ФЗ.", "deadline", "30 дней по 59-ФЗ", False),
    ("Срок рассмотрения — 30 дней.", "deadline", "30 дней по 59-ФЗ", True),
]


@pytest.mark.parametrize("text,field,value,flagged", CASES, ids=[f"{c[2]}" for c in CASES])
def test_battery(text, field, value, flagged):
    assert bool(FactChecker(text).check(field, value)) == flagged
