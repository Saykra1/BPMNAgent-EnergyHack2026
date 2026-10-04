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

    llm_provider: str = "openai"           # openai | yandex | gemini | anthropic
    llm_model: str = ""                    # default per provider
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_fallback_models: str = ""          # comma-separated; empty = discover automatically (gemini)
    llm_repair_model: str = ""             # optional separate model for the self-repair loop
    llm_json_mode: str = "json_schema"     # json_schema | json_object | prompt_json (openai/yandex)
    llm_reasoning_effort: str = ""         # gpt-oss reasoning_effort: low | medium | high (optional)
    yandex_folder_id: str = ""             # Yandex AI Studio folder for gpt://<folder>/<model>
    jev_enabled: bool = False               # optional OpenRouter semantic source-link review
    jev_model: str = "typesafe/jev-1.13"
    llm_timeout: float = 180.0
    llm_temperature: float = 0.1
    llm_effort: str = "low"                # low | medium | high
    max_repairs: int = 3
    llm_onprem_only: bool = False          # refuse any non-local LLM endpoint
    runs_dir: Path = ROOT / "runs"
    require_login: bool = True             # registration/login required for the whole API (teams always need it)
    prompt_versions: str = ""              # pin prompt versions: "ir_extract.system=1,ir_edit.system=2"
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
    settings = Settings(env_file=str(path), **values)
    if settings.prompt_versions:
        import os
        os.environ["PROMPT_VERSIONS"] = settings.prompt_versions    # read by llm.prompts
    return settings
