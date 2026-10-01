"""Диагностика подключения к LLM:  python scripts/check_llm.py

Показывает, какой файл .env прочитан и с какими настройками, и делает один короткий запрос к модели.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import ENV_CANDIDATES, get_settings  # noqa: E402
from app.llm.client import LLMError, make_client  # noqa: E402

s = get_settings()
print("Файл настроек:", s.env_file or "НЕ НАЙДЕН. Искал:\n  " + "\n  ".join(map(str, ENV_CANDIDATES)))
key = s.llm_api_key
print(f"LLM_PROVIDER={s.llm_provider}  LLM_MODEL={s.llm_model or '(по умолчанию)'}  "
      f"LLM_API_KEY={'задан (' + key[:4] + '…' + key[-4:] + ')' if key else 'НЕ ЗАДАН'}  "
      f"LLM_BASE_URL={s.llm_base_url or '(по умолчанию)'}")
try:
    llm = make_client(s)
    if hasattr(llm, "list_models"):
        try:
            models = [m for m in llm.list_models() if "flash" in m or "pro" in m]
            print("Доступные модели (flash/pro):", ", ".join(models) or "нет")
        except LLMError as e:
            print("Список моделей недоступен:", e)
    r = llm.complete("Отвечай одним словом.", [{"role": "user", "content": "Скажи: работает"}], max_tokens=50)
    print(f"OK: модель {r.model} ответила «{r.text.strip()}» за {r.latency_s:.1f} с")
    for line in getattr(llm, "log", []):
        print("   ", line)
except LLMError as e:
    print("ОШИБКА:", e)
    for line in getattr(llm, "log", []) if "llm" in dir() else []:
        print("   ", line)
    sys.exit(1)
