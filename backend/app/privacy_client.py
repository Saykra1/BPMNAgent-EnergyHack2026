"""Local PII boundary around the existing model client; the generation pipeline is unchanged."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from .privacy import PrivacyGuard

_active_guard: ContextVar[PrivacyGuard | None] = ContextVar("privacy_guard", default=None)


def active_guard() -> PrivacyGuard | None:
    return _active_guard.get()


@contextmanager
def protected_request(text: str, *, hide=(), show=(), trusted=(), instruction: str = ""):
    guard = PrivacyGuard(text, hide, show, trusted)
    if instruction:
        guard.learn(instruction, own=True)
    token = _active_guard.set(guard)
    try:
        yield guard
    finally:
        _active_guard.reset(token)


class ProtectedClient:
    def __init__(self, client):
        self.client = client
        self.name = client.name
        self.model = getattr(client, "model", None)

    def __getattr__(self, name):
        return getattr(self.client, name)

    def complete(self, system, messages, json_mode=False, max_tokens=8000):
        guard = _active_guard.get()
        if guard is None:
            return self.client.complete(system, messages, json_mode=json_mode, max_tokens=max_tokens)
        safe_messages = guard.mask_messages(messages)
        response = self.client.complete(system, safe_messages, json_mode=json_mode, max_tokens=max_tokens)
        return replace(response, text=guard.unmask(response.text))
