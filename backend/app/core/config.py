"""Centralised application configuration.

Every tunable value (model, ports, providers, chunking...) lives here so it is
never hardcoded across the code base. Values come from environment variables or
from the `.env` file at the repository root (or inside `backend/`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

# Values accepted by the Responses API `reasoning` parameter (see openai.types.shared_params.Reasoning).
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
REASONING_MODES = ("standard", "pro")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI ---
    openai_api_key: SecretStr | None = None
    # No default model on purpose: it must come from OPENAI_MODEL (validated when AI_PROVIDER=openai).
    openai_model: str = ""
    openai_reasoning_effort: str = "high"
    openai_reasoning_mode: str = "standard"
    openai_timeout_seconds: float = 180.0
    openai_max_retries: int = 3

    # --- Server ---
    backend_port: int = 8000
    frontend_port: int = 5173
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Storage ---
    data_dir: Path = Field(default=REPO_ROOT / "data")

    # --- Providers ---
    transcript_provider: str = "youtube"  # "youtube" | "fixture"
    ai_provider: str = "openai"  # "openai" | "fake"
    transcript_languages: str = "es,en"

    # --- AI pipeline ---
    ai_enable_verification: bool = True
    chunk_target_words: int = 1400
    chunk_max_words: int = 1900
    writer_batch_max_items: int = 70

    # --- Multi-URL batches ---
    max_videos_per_batch: int = Field(default=20, ge=1)

    @field_validator("openai_model", "openai_reasoning_effort", "openai_reasoning_mode", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("openai_reasoning_effort")
    @classmethod
    def _check_effort(cls, value: str) -> str:
        value = value.lower() or "high"
        if value not in REASONING_EFFORTS:
            raise ValueError(f"OPENAI_REASONING_EFFORT must be one of {', '.join(REASONING_EFFORTS)} (got {value!r})")
        return value

    @field_validator("openai_reasoning_mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        value = value.lower() or "standard"
        if value not in REASONING_MODES:
            raise ValueError(f"OPENAI_REASONING_MODE must be one of {', '.join(REASONING_MODES)} (got {value!r})")
        return value

    @model_validator(mode="after")
    def _check_openai_model(self) -> Settings:
        if self.ai_provider == "openai":
            if not self.openai_model:
                raise ValueError("OPENAI_MODEL is required when AI_PROVIDER=openai (e.g. OPENAI_MODEL=gpt-5.6-luna)")
            if any(c.isspace() for c in self.openai_model):
                raise ValueError(f"OPENAI_MODEL must not contain spaces (got {self.openai_model!r})")
        return self

    @property
    def openai_reasoning(self) -> dict[str, str]:
        """The `reasoning` parameter sent on every Responses API call."""
        return {"effort": self.openai_reasoning_effort, "mode": self.openai_reasoning_mode}

    def openai_config_summary(self) -> str:
        """Non-sensitive description for logs (never includes the API key)."""
        has_key = bool(self.openai_api_key and self.openai_api_key.get_secret_value().strip())
        return "\n".join(
            [
                "OpenAI configuration:",
                f"model={self.openai_model or '<unset>'}",
                f"reasoning_effort={self.openai_reasoning_effort}",
                f"reasoning_mode={self.openai_reasoning_mode}",
                f"api_key={'configured' if has_key else 'missing'}",
            ]
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def transcript_language_list(self) -> list[str]:
        return [lang.strip().lower() for lang in self.transcript_languages.split(",") if lang.strip()]

    @property
    def resolved_data_dir(self) -> Path:
        path = self.data_dir
        if not path.is_absolute():
            path = (BACKEND_DIR / path).resolve()
        return path

    @property
    def db_path(self) -> Path:
        return self.resolved_data_dir / "app.db"

    @property
    def videos_dir(self) -> Path:
        return self.resolved_data_dir / "videos"

    @property
    def collections_dir(self) -> Path:
        return self.resolved_data_dir / "collections"


@lru_cache
def get_settings() -> Settings:
    return Settings()
