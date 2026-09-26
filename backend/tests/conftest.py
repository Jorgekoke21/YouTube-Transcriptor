"""Shared fixtures. No test here talks to YouTube or OpenAI (see tests/integration for opt-in ones)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.container import build_service
from app.main import create_app
from app.models.document import ContentBlock, DocumentSection, DocumentType, KeyPoint, SourceRange
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.services.ai.base import AIProcessor, AIResult, ProgressCallback, VideoEvidence
from app.services.ai.evidence import EvidenceItem
from app.services.metadata import StaticMetadataProvider
from app.services.transcripts.base import normalize_segments
from app.services.transcripts.fixture import FixtureTranscriptProvider

FIXTURES = Path(__file__).parent / "fixtures"
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def fixture_transcript() -> Transcript:
    data = json.loads((FIXTURES / "transcript_divergencias.json").read_text(encoding="utf-8"))
    return Transcript(
        video_id="dQw4w9WgXcQ",
        language="Español",
        language_code=data["language_code"],
        is_generated=data["is_generated"],
        segments=normalize_segments(data["segments"]),
    )


class StubAIProcessor(AIProcessor):
    """Mock AIProcessor: returns a fixed document and records calls."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_with = fail_with

    def config_fingerprint(self, document_type: DocumentType) -> str:
        return f"stub|{document_type.value}"

    def process(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        refresh_extraction: bool = False,
    ) -> AIResult:
        self.calls.append((document_type.value, output_language))
        if on_progress:
            on_progress({"stage": "extracting", "done": 1, "total": 1})
        if self.fail_with:
            raise self.fail_with
        usage = AIUsage(model="stub")
        usage.record("writer", 10, 5)
        return AIResult(
            title="Divergencias RSI",
            summary="Resumen de prueba.",
            sections=[
                DocumentSection(
                    title="Confirmación de entrada",
                    blocks=[
                        ContentBlock(
                            type="paragraph",
                            text="El profesor espera una ruptura de estructura.",
                            items=[],
                            table_headers=[],
                            table_rows=[],
                            source_ranges=[SourceRange(start=49.5, end=66.7)],
                        )
                    ],
                    source_ranges=[SourceRange(start=49.5, end=66.7)],
                    subsections=[],
                )
            ],
            key_points=[KeyPoint(text="Una divergencia no es una entrada.", source_ranges=[SourceRange(start=34.8, end=40.8)])],
            usage=usage,
            prompt_versions={"base": "base-v1"},
            model="stub",
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_dir=tmp_path / "data",
        openai_api_key=None,
        transcript_provider="fixture",
        ai_provider="fake",
    )


@pytest.fixture
def stub_ai() -> StubAIProcessor:
    return StubAIProcessor()


@pytest.fixture
def service(settings: Settings, stub_ai: StubAIProcessor):
    return build_service(settings, transcripts=FixtureTranscriptProvider(), metadata=StaticMetadataProvider(), ai=stub_ai)


@pytest.fixture
def client(settings: Settings, service) -> TestClient:
    return TestClient(create_app(settings, service=service))


def evidence_item(id_: str, text: str, start: float, end: float) -> EvidenceItem:
    return EvidenceItem(id=id_, kind="rule", certainty="stated", text=text, tags=[], start=start, end=end, chunk_index=0)


@pytest.fixture
def sources(fixture_transcript: Transcript) -> list[VideoEvidence]:
    """Two videos (A, B) with hand-written extractions, for consolidated-document tests."""
    a, b = "AAAAAAAAAAA", "BBBBBBBBBBB"
    video_a = VideoMetadata(
        video_id=a, url=f"https://www.youtube.com/watch?v={a}", title="Divergencias con RSI", channel="Canal A"
    )
    video_b = VideoMetadata(video_id=b, url=f"https://www.youtube.com/watch?v={b}", title="RSI y MACD", channel="Canal B")
    return [
        VideoEvidence(
            video=video_a,
            transcript=fixture_transcript.model_copy(update={"video_id": a}),
            evidence=[
                evidence_item("c0-i0", "Una divergencia por sí sola no es una entrada.", 34.8, 40.8),
                evidence_item("c0-i1", "El stop se coloca bajo el último mínimo.", 66.7, 72.0),
                evidence_item("c0-i2", "Se espera la ruptura del último máximo relevante.", 54.3, 62.7),
            ],
        ),
        VideoEvidence(
            video=video_b,
            transcript=fixture_transcript.model_copy(update={"video_id": b}),
            evidence=[
                evidence_item("c0-i0", "La divergencia no es una señal de entrada por sí misma.", 12.3, 20.0),
                evidence_item("c0-i1", "El stop se coloca bajo la media de 20 periodos.", 49.5, 54.3),
            ],
        ),
    ]
