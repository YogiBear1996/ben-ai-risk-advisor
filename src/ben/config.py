"""Application settings, loaded from environment variables and `.env`.

Every tunable lives here. Secrets use `SecretStr` so they never appear in reprs or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The only place the default model ID is defined. Override with BEN_MODEL.
DEFAULT_MODEL = "claude-sonnet-5"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Claude ---
    anthropic_api_key: SecretStr | None = None
    ben_model: str = DEFAULT_MODEL
    ben_max_tokens: int = 4096
    ben_max_tool_rounds: int = 6
    ben_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "medium"

    # --- Paths & storage ---
    data_dir: Path = PROJECT_ROOT / "data"
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    prompts_dir: Path = PROJECT_ROOT / "prompts"
    database_url: str | None = None  # defaults to sqlite in data_dir
    vector_dir: Path | None = None  # defaults to data_dir/lancedb

    # --- Retrieval ---
    embedding_backend: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path | None = None  # defaults to data_dir/models
    retrieval_top_k: int = 6
    hybrid_search: bool = True
    chunk_max_chars: int = 1800
    chunk_overlap_chars: int = 200

    # --- Conversation ---
    ben_history_turns: int = 10

    # --- Telegram ---
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_url: str | None = None  # public base URL, e.g. https://ben.example.com
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # --- WhatsApp Cloud API ---
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_api_version: str = "v21.0"
    whatsapp_allowed_numbers: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Security & governance ---
    rate_limit_per_minute: int = 10
    rate_limit_burst: int = 5
    redact_before_logging: bool = True
    redact_before_model: bool = False
    retention_days: int = 90
    cleanup_hour_utc: int = 3
    log_level: str = "INFO"

    @field_validator("telegram_allowed_user_ids", "whatsapp_allowed_numbers", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("whatsapp_allowed_numbers", mode="after")
    @classmethod
    def _normalise_numbers(cls, value: list[str]) -> list[str]:
        return [n.lstrip("+").replace(" ", "") for n in value]

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'ben.db'}"

    @property
    def resolved_vector_dir(self) -> Path:
        return self.vector_dir or self.data_dir / "lancedb"


@lru_cache
def get_settings() -> Settings:
    return Settings()
