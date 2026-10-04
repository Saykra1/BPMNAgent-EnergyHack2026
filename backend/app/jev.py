"""Optional, read-only Jev check of source quotes attached to BPMN steps."""
from __future__ import annotations

import httpx

from .config import Settings
from .insights import inspect_xml

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
TASK_KINDS = {"task", "userTask", "serviceTask", "scriptTask", "manualTask",
              "sendTask", "receiveTask", "businessRuleTask", "subProcess"}
MAX_LINKS = 24


class JevReviewError(Exception):
    """A safe, user-facing failure of the optional semantic check."""


def review_source_links(xml: str, text: str, settings: Settings,
                        transport: httpx.BaseTransport | None = None) -> dict:
    if not settings.jev_enabled:
        raise JevReviewError("Проверка Jev не включена в настройках сервера.")
    if not settings.llm_api_key or settings.llm_base_url.rstrip("/") != "https://openrouter.ai/api/v1":
        raise JevReviewError("Для Jev нужен ключ OpenRouter и LLM_BASE_URL=https://openrouter.ai/api/v1.")

    cards = [c for c in inspect_xml(xml, text)["cards"]
             if c["kind"] in TASK_KINDS and c.get("source_found") and c.get("source_quote") and c.get("name")]
    selected = cards[:MAX_LINKS]
    if not selected:
        return {"ok": True, "model": settings.jev_model, "checked": 0, "total": 0, "items": [],
                "note": "Нет шагов с точной цитатой из описания для смысловой сверки."}

    links = {f"link_{i}": {"quote": c["source_quote"][:1200], "step": c["name"][:250]}
             for i, c in enumerate(selected)}
    questions = {key: {"type": "noul", "instructions":
                 f"Does the source quote in links.{key}.quote describe substantially the same process action "
                 f"as links.{key}.step? Treat a paraphrase as a match, but do not invent unstated actions."}
                 for key in links}
    payload = {"model": settings.jev_model, "state": {"links": links}, "questions": questions}
    try:
        with httpx.Client(transport=transport, timeout=35) as client:
            response = client.post(JEV_URL, json=payload,
                                   headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        if response.status_code == 429:
            raise JevReviewError("Jev временно ограничен по частоте запросов. Повторите проверку позже.")
        if response.status_code == 402:
            raise JevReviewError("На балансе OpenRouter недостаточно средств для проверки Jev.")
        if response.status_code in (401, 403):
            raise JevReviewError("OpenRouter не разрешил вызов Jev. Проверьте ключ и доступ к модели.")
        response.raise_for_status()
        data = response.json()
        answers = data["answers"]
        items = []
        for i, card in enumerate(selected):
            probability = float(answers[f"link_{i}"]["noul"])
            if not 0 <= probability <= 1:
                raise ValueError("Некорректная вероятность")
            items.append({"id": card["id"], "name": card["name"], "quote": card["source_quote"],
                          "support": round(probability, 3)})
    except JevReviewError:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise JevReviewError("Смысловая проверка Jev не завершилась. Повторите попытку позже.") from exc

    return {"ok": True, "model": data.get("model", settings.jev_model), "checked": len(items),
            "total": len(cards), "items": sorted(items, key=lambda item: item["support"]),
            "note": "Это вероятностная подсказка, не доказательство корректности BPMN. Проверьте спорные связи вручную."}
