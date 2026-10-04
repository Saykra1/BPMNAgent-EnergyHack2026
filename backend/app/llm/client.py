"""LLM adapter: one interface for every provider.

    llm.complete(messages, schema=None, max_tokens=...) -> LLMResponse

`messages` is a chat list; an optional first {"role": "system"} message carries instructions.
`schema` selects the output mode: None = free text, {} = any JSON object, a JSON Schema dict =
structured output constrained by that schema (each provider uses its native mechanism and degrades
gracefully: json_schema -> json_object -> prompt-only, remembered per client).

Providers (LLM_PROVIDER):
- `openai`: any OpenAI-compatible Chat Completions endpoint (vLLM, Ollama, LM Studio, OpenRouter,
  OpenAI) — gpt-oss-120b, Qwen3 and other open models are served this way.
- `yandex`: Yandex AI Studio (Foundation Models) through its OpenAI-compatible endpoint;
  model names are expanded to gpt://<folder>/<model>.
- `gemini`: Google Gemini via the native generateContent REST API.
- `anthropic`: Claude via the official Anthropic SDK.
- `scripted`: replays prepared answers (tests and offline demo).
Model names, keys and URLs come only from configuration (.env / environment).
"""
from __future__ import annotations

import copy
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from ..config import Settings


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    mode: str = "text"                 # text | json_schema | json_object | prompt_json
    notes: list[str] = field(default_factory=list)


class LLMClient(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict], schema: dict | None = None, *,
                 max_tokens: int = 8000) -> LLMResponse: ...


THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


