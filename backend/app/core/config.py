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
# Optional proxy used ONLY by the YouTube transcript provider (never by OpenAI).
YOUTUBE_PROXY_PROVIDERS = ("none", "webshare")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        # Validation errors must never echo raw input: it contains secrets (API key, proxy credentials).
        hide_input_in_errors=True,
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

    # --- YouTube transcript proxy (cloud IPs are usually blocked by YouTube) ---
    youtube_proxy_provider: str = "none"  # "none" | "webshare"
    webshare_proxy_username: SecretStr | None = None
    webshare_proxy_password: SecretStr | None = None
    webshare_proxy_locations: str = ""  # comma-separated country codes, e.g. "es,de,fr"

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

    @field_validator("youtube_proxy_provider", mode="before")
    @classmethod
    def _check_proxy_provider(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower() or "none"
            if value not in YOUTUBE_PROXY_PROVIDERS:
                raise ValueError(f"YOUTUBE_PROXY_PROVIDER must be one of {', '.join(YOUTUBE_PROXY_PROVIDERS)} (got {value!r})")
        return value

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

    @model_validator(mode="after")
    def _check_youtube_proxy(self) -> Settings:
        if self.youtube_proxy_provider == "webshare":
            missing = [
                name
                for name, secret in (
                    ("WEBSHARE_PROXY_USERNAME", self.webshare_proxy_username),
                    ("WEBSHARE_PROXY_PASSWORD", self.webshare_proxy_password),
                )
                if not (secret and secret.get_secret_value().strip())
            ]
            if missing:
                verb = "is" if len(missing) == 1 else "are"
                raise ValueError(f"{' and '.join(missing)} {verb} required when YOUTUBE_PROXY_PROVIDER=webshare")
        return self

    @property
    def webshare_proxy_location_list(self) -> list[str]:
        return [loc.strip().lower() for loc in self.webshare_proxy_locations.split(",") if loc.strip()]

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
