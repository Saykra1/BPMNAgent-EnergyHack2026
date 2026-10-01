"""Диагностика подключения к LLM.

  python scripts/check_llm.py          # настройки + пробный запрос к LLM_MODEL (с ретраями и резервными моделями)
  python scripts/check_llm.py --all    # Gemini: по одному запросу к каждой Flash/Pro-модели, таблица статусов
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import ENV_CANDIDATES, get_settings  # noqa: E402
from app.llm.client import GeminiClient, LLMError, make_client  # noqa: E402

PROMPT = ("Отвечай одним словом.", [{"role": "user", "content": "Скажи: работает"}])

s = get_settings()
print("Файл настроек:", s.env_file or "НЕ НАЙДЕН. Искал:\n  " + "\n  ".join(map(str, ENV_CANDIDATES)))
key = s.llm_api_key
print(f"LLM_PROVIDER={s.llm_provider}  LLM_MODEL={s.llm_model or '(по умолчанию)'}  "
      f"LLM_API_KEY={'задан (' + key[:4] + '…' + key[-4:] + ')' if key else 'НЕ ЗАДАН'}  "
      f"LLM_BASE_URL={s.llm_base_url or '(по умолчанию)'}", flush=True)
try:
    llm = make_client(s)
except LLMError as e:
    print("ОШИБКА:", e)
    sys.exit(1)

if isinstance(llm, GeminiClient):
    try:
        models = llm.list_models()
        print("Доступные модели:", ", ".join(m for m in models if "flash" in m or "pro" in m) or "нет", flush=True)
    except LLMError as e:
        print("Список моделей недоступен:", e)
        models = []
    if "--all" in sys.argv:
        llm.BACKOFF = ()          # one attempt per model, no waiting
        cands = [m for m in models if ("flash" in m or "pro" in m)
                 and not any(w in m for w in GeminiClient.SKIP_WORDS)]
        print(f"\nПроверяю {len(cands)} моделей по одному запросу:")
        ok = []
        for m in cands:
            t = time.time()
            data, err = llm._try_model(m, *PROMPT, False, 50)
            status = "OK" if data else err.split(": ", 1)[-1][:90]
            if data:
                ok.append(m)
            print(f"  {m:40s} {time.time() - t:5.1f} с  {status}", flush=True)
        print("\nРаботают:", ", ".join(ok) or "ни одна")
        if ok:
            print(f"Пропишите в .env:  LLM_MODEL={ok[0]}")
            if len(ok) > 1:
                print(f"                   LLM_FALLBACK_MODELS={','.join(ok[1:4])}")
        sys.exit(0 if ok else 1)

print(f"\nПробный запрос к {getattr(llm, 'model', '')} (каждая попытка печатается):", flush=True)
llm.verbose = True
try:
    r = llm.complete(*PROMPT, max_tokens=50)
    print(f"OK: модель {r.model} ответила «{r.text.strip()}» за {r.latency_s:.1f} с")
except LLMError as e:
    print("ОШИБКА:", e)
    sys.exit(1)