def clean_text(text: str) -> str:
    """Remove reasoning traces some open models (Qwen3, DeepSeek) put into the answer."""
    text = THINK_RE.sub("", text or "")
    if "<think>" in text and "</think>" not in text:      # truncated reasoning: keep what follows
        text = text.split("<think>", 1)[0]
    return text.strip()


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def inline_refs(schema: dict) -> dict:
    """Resolve local $ref/$defs so that providers with partial JSON Schema support accept it."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**target, **extra})
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def schema_hint(schema: dict) -> str:
    """Prompt fallback when the endpoint cannot enforce a schema natively."""
    import json
    return ("\n\nОтвет — ТОЛЬКО один JSON-объект без пояснений и markdown, строго по JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False))


class BaseLLM:
    """Common entry point; providers implement `_complete`."""
    name = "base"
    model = ""

    def complete(self, messages: list[dict], schema: dict | None = None, *,
                 max_tokens: int = 8000) -> LLMResponse:
        system, rest = split_system(messages)
        if not rest:
            raise LLMError("Пустой запрос к модели")
        resp = self._complete(system, rest, schema, max_tokens)
        resp.text = clean_text(resp.text)
        return resp

    def _complete(self, system: str, messages: list[dict], schema: dict | None,
                  max_tokens: int) -> LLMResponse:  # pragma: no cover - abstract
        raise NotImplementedError


class AnthropicClient(BaseLLM):
    name = "anthropic"

    def __init__(self, settings: Settings):
        import os

        import anthropic

        self._anthropic = anthropic
        api_key = settings.llm_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise LLMError("Не задан LLM_API_KEY (или ANTHROPIC_API_KEY)")
        # base_url is explicit so that an unrelated ANTHROPIC_BASE_URL in the environment is not picked up
        self.client = anthropic.Anthropic(api_key=api_key, timeout=settings.llm_timeout,
                                          base_url=settings.llm_base_url or "https://api.anthropic.com")
        self.model = settings.llm_model or "claude-opus-5-5"
        self.effort = settings.llm_effort

    def _complete(self, system, messages, schema, max_tokens):
        t0 = time.time()
        if schema:
            system = system + schema_hint(inline_refs(schema))
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API {e.status_code}: {e.message}") from e
        except self._anthropic.APIConnectionError as e:
            raise LLMError(f"Нет соединения с Anthropic API: {e}") from e
        if resp.stop_reason == "refusal":
            raise LLMError("Модель отказалась отвечать на запрос")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LLMResponse(text, resp.model, time.time() - t0,
                           resp.usage.input_tokens, resp.usage.output_tokens,
                           "prompt_json" if schema is not None else "text")


class OpenAICompatibleClient(BaseLLM):
    """OpenAI Chat Completions protocol (vLLM, Ollama, OpenRouter, Yandex AI Studio, OpenAI...)."""
    name = "openai"
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    BACKOFF = (2, 5, 12)
    MODES = ("json_schema", "json_object", "prompt_json")

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not settings.llm_base_url:
            raise LLMError("Для провайдера openai задайте LLM_BASE_URL (например http://localhost:8000/v1)")
        if not settings.llm_model:
            raise LLMError("Для провайдера openai задайте LLM_MODEL (например openai/gpt-oss-120b)")
        self.url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self.key = settings.llm_api_key
        self.model = settings.llm_model
        self.temperature = settings.llm_temperature
        self.http = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout, connect=15), transport=transport)
        self.sleep = sleep
        # structured-output capability of the endpoint, downgraded on the first rejection
        self.json_mode = settings.llm_json_mode if settings.llm_json_mode in self.MODES else "json_schema"
        self.reasoning_effort = settings.llm_reasoning_effort

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _body(self, system, messages, schema, max_tokens, mode) -> dict:
        if schema and mode == "prompt_json":
            system = system + schema_hint(inline_refs(schema))
        elif schema is not None and mode == "prompt_json":
            system = system + "\n\nОтвет — ТОЛЬКО один JSON-объект без пояснений."
        body = {"model": self.model, "temperature": self.temperature, "max_tokens": max_tokens,
                "messages": ([{"role": "system", "content": system}] if system else []) + messages}
        if schema is not None and mode == "json_schema" and schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "response", "schema": inline_refs(schema), "strict": False}}
        elif schema is not None and mode in ("json_schema", "json_object"):
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort      # gpt-oss: low | medium | high
        return body

    def _post(self, body) -> httpx.Response:
        r = None
        for attempt in range(len(self.BACKOFF) + 1):
            try:
                r = self.http.post(self.url, json=body, headers=self.headers())
            except httpx.TimeoutException as e:
                raise LLMError(f"LLM API: таймаут ответа ({self.model})") from e
            except httpx.HTTPError as e:
                raise LLMError(f"Нет соединения с LLM API: {e}") from e
            if r.status_code not in self.RETRY_STATUSES or attempt == len(self.BACKOFF):
                return r
            self.sleep(self.BACKOFF[attempt])
        return r

    def _complete(self, system, messages, schema, max_tokens):
        t0 = time.time()
        notes = []
        modes = self.MODES[self.MODES.index(self.json_mode):] if schema is not None else ("text",)
        r = None
        for mode in modes:
            body = self._body(system, messages, schema, max_tokens, mode)
            r = self._post(body)
            if r.status_code == 400 and mode != modes[-1] and "response_format" in body:
                notes.append(f"endpoint отклонил {mode}: {r.text[:160]}")
                self.json_mode = modes[modes.index(mode) + 1]        # remember the downgrade
                continue
            break
        if r.status_code in (401, 403):
            raise LLMError(f"LLM API {r.status_code}: доступ запрещён — проверьте LLM_API_KEY. {r.text[:200]}")
        if r.status_code == 404:
            raise LLMError(f"LLM API 404: модель или URL не найдены (LLM_MODEL={self.model}). {r.text[:200]}")
        if r.status_code != 200:
            raise LLMError(f"LLM API {r.status_code}: {r.text[:400]}")
        data = r.json()
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Неожиданный ответ LLM API: {str(data)[:300]}") from e
        text = msg.get("content") or ""
        if not text and data["choices"][0].get("finish_reason") == "length":
            raise LLMError("Ответ модели обрезан по лимиту токенов (max_tokens)")
        usage = data.get("usage") or {}
        return LLMResponse(text, data.get("model", self.model), time.time() - t0,
                           usage.get("prompt_tokens"), usage.get("completion_tokens"),
                           modes[0] if schema is None else self.json_mode, notes)


class YandexClient(OpenAICompatibleClient):
    """Yandex AI Studio (Foundation Models) via the OpenAI-compatible API.

    LLM_MODEL may be a short name (gpt-oss-120b/latest, qwen3-235b-a22b-fp8/latest, yandexgpt/latest)
    expanded with YANDEX_FOLDER_ID, or a full gpt:// URI. LLM_API_KEY is an API key (Api-Key auth)
    or an IAM token (starts with "t1.", Bearer auth).
    """
    name = "yandex"
    DEFAULT_URL = "https://llm.api.cloud.yandex.net/v1"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not settings.llm_api_key:
            raise LLMError("Для Yandex задайте LLM_API_KEY (API-ключ сервисного аккаунта или IAM-токен)")
        model = settings.llm_model or "gpt-oss-120b/latest"
        if not model.startswith("gpt://"):
            if not settings.yandex_folder_id:
                raise LLMError("Для Yandex задайте YANDEX_FOLDER_ID или полный LLM_MODEL=gpt://<folder>/<model>")
            model = f"gpt://{settings.yandex_folder_id}/{model}"
        s = settings.model_copy(update={"llm_base_url": settings.llm_base_url or self.DEFAULT_URL,
                                        "llm_model": model})
        super().__init__(s, transport, sleep)
        self.folder = settings.yandex_folder_id or model.split("/")[2]

    def headers(self) -> dict:
        scheme = "Bearer" if self.key.startswith("t1.") else "Api-Key"
        return {"Authorization": f"{scheme} {self.key}", "OpenAI-Project": self.folder}


def _gemini_rank(name: str):
    """Order fallback models: "latest" aliases, stable before preview, newer versions, non-lite first."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    ver = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return ("latest" not in name, "preview" in name or "exp" in name, (-ver[0], -ver[1]), "lite" in name, name)


