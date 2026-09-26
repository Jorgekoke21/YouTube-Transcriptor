"""OpenAI configuration: env vars, defaults, validation and what actually reaches the Responses API.

No real OpenAI call is made: the SDK client is replaced by a fake that records every `responses.parse` call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.models.document import DocumentType
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.services.ai.fake import FakeStructuredLLM
from app.services.ai.openai_processor import OpenAIProcessor

OPENAI_VARS = ("OPENAI_MODEL", "OPENAI_REASONING_EFFORT", "OPENAI_REASONING_MODE", "OPENAI_API_KEY", "AI_PROVIDER")
VIDEO = VideoMetadata(video_id="dQw4w9WgXcQ", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="Divergencias")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's real environment (e.g. a system-wide OPENAI_API_KEY)."""
    for var in OPENAI_VARS:
        monkeypatch.delenv(var, raising=False)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# ---------------------------------------------------------------- defaults, env vars and validation


def test_reasoning_defaults_are_high_and_standard() -> None:
    s = settings(openai_model="gpt-5.6-luna")
    assert s.openai_reasoning_effort == "high"
    assert s.openai_reasoning_mode == "standard"
    assert s.openai_reasoning == {"effort": "high", "mode": "standard"}


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "medium")
    monkeypatch.setenv("OPENAI_REASONING_MODE", "pro")
    s = settings()
    assert s.openai_model == "gpt-5.6-luna"
    assert s.openai_reasoning == {"effort": "medium", "mode": "pro"}


def test_values_are_read_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_MODEL=gpt-5.6-luna\nOPENAI_REASONING_EFFORT=high\nOPENAI_REASONING_MODE=standard\n", encoding="utf-8"
    )
    s = Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    assert (s.openai_model, s.openai_reasoning_effort, s.openai_reasoning_mode) == ("gpt-5.6-luna", "high", "standard")


def test_values_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "  gpt-5.6-luna ")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", " HIGH ")
    monkeypatch.setenv("OPENAI_REASONING_MODE", "")
    s = settings()
    assert s.openai_reasoning == {"effort": "high", "mode": "standard"}
    assert s.openai_model == "gpt-5.6-luna"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"openai_model": "gpt-5.6-luna", "openai_reasoning_effort": "extreme"}, "OPENAI_REASONING_EFFORT"),
        ({"openai_model": "gpt-5.6-luna", "openai_reasoning_mode": "turbo"}, "OPENAI_REASONING_MODE"),
        ({"openai_model": ""}, "OPENAI_MODEL is required"),
        ({"openai_model": "gpt 5"}, "OPENAI_MODEL must not contain spaces"),
    ],
)
def test_invalid_configuration_fails_at_startup(overrides: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        settings(**overrides)


def test_model_is_not_required_when_openai_is_not_used() -> None:
    assert settings(ai_provider="fake").openai_model == ""


def test_no_model_is_hardcoded() -> None:
    import app.core.config as config

    assert not hasattr(config, "DEFAULT_OPENAI_MODEL")
    with pytest.raises(ValidationError):
        settings()  # AI_PROVIDER defaults to openai and OPENAI_MODEL is unset


# ---------------------------------------------------------------- what reaches the Responses API


class RecordingResponses:
    """Stands in for `client.responses`: records kwargs and answers with deterministic fake outputs."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._fake = FakeStructuredLLM()

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = self._fake.parse(
            stage="recorded",
            instructions=kwargs["instructions"],
            input_text=kwargs["input"],
            schema=kwargs["text_format"],
            usage=AIUsage(),
        )
        return SimpleNamespace(output_parsed=parsed, status="completed", usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def processor_with_recorder(**overrides) -> tuple[OpenAIProcessor, RecordingResponses]:
    s = settings(openai_api_key="sk-test-not-real", openai_model="gpt-5.6-luna", **overrides)
    processor = OpenAIProcessor(s)
    recorder = RecordingResponses()
    llm = processor.build_llm()
    llm._client = SimpleNamespace(responses=recorder)  # type: ignore[attr-defined]
    return processor, recorder


def test_all_four_pipeline_stages_use_luna_high_standard(fixture_transcript: Transcript) -> None:
    processor, recorder = processor_with_recorder()
    result = processor.process(fixture_transcript, VIDEO, DocumentType.FULL_NOTES, "es")

    stages = [call["text_format"].__name__ for call in recorder.calls]
    assert set(stages) == {"ChunkExtraction", "Outline", "WrittenChapter", "VerifiedChapter"}
    for call in recorder.calls:
        assert call["model"] == "gpt-5.6-luna"
        assert call["reasoning"] == {"effort": "high", "mode": "standard"}
        assert call["store"] is False
    assert result.model == "gpt-5.6-luna"


def test_clean_transcript_mode_uses_the_same_configuration(fixture_transcript: Transcript) -> None:
    processor, recorder = processor_with_recorder()
    processor.process(fixture_transcript, VIDEO, DocumentType.CLEAN_TRANSCRIPT, "es")
    assert recorder.calls
    assert all(c["model"] == "gpt-5.6-luna" and c["reasoning"] == {"effort": "high", "mode": "standard"} for c in recorder.calls)


def test_env_overrides_reach_the_api_call(monkeypatch: pytest.MonkeyPatch, fixture_transcript: Transcript) -> None:
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    monkeypatch.setenv("OPENAI_REASONING_MODE", "pro")
    processor, recorder = processor_with_recorder()
    processor.process(fixture_transcript, VIDEO, DocumentType.SUMMARY, "es")
    assert all(c["reasoning"] == {"effort": "low", "mode": "pro"} for c in recorder.calls)


def test_cache_fingerprint_changes_with_reasoning() -> None:
    base = OpenAIProcessor(settings(openai_model="gpt-5.6-luna")).config_fingerprint(DocumentType.TRADING)
    other = OpenAIProcessor(settings(openai_model="gpt-5.6-luna", openai_reasoning_effort="medium"))
    pro = OpenAIProcessor(settings(openai_model="gpt-5.6-luna", openai_reasoning_mode="pro"))
    assert "gpt-5.6-luna|effort=high,mode=standard" in base
    assert other.config_fingerprint(DocumentType.TRADING) != base
    assert pro.config_fingerprint(DocumentType.TRADING) != base


# ---------------------------------------------------------------- startup log and health


def test_startup_log_shows_configuration_but_never_the_key(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    s = settings(openai_api_key="sk-secret-value-123", openai_model="gpt-5.6-luna", data_dir=tmp_path / "data")
    with caplog.at_level(logging.INFO):
        client = TestClient(create_app(s))
    assert "OpenAI configuration:\nmodel=gpt-5.6-luna\nreasoning_effort=high\nreasoning_mode=standard" in caplog.text
    assert "sk-secret-value-123" not in caplog.text

    health = client.get("/api/health").json()
    assert health["model"] == "gpt-5.6-luna"
    assert health["reasoning"] == {"effort": "high", "mode": "standard"}
    assert "sk-secret" not in str(health)
