"""Deterministic detection and masking of personal / sensitive data before text goes to an LLM.

mask(text) replaces ФИО, phones, e-mails, ИНН/СНИЛС/ОГРН, passport and card numbers, contract
numbers (and optionally addresses and energy-object names) with tokens like [ФИО_1]. The same
value always gets the same token, so the model still sees who does what. unmask() puts the
original values back into the model's answer (the IR), so the diagram shows real names while the
LLM provider never received them.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

_PATRONYMIC = r"[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)"
_NAME = r"[А-ЯЁ][а-яё]+"
_INIT = r"[А-ЯЁ]\.\s?[А-ЯЁ]\."

# (type, label, pattern) — order matters: specific identifiers before generic numbers.
RULES: list[tuple[str, str, re.Pattern]] = [
    ("email", "EMAIL", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("snils", "СНИЛС", re.compile(r"\b\d{3}-\d{3}-\d{3}[ -]\d{2}\b")),
    ("inn", "ИНН", re.compile(r"(?<=ИНН)[\s:№]*\d{10}(?:\d{2})?\b|\b\d{12}\b")),
    ("ogrn", "ОГРН", re.compile(r"(?<=ОГРН)(?:ИП)?[\s:№]*\d{13}(?:\d{2})?\b")),
    ("passport", "ПАСПОРТ", re.compile(r"(?i)(?<=паспорт)[а-яё\s:№]{0,12}\d{2}\s?\d{2}\s?№?\s?\d{6}\b")),
    ("card", "КАРТА", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    ("phone", "ТЕЛЕФОН", re.compile(r"(?<!\d)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)")),
    ("contract", "ДОГОВОР", re.compile(r"(?i)(?<=договор)(?:[а-яё]{0,3})\s*№\s*[\w/\-.]*\d[\w/\-]*")),
    ("fio", "ФИО", re.compile(rf"\b{_NAME}\s{_NAME}\s{_PATRONYMIC}\b|\b{_NAME}\s{_INIT}|{_INIT}\s?{_NAME}\b")),
]
OPTIONAL_RULES = {
    "address": ("АДРЕС", re.compile(
        r"(?i)\b(?:г\.|город|ул\.|улица|пр-т|проспект|пер\.|переулок|ш\.|шоссе)\s*[А-ЯЁA-Z][\w\-ё]*"
        r"(?:[\s,]+(?:д\.|дом|корп\.|стр\.|кв\.)?\s*\d+[\w/]*)*")),
    "object": ("ОБЪЕКТ", re.compile(
        r"(?:\bПС|\bТП|\bРП|\bКТП|\bЛЭП|\bВЛ|\bКЛ)\s*(?:№\s*)?\d*\s*(?:\d+\s*кВ\s*)?«[^»]{2,40}»")),
}


@dataclass
class Masked:
    token: str
    type: str
    original: str

    def to_dict(self, reveal: bool = False) -> dict:
        d = asdict(self)
        if not reveal:   # UI shows what was masked without echoing the full value
            d["original"] = _preview(self.original)
        return d


def _preview(value: str) -> str:
    v = value.strip()
    return v if len(v) <= 3 else v[:2] + "•" * min(6, len(v) - 3) + v[-1]


def mask(text: str, addresses: bool = False, objects: bool = False) -> tuple[str, list[Masked]]:
    rules = list(RULES)
    if addresses:
        rules.append(("address",) + OPTIONAL_RULES["address"])
    if objects:
        rules.append(("object",) + OPTIONAL_RULES["object"])
    found: dict[str, Masked] = {}
    counters: dict[str, int] = {}
    out = text
    for kind, label, rx in rules:
        def repl(m: re.Match) -> str:
            raw = m.group(0)
            value = raw.strip(" :№")
            if not value or value.startswith("["):
                return raw
            key = f"{kind}:{value}"
            if key not in found:
                counters[label] = counters.get(label, 0) + 1
                found[key] = Masked(f"[{label}_{counters[label]}]", kind, value)
            lead = raw[:len(raw) - len(raw.lstrip(" :№"))]
            return lead + found[key].token
        out = rx.sub(repl, out)
    return out, list(found.values())


def unmask(obj, items: list[Masked]):
    """Recursively restore original values in strings of a JSON-like object."""
    if not items:
        return obj
    table = {m.token: m.original for m in items}
    rx = re.compile("|".join(re.escape(t) for t in table))
    if isinstance(obj, str):
        return rx.sub(lambda m: table[m.group(0)], obj)
    if isinstance(obj, list):
        return [unmask(x, items) for x in obj]
    if isinstance(obj, dict):
        return {k: unmask(v, items) for k, v in obj.items()}
    return obj


def is_local_url(url: str) -> bool:
    """True for localhost / private-network endpoints (on-prem model)."""
    host = urlparse(url).hostname or ""
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False