class GeminiClient(BaseLLM):
    """Google Gemini via the native REST API (generateContent).

    Free-tier models are often overloaded (503) or rate limited (429). The client retries
    with backoff, then falls back to other available Flash models discovered with the
    ListModels API (or LLM_FALLBACK_MODELS), and sticks to the first model that answers.
    """
    name = "gemini"
    DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-flash-latest"
    THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": -1}   # Gemini 2.5: -1 = dynamic
    RETRY_STATUSES = (429, 500, 502, 503, 504)
    BACKOFF = (2, 5, 12)
    SKIP_WORDS = ("image", "tts", "audio", "live", "embedding", "vision", "robotics", "computer-use", "native",
                  "omni", "customtools", "deep-research", "nano-banana", "lyria")
    DEADLINE_S = 150          # whole complete() call, all models and retries

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        if not settings.llm_api_key:
            raise LLMError("Не задан LLM_API_KEY для Gemini (ключ из https://aistudio.google.com/apikey)")
        base = (settings.llm_base_url or self.DEFAULT_URL).rstrip("/")
        if base.endswith("/openai"):          # OpenAI-compatible URL given: use the native one
            base = base[: -len("/openai")]
        self.base = base
        self.key = settings.llm_api_key.strip()
        self.model = (settings.llm_model or self.DEFAULT_MODEL).removeprefix("models/")
        self.fallbacks = [m.strip().removeprefix("models/") for m in settings.llm_fallback_models.split(",")
                          if m.strip()]
        self.temperature = settings.llm_temperature
        self.effort = settings.llm_effort
        self.http = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout, connect=15), transport=transport)
        self.sleep = sleep
        self._no_thinking: set[str] = set()
        self._discovered: list[str] | None = None
        self.log: list[str] = []   # human-readable trace of retries / fallbacks
        self.verbose = False        # print every attempt (scripts/check_llm.py)
        self.use_schema = True      # responseJsonSchema accepted until proven otherwise
        self._deadline = float("inf")

    def _note(self, msg: str) -> None:
        self.log.append(msg)
        if self.verbose:
            print("   ", msg, flush=True)

    # ------------------------------------------------------------------ discovery
    def list_models(self) -> list[str]:
        """Models available for this key that support generateContent."""
        r = self.http.get(f"{self.base}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": self.key})
        if r.status_code != 200:
            raise LLMError(f"Gemini ListModels {r.status_code}: {self._error_text(r)}")
        out = []
        for m in r.json().get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                out.append(m["name"].removeprefix("models/"))
        return out

    def _candidates(self) -> list[str]:
        if self.fallbacks:
            return [m for m in self.fallbacks if m != self.model]
        if self._discovered is None:
            try:
                names = self.list_models()
            except (LLMError, httpx.HTTPError):
                names = []
            flash = [n for n in names if "flash" in n and not any(w in n for w in self.SKIP_WORDS)]

            self._discovered = sorted(flash, key=_gemini_rank)[:6]
        return [m for m in self._discovered if m != self.model]

    # ------------------------------------------------------------------ request
    @staticmethod
    def _error_text(r: httpx.Response) -> str:
        try:
            return r.json().get("error", {}).get("message", r.text)
        except ValueError:
            return r.text

    @staticmethod
    def _retry_delay(r: httpx.Response) -> float | None:
        try:
            for d in r.json().get("error", {}).get("details", []):
                if "retryDelay" in d:
                    return float(str(d["retryDelay"]).rstrip("s"))
        except (ValueError, AttributeError):
            pass
        return None

    def _body(self, model, system, messages, schema, max_tokens) -> dict:
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages]
        gen = {"temperature": self.temperature, "maxOutputTokens": max_tokens + 8192}
        if schema is not None:
            gen["responseMimeType"] = "application/json"
            if schema and self.use_schema:
                gen["responseJsonSchema"] = inline_refs(schema)
        if model not in self._no_thinking:
            if model.startswith("gemini-2.5"):
                gen["thinkingConfig"] = {"thinkingBudget": self.THINKING_BUDGET.get(self.effort, 1024)}
            elif model.startswith("gemini-3") or "latest" in model:
                gen["thinkingConfig"] = {"thinkingLevel": "high" if self.effort == "high" else "low"}
        return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": gen}

    def _try_model(self, model, system, messages, schema, max_tokens) -> tuple[dict | None, str]:
        """Returns (response json, '') or (None, error description)."""
        url = f"{self.base}/models/{model}:generateContent"
        last = ""
        for attempt in range(len(self.BACKOFF) + 1):
            if time.time() > self._deadline:
                return None, f"{model}: превышено общее время ожидания"
            body = self._body(model, system, messages, schema, max_tokens)
            t = time.time()
            try:
                r = self.http.post(url, json=body, headers={"x-goog-api-key": self.key})
            except httpx.TimeoutException:
                last = f"{model}: таймаут {time.time() - t:.0f} с"
                self._note(last)
                return None, last
            except httpx.HTTPError as e:
                raise LLMError(f"Нет соединения с Gemini API: {e}") from e
            if r.status_code == 200:
                self._note(f"{model}: 200 OK за {time.time() - t:.1f} с")
                return r.json(), ""
            msg = self._error_text(r)
            last = f"{model}: {r.status_code} {msg[:200]}"
            self._note(f"{last} ({time.time() - t:.1f} с)")
            if r.status_code == 400 and "thinking" in msg.lower() and model not in self._no_thinking:
                self._no_thinking.add(model)      # model does not accept thinkingConfig: resend without it
                continue
            if r.status_code == 400 and "schema" in msg.lower() and self.use_schema and schema:
                self.use_schema = False           # schema not accepted: JSON mode + prompt instead
                system = system + schema_hint(inline_refs(schema))
                continue
            if r.status_code in (400, 401, 403) and ("key" in msg.lower() or r.status_code != 400):
                raise LLMError(f"Gemini API {r.status_code}: {msg[:300]} Проверьте LLM_API_KEY.")
            if r.status_code == 404:
                return None, last                 # unknown model -> next candidate
            if r.status_code in self.RETRY_STATUSES:
                delay = self._retry_delay(r)
                if r.status_code == 429 and delay and delay > 30:
                    return None, last             # quota for this model exhausted -> next candidate
                if attempt < len(self.BACKOFF):
                    pause = min(delay or self.BACKOFF[attempt], 30)
                    self._note(f"{model}: пауза {pause:.0f} с и повтор")
                    self.sleep(pause)
                    continue
                return None, last
            raise LLMError(f"Gemini API {r.status_code}: {msg[:400]}")
        return None, last

    def _complete(self, system, messages, schema, max_tokens):
        t0 = time.time()
        self._deadline = t0 + self.DEADLINE_S
        errors = []
        tried = []

        def models():
            yield self.model
            yield from self._candidates()      # discovered lazily, only if the primary model fails

        for model in models():
            if model in tried:
                continue
            tried.append(model)
            data, err = self._try_model(model, system, messages, schema, max_tokens)
            if data is None:
                errors.append(err)
                continue
            if model != self.model:
                self._note(f"переключение на модель {model}")
                print(f"[bpmn-agent] Gemini: {self.model} недоступна, работаю через {model}")
                self.model = model                # sticky: next calls go straight to the working model
            return self._parse(data, model, t0)
        hint = ""
        if any("таймаут" in e or "время ожидания" in e for e in errors):
            hint = " Модели не успели ответить — проверьте сеть/VPN или выберите другую модель в LLM_MODEL."
        elif any(" 503 " in e for e in errors):
            hint = " Модели перегружены (503) — повторите через пару минут или укажите другую в LLM_MODEL."
        elif any(" 429 " in e for e in errors):
            hint = " Исчерпана квота бесплатного тарифа (429) — подождите или используйте другой ключ/модель."
        elif any(" 404 " in e for e in errors):
            hint = " Модель не найдена (404): запустите python scripts/check_llm.py, он покажет доступные модели."
        raise LLMError("Gemini не ответил ни одной моделью (" + ", ".join(tried) + ")." + hint +
                       " Детали: " + " | ".join(errors[-4:]))

    def _parse(self, data: dict, model: str, t0: float) -> LLMResponse:
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "пустой ответ")
            raise LLMError(f"Gemini не вернул ответ: {reason}")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text and cands[0].get("finishReason") == "MAX_TOKENS":
            raise LLMError("Gemini: ответ обрезан по лимиту токенов (MAX_TOKENS)")
        usage = data.get("usageMetadata") or {}
        return LLMResponse(text, data.get("modelVersion", model), time.time() - t0,
                           usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
                           "json_schema" if self.use_schema else "json_object")


