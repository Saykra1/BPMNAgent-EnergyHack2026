from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "anthropic"        # anthropic | openai
    llm_model: str = ""                    # default per provider
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout: float = 180.0
    llm_temperature: float = 0.1
    llm_effort: str = "low"                # anthropic effort: low | medium | high
    max_repairs: int = 3
    runs_dir: Path = ROOT / "runs"


def get_settings() -> Settings:
    return Settings()
