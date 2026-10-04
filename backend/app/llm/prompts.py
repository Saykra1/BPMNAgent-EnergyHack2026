"""Prompt store. Prompt texts live in versioned files backend/app/prompts/<name>.v<N>.md.

`load(name)` returns the highest version (or the one pinned via PROMPT_VERSIONS="name=1,other=2");
`render(name, **vars)` substitutes {{var}} placeholders. The used versions are written to the run
journal, so every result can be traced to the exact prompt text.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
_FILE_RE = re.compile(r"^(?P<name>.+)\.v(?P<ver>\d+)\.md$")


def _pins() -> dict[str, int]:
    pins = {}
    for item in os.environ.get("PROMPT_VERSIONS", "").split(","):
        if "=" in item:
            k, v = item.split("=", 1)
            if v.strip().isdigit():
                pins[k.strip()] = int(v)
    return pins


@lru_cache(maxsize=None)
def _versions() -> dict[str, dict[int, Path]]:
    out: dict[str, dict[int, Path]] = {}
    for f in PROMPT_DIR.glob("*.md"):
        m = _FILE_RE.match(f.name)
        if m:
            out.setdefault(m["name"], {})[int(m["ver"])] = f
    return out


def version(name: str) -> int:
    versions = _versions().get(name)
    if not versions:
        raise KeyError(f"Нет файла промпта {name}.v*.md в {PROMPT_DIR}")
    pin = _pins().get(name)
    return pin if pin in versions else max(versions)


def load(name: str) -> str:
    text = _versions()[name][version(name)].read_text("utf-8")
    return re.sub(r"^<!--.*?-->\s*", "", text, flags=re.S).rstrip() + "\n"   # strip header comment


def render(name: str, **values) -> str:
    text = load(name)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    left = re.findall(r"\{\{(\w+)\}\}", text)
    if left:
        raise KeyError(f"Промпт {name}: не заданы переменные {left}")
    return text


def used(*names: str) -> dict[str, str]:
    """{name: 'vN'} for the run journal."""
    return {n: f"v{version(n)}" for n in names}


class _Template:
    """Legacy `.format(**kw)` interface over a prompt file (code-generation mode)."""

    def __init__(self, name: str, **fixed):
        self.name, self.fixed = name, fixed

    def format(self, **values) -> str:
        return render(self.name, **self.fixed, **values)

    def __str__(self) -> str:
        return self.format()


# ---- legacy code-generation mode (LLM writes DIAGRAM code from the IR) -------------------------
API_REFERENCE = load("dsl_api_reference")
CODE_RULES = load("dsl_code_rules")
_DSL = dict(api_reference=API_REFERENCE, code_rules=CODE_RULES)
CODEGEN_SYSTEM = render("code_generate.system", **_DSL)
CODEGEN_USER = _Template("code_generate.user")
DIRECT_SYSTEM = render("code_direct.system", **_DSL)
REPAIR_USER = _Template("code_repair.user")
REFINE_SYSTEM = render("code_refine.system", **_DSL)
REFINE_USER = _Template("code_refine.user")