class ScriptedClient(BaseLLM):
    """Returns prepared answers in order (tests, offline demo). Records every call."""
    name = "scripted"
    model = "scripted"

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def _complete(self, system, messages, schema, max_tokens):
        self.calls.append({"system": system, "messages": messages, "schema": schema})
        if not self.answers:
            raise LLMError("ScriptedClient: ответы закончились")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return LLMResponse(answer, "scripted", 0.0, len(str(messages)) // 4, len(answer) // 4,
                           "json_schema" if schema else "text")


PROVIDERS = {
    "openai": OpenAICompatibleClient, "openai-compatible": OpenAICompatibleClient, "vllm": OpenAICompatibleClient,
    "ollama": OpenAICompatibleClient, "openrouter": OpenAICompatibleClient,
    "yandex": YandexClient, "yandexgpt": YandexClient,
    "gemini": GeminiClient, "google": GeminiClient,
    "anthropic": AnthropicClient,
}


def make_client(settings: Settings, role: str = "main") -> LLMClient:
    """Create the adapter from configuration. role="repair" may use a separate model
    (LLM_REPAIR_MODEL) for the self-repair loop; other settings are shared."""
    if role == "repair" and settings.llm_repair_model:
        settings = settings.model_copy(update={"llm_model": settings.llm_repair_model})
    cls = PROVIDERS.get(settings.llm_provider.lower())
    if settings.llm_onprem_only:
        from ..pii import is_local_url
        if cls not in (OpenAICompatibleClient,) or not is_local_url(settings.llm_base_url):
            raise LLMError("Включён режим LLM_ONPREM_ONLY: разрешена только локальная модель "
                           "(LLM_PROVIDER=openai и LLM_BASE_URL на localhost/внутренний адрес).")
    if cls is None:
        raise LLMError(f"Неизвестный LLM_PROVIDER={settings.llm_provider!r} "
                       f"(допустимо: {', '.join(sorted(set(PROVIDERS)))})")
    return cls(settings)
