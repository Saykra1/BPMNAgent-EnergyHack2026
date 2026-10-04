"""Model mistakes that used to break generation and are now absorbed deterministically."""
import pytest

from app.llm.plan import compile_plan, parse_plan
from app.pipeline import build
from app.sandbox import SandboxError, run_code


def test_external_participant_joined_by_control_flows_becomes_a_lane():
    plan = parse_plan({
        "title": "Заявка",
        "participants": [{"id": "app", "name": "Заявитель", "external": True},
                         {"id": "net", "name": "Сетевая организация"}],
        "elements": [{"id": "apply", "type": "user_task", "name": "Подать заявку", "participant": "app"},
                     {"id": "check", "type": "task", "name": "Проверить заявку", "participant": "net"}],
        "flows": [{"from": "start", "to": "apply"}, {"from": "apply", "to": "check"}, {"from": "check", "to": "end"}],
    })
    assert not plan.participants[0].external
    assert any("«Заявитель» показан дорожкой" in a for a in plan.assumptions)
    assert build(compile_plan(plan), plan.title).ok


def test_external_organisation_with_messages_stays_a_pool():
    plan = parse_plan({
        "participants": [{"id": "net", "name": "Сеть"}, {"id": "bank", "name": "Банк", "external": True}],
        "elements": [{"id": "pay", "type": "send_task", "name": "Запросить оплату", "participant": "net"}],
        "flows": [{"from": "start", "to": "pay"}, {"from": "pay", "to": "end"}],
        "message_flows": [{"from": "pay", "to": "bank", "label": "Счёт"}],
    })
    assert plan.participants[1].external and plan.assumptions == []


def test_lane_aliases_are_allowed_in_the_sandbox():
    code = ("pool, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['А', 'Б'], 'Орг')\nlane_a = lanes[0]\na, b = lanes\n"
            "t = DIAGRAM.add_task('Шаг', lane_a)\nDIAGRAM.add_link(ROOT_START_TASK_ID, t)\nDIAGRAM.add_link(t, ROOT_END_TASK_ID)\n")
    assert build(code).ok


@pytest.mark.parametrize("line", ["x = open('/etc/passwd')", "x = DIAGRAM.nodes", "x = lanes.__class__",
                                  "x = [i for i in lanes]", "x = 1 + 2", "x = lanes[len(lanes)]"])
def test_aliases_do_not_open_the_sandbox(line):
    with pytest.raises(SandboxError):
        run_code("pool, lanes = DIAGRAM.add_pool(ROOT_PROCESS_ID, ['А'], 'Орг')\n" + line)
