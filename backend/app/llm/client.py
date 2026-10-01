"""LLM providers.

- `anthropic`: Claude via the official Anthropic SDK.
- `openai`: any OpenAI-compatible Chat Completions endpoint (OpenAI, vLLM, Ollama,
  LM Studio, OpenRouter, GigaChat/YandexGPT via compatible gateways) over plain HTTP.
- `scripted`: replays prepared answers (tests and offline demo).
"""
from __future__ import annotations

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
    if provider in ("openai", "openai-compatible", "ollama", "vllm"):
        return OpenAICompatibleClient(settings)
    raise LLMError(f"Неизвестный LLM_PROVIDER={settings.llm_provider!r} (anthropic | openai)")
