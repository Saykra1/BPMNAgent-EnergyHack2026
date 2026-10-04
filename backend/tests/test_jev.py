"""The Jev review is optional and never changes the BPMN document."""
import json
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.jev import JevReviewError, review_audit_items, review_source_links


ROOT = Path(__file__).resolve().parents[2] / "examples" / "03_grid_connection"
XML = (ROOT / "result.bpmn").read_text("utf-8")
TEXT = (ROOT / "input.txt").read_text("utf-8")


def test_jev_review_uses_typed_questions_and_preserves_element_ids():
    captured = {}

    def reply(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        captured["body"] = body
        answers = {name: {"type": "noul", "noul": 0.42 if name == "link_0" else 0.91}
                   for name in body["questions"]}
        return httpx.Response(200, json={"model": "typesafe/jev-1.13", "answers": answers})

    settings = Settings(llm_provider="openai", llm_api_key="test-key",
                        llm_base_url="https://openrouter.ai/api/v1", jev_enabled=True)
    result = review_source_links(XML, TEXT, settings, httpx.MockTransport(reply))
    assert result["ok"] and result["checked"] > 1
    assert result["items"][0]["support"] == 0.42
    assert all(item["id"] and item["quote"] in TEXT for item in result["items"])
    assert captured["body"]["model"] == "typesafe/jev-1.13"
    assert all(question["type"] == "noul" for question in captured["body"]["questions"].values())
    assert XML not in json.dumps(captured["body"])


def test_jev_does_not_send_another_providers_key_to_openrouter():
    settings = Settings(llm_api_key="test-key", llm_base_url="https://api.mistral.ai/v1",
                        jev_enabled=True)
    with pytest.raises(JevReviewError, match="OpenRouter"):
        review_source_links(XML, TEXT, settings)


def test_jev_audit_scores_requirements_and_branches_without_xml():
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "xml" not in body["state"]
        assert body["state"]["gaps"]["gap_0"]["requirement"] == "Проверить документы"
        assert body["state"]["branches"]["branch_0"]["condition"] == "Да"
        return httpx.Response(200, json={"answers": {
            "gap_0": {"noul": 0.18}, "branch_0": {"noul": 0.92}}})

    settings = Settings(llm_api_key="test-key", llm_base_url="https://openrouter.ai/api/v1",
                        jev_enabled=True)
    result = review_audit_items(
        [{"id": "gap_12", "fragment": "Проверить документы", "candidates": ["Получить документы"]}],
        [{"id": "flow_1", "excerpt": "Если документы есть", "gateway": "Документы есть?",
          "condition": "Да", "destination": "Продолжить"}], settings, httpx.MockTransport(reply))
    assert result["gaps"] == [{"id": "gap_12", "support": 0.18}]
    assert result["branches"] == [{"id": "flow_1", "support": 0.92}]
