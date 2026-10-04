from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# Where a .env file is looked for, in order. Covers the usual Windows mishaps:
# Notepad saving ".env.txt", the file put one folder up or into backend/.
ENV_CANDIDATES = [
    ROOT / ".env", ROOT / ".env.txt", ROOT / "backend" / ".env", ROOT / "backend" / ".env.txt",
    Path.cwd() / ".env", ROOT.parent / ".env", ROOT.parent / ".env.txt",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    llm_provider: str = "anthropic"        # anthropic | gemini | openai
    llm_model: str = ""                    # default per provider
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_fallback_models: str = ""          # comma-separated; empty = discover automatically (gemini)
    jev_enabled: bool = False               # optional OpenRouter semantic source-link review
    jev_model: str = "typesafe/jev-1.13"
    require_login: bool = False            # demo stays open; account is needed for shared projects
    llm_timeout: float = 180.0
    llm_temperature: float = 0.1
    llm_effort: str = "low"                # low | medium | high
    max_repairs: int = 3
    runs_dir: Path = ROOT / "runs"
    env_file: str | None = None            # which file the settings came from (diagnostics)


def find_env_file() -> Path | None:
    for p in ENV_CANDIDATES:
        if p.is_file():
            return p
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """Tolerant .env parser: UTF-8 with or without BOM, cp1251, quotes, comments, `export`."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip().lower()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if value != "":
            out[key] = value
    return out


def get_settings() -> Settings:
    path = find_env_file()
    if path is None:
        return Settings()
    values = {k: v for k, v in read_env_file(path).items() if k in Settings.model_fields}
    return Settings(env_file=str(path), **values)
