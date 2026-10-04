"""Optional, read-only Jev check of source quotes attached to BPMN steps."""
from __future__ import annotations

import httpx

from .config import Settings
from .insights import inspect_xml
from .privacy import PrivacyGuard

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
TASK_KINDS = {"task", "userTask", "serviceTask", "scriptTask", "manualTask",
              "sendTask", "receiveTask", "businessRuleTask", "subProcess"}
MAX_LINKS = 24


class JevReviewError(Exception):
    """A safe, user-facing failure of the optional semantic check."""


def review_source_links(xml: str, text: str, settings: Settings,
                        transport: httpx.BaseTransport | None = None, privacy: dict | None = None) -> dict:
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
    guard = PrivacyGuard(text, **(privacy or {}))
    payload = {"model": settings.jev_model, "state": _mask_state({"links": links}, guard), "questions": questions}
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


def review_audit_items(gaps: list[dict], branches: list[dict], settings: Settings,
                       transport: httpx.BaseTransport | None = None, privacy: dict | None = None) -> dict:
    """Assess bounded, user-visible audit candidates; never mutate the BPMN graph."""
    if not settings.jev_enabled:
        raise JevReviewError("Проверка Jev не включена в настройках сервера.")
    if not settings.llm_api_key or settings.llm_base_url.rstrip("/") != "https://openrouter.ai/api/v1":
        raise JevReviewError("Для Jev нужен ключ OpenRouter и LLM_BASE_URL=https://openrouter.ai/api/v1.")

    selected_gaps = gaps[:12]
    selected_branches = branches[:12]
    if not selected_gaps and not selected_branches:
        return {"ok": True, "gaps": [], "branches": [], "note": "Нет спорных мест для проверки."}

    state = {"gaps": {}, "branches": {}}
    questions = {}
    for i, item in enumerate(selected_gaps):
        key = f"gap_{i}"
        state["gaps"][key] = {
            "requirement": str(item.get("fragment", ""))[:1000],
            "closest_bpmn_steps": [str(v)[:180] for v in item.get("candidates", [])[:4]],
        }
        questions[key] = {"type": "noul", "instructions":
                          f"Is the process requirement in gaps.{key}.requirement substantively represented "
                          f"by the listed BPMN steps? A similar topic without the required action or condition is not enough."}
    for i, item in enumerate(selected_branches):
        key = f"branch_{i}"
        state["branches"][key] = {
            "source_excerpt": str(item.get("excerpt", ""))[:1200],
            "gateway": str(item.get("gateway", ""))[:180],
            "condition": str(item.get("condition", ""))[:180],
            "destination": str(item.get("destination", ""))[:180],
        }
        questions[key] = {"type": "noul", "instructions":
                          f"Does branches.{key}.source_excerpt support taking this exact BPMN branch "
                          f"from its gateway to its destination under the named condition? Do not infer missing rules."}
    guard = PrivacyGuard("\n".join(str(value) for item in selected_gaps + selected_branches
                                   for value in item.values()), **(privacy or {}))
    state = _mask_state(state, guard)
    try:
        with httpx.Client(transport=transport, timeout=35) as client:
            response = client.post(JEV_URL, json={"model": settings.jev_model, "state": state,
                                                  "questions": questions},
                                   headers={"Authorization": f"Bearer {settings.llm_api_key}"})
        if response.status_code == 429:
            raise JevReviewError("Jev временно ограничен по частоте запросов. Повторите проверку позже.")
        if response.status_code == 402:
            raise JevReviewError("На балансе OpenRouter недостаточно средств для проверки Jev.")
        if response.status_code in (401, 403):
            raise JevReviewError("OpenRouter не разрешил вызов Jev. Проверьте ключ и доступ к модели.")
        response.raise_for_status()
        answers = response.json()["answers"]
        def probability(key: str) -> float:
            value = float(answers[key]["noul"])
            if not 0 <= value <= 1:
                raise ValueError("Некорректная вероятность")
            return round(value, 3)
        return {"ok": True,
                "gaps": [{"id": str(item.get("id", "")), "support": probability(f"gap_{i}")}
                         for i, item in enumerate(selected_gaps)],
                "branches": [{"id": str(item.get("id", "")), "support": probability(f"branch_{i}")}
                             for i, item in enumerate(selected_branches)],
                "note": "Jev оценивает смысл, но не доказывает полноту маршрута. Спорные места проверьте вручную."}
    except JevReviewError:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise JevReviewError("Смысловая проверка Jev не завершилась. Повторите попытку позже.") from exc


def _mask_state(value, guard: PrivacyGuard):
    if isinstance(value, str):
        return guard.mask(value)
    if isinstance(value, list):
        return [_mask_state(item, guard) for item in value]
    if isinstance(value, dict):
        return {key: _mask_state(item, guard) for key, item in value.items()}
    return value
