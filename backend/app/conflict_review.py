"""Optional, read-only contradiction review borrowed from features_from_tyoma."""
from __future__ import annotations

import json
import re

from .privacy_client import protected_request

SYSTEM = """Ты проверяешь описание бизнес-процесса на внутренние противоречия. Противоречие — два правила,
которые невозможно выполнить одновременно в одном и том же случае: несовместимые сроки одного действия,
разные единственные исполнители, запрет и обязанность одного действия или несовместимый порядок шагов.
Правила для разных условий или категорий, неполнота и просто неясность — не противоречие.
Ответь только JSON: {"conflicts": [{"topic": "краткая тема", "rule_a": "дословная цитата 1",
"rule_b": "дословная цитата 2"}]}. Каждая цитата — непрерывный фрагмент исходного текста.
Не выбирай правило сам. Если противоречий нет, верни {"conflicts": []}."""


def _quote_span(quote: str, text: str) -> tuple[int, int] | None:
    words = quote.strip(' \t\r\n«»"“”„\'‘’').split()
    if not words:
        return None
    found = re.search(r"\s+".join(map(re.escape, words)), text)
    return found.span() if found else None


def review(text: str, llm, privacy: dict | None = None) -> dict:
    with protected_request(text, **(privacy or {})) as guard:
        answer = llm.complete(SYSTEM, [{"role": "user", "content": text}], json_mode=True, max_tokens=1200)
        try:
            payload = json.loads(answer.text)
        except json.JSONDecodeError as exc:
            raise ValueError("Модель вернула некорректный список противоречий") from exc
        found = []
        for item in payload.get("conflicts", [])[:10]:
            if not isinstance(item, dict):
                continue
            a = _quote_span(str(item.get("rule_a", "")), text)
            b = _quote_span(str(item.get("rule_b", "")), text)
            if a is None or b is None or (a[0] < b[1] and b[0] < a[1]):
                continue
            found.append({"topic": str(item.get("topic", ""))[:160], "rule_a": text[a[0]:a[1]],
                          "rule_b": text[b[0]:b[1]], "start_a": a[0], "start_b": b[0]})
        return {"conflicts": found, "privacy": guard.report(),
                "note": "Это подсказки для аналитика: исправьте противоречия в описании перед генерацией."}
