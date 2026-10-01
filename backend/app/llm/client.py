"""LLM providers.

- `anthropic`: Claude via the official Anthropic SDK.
- `gemini`: Google Gemini via the native generateContent REST API.
- `openai`: any OpenAI-compatible Chat Completions endpoint (OpenAI, vLLM, Ollama,
  LM Studio, OpenRouter, GigaChat/YandexGPT via compatible gateways) over plain HTTP.
- `scripted`: replays prepared answers (tests and offline demo).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
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


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], json_mode: bool = False,
                 max_tokens: int = 8000) -> LLMResponse: ...


class AnthropicClient:
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

    def complete(self, system, messages, json_mode=False, max_tokens=8000):
        t0 = time.time()
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
                           resp.usage.input_tokens, resp.usage.output_tokens)


class OpenAICompatibleClient:
    name = "openai"

    def __init__(self, settings: Settings):
        if not settings.llm_base_url:
            raise LLMError("Для провайдера openai задайте LLM_BASE_URL (например https://api.openai.com/v1)")
        self.url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self.key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout
        self.temperature = settings.llm_temperature

    def complete(self, system, messages, json_mode=False, max_tokens=8000):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        t0 = time.time()
        try:
            r = httpx.post(self.url, json=body, headers=headers, timeout=self.timeout)
            if r.status_code == 400 and json_mode:
                body.pop("response_format")   # some gateways do not support JSON mode
                r = httpx.post(self.url, json=body, headers=headers, timeout=self.timeout)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise LLMError(f"LLM API {e.response.status_code}: {e.response.text[:500]}") from e
        except httpx.HTTPError as e:
            raise LLMError(f"Нет соединения с LLM API: {e}") from e
        data = r.json()
        usage = data.get("usage") or {}
        return LLMResponse(data["choices"][0]["message"]["content"] or "", data.get("model", self.model),
                           time.time() - t0, usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _gemini_rank(name: str):
    """Order fallback models: "latest" aliases, stable before preview, newer versions, non-lite first."""
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    ver = (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return ("latest" not in name, "preview" in name or "exp" in name, (-ver[0], -ver[1]), "lite" in name, name)


class GeminiClient:
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
    SKIP_WORDS = ("image", "tts", "audio", "live", "embedding", "vision", "robotics", "computer-use", "native")

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
        self.http = httpx.Client(timeout=settings.llm_timeout, transport=transport)
        self.sleep = sleep
        self._no_thinking: set[str] = set()
        self._discovered: list[str] | None = None
        self.log: list[str] = []   # human-readable trace of retries / fallbacks

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

    def _body(self, model, system, messages, json_mode, max_tokens) -> dict:
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                    for m in messages]
        gen = {"temperature": self.temperature, "maxOutputTokens": max_tokens + 8192}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        if model not in self._no_thinking:
            if model.startswith("gemini-2.5"):
                gen["thinkingConfig"] = {"thinkingBudget": self.THINKING_BUDGET.get(self.effort, 1024)}
            elif model.startswith("gemini-3") or "latest" in model:
                gen["thinkingConfig"] = {"thinkingLevel": "high" if self.effort == "high" else "low"}
        return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": gen}

    def _try_model(self, model, system, messages, json_mode, max_tokens) -> tuple[dict | None, str]:
        """Returns (response json, '') or (None, error description)."""
        url = f"{self.base}/models/{model}:generateContent"
        last = ""
        for attempt in range(len(self.BACKOFF) + 1):
            body = self._body(model, system, messages, json_mode, max_tokens)
            try:
                r = self.http.post(url, json=body, headers={"x-goog-api-key": self.key})
            except httpx.HTTPError as e:
                raise LLMError(f"Нет соединения с Gemini API: {e}") from e
            if r.status_code == 200:
                return r.json(), ""
            msg = self._error_text(r)
            last = f"{model}: {r.status_code} {msg[:200]}"
            self.log.append(last)
            if r.status_code == 400 and "thinking" in msg.lower() and model not in self._no_thinking:
                self._no_thinking.add(model)      # model does not accept thinkingConfig: resend without it
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
                    self.sleep(min(delay or self.BACKOFF[attempt], 30))
                    continue
                return None, last
            raise LLMError(f"Gemini API {r.status_code}: {msg[:400]}")
        return None, last

    def complete(self, system, messages, json_mode=False, max_tokens=8000):
        t0 = time.time()
        errors = []
        tried = []

        def models():
            yield self.model
            yield from self._candidates()      # discovered lazily, only if the primary model fails

        for model in models():
            if model in tried:
                continue
            tried.append(model)
            data, err = self._try_model(model, system, messages, json_mode, max_tokens)
            if data is None:
                errors.append(err)
                continue
            if model != self.model:
                self.log.append(f"переключение на модель {model}")
                print(f"[bpmn-agent] Gemini: {self.model} недоступна, работаю через {model}")
                self.model = model                # sticky: next calls go straight to the working model
            return self._parse(data, model, t0)
        hint = ""
        if any(" 503 " in e for e in errors):
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
                           usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))


class ScriptedClient:
    """Returns prepared answers in order. Used by tests and the offline demo."""
    name = "scripted"

    def __init__(self, answers: list[str]):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def complete(self, system, messages, json_mode=False, max_tokens=8000):
        self.calls.append({"system": system, "messages": messages})
        if not self.answers:
            raise LLMError("ScriptedClient: ответы закончились")
        return LLMResponse(self.answers.pop(0), "scripted", 0.0)


def make_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicClient(settings)
    if provider in ("gemini", "google"):
        return GeminiClient(settings)
    if provider in ("openai", "openai-compatible", "ollama", "vllm"):
        return OpenAICompatibleClient(settings)
    raise LLMError(f"Неизвестный LLM_PROVIDER={settings.llm_provider!r} (anthropic | gemini | openai)")
