"""AI pipeline tests. OpenAI is never called: the LLM seam is scripted or mocked."""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest
from openai.lib._parsing._responses import type_to_text_format_param

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.document import ContentBlock, DocumentSection, DocumentSubsection, DocumentType, SourceRange
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.services.ai.fake import FakeStructuredLLM
from app.services.ai.llm import OpenAIStructuredLLM, StructuredLLM
from app.services.ai.openai_processor import OpenAIProcessor
from app.services.ai.pipeline import DocumentPipeline, PipelineConfig
from app.services.ai.schemas import (
    ChunkExtraction,
    CleanChunk,
    ExtractedItem,
    Outline,
    OutlineChapter,
    OutlineKeyPoint,
    VerificationIssue,
    VerifiedChapter,
    WrittenChapter,
)

VIDEO = VideoMetadata(video_id="dQw4w9WgXcQ", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="Divergencias")


def block(text: str, ranges: list[tuple[float, float]]) -> ContentBlock:
    return ContentBlock(
        type="paragraph",
        text=text,
        items=[],
        table_headers=[],
        table_rows=[],
        source_ranges=[SourceRange(start=a, end=b) for a, b in ranges],
    )


def item(text: str, start: float, end: float, kind: str = "rule") -> ExtractedItem:
    return ExtractedItem(kind=kind, certainty="stated", text=text, tags=[], source_start=start, source_end=end)  # type: ignore[arg-type]


class ScriptedLLM(StructuredLLM):
    """Returns canned objects per schema and records every request."""

    def __init__(self) -> None:
        self.model = "scripted"
        self.requests: list[tuple[str, str, str]] = []

    def parse(self, *, stage, instructions, input_text, schema, usage):
        self.requests.append((stage, instructions, input_text))
        usage.record(stage, 100, 10)
        if schema is ChunkExtraction:
            return ChunkExtraction(
                topics=[],
                items=[
                    item("Una divergencia por sí sola no es una entrada.", 34.8, 40.8),
                    item("Se espera la ruptura del último máximo relevante.", 54.3, 62.7, "procedure_step"),
                    item("Timestamp inventado fuera del vídeo.", 9000, 9100, "data"),
                    item("Saludo inicial.", 0.0, 3.2, "other"),
                ],
                procedures=[],
            )
        if schema is Outline:
            return Outline(
                title="Divergencias RSI",
                summary="Resumen.",
                chapters=[
                    OutlineChapter(title="Entrada", purpose="", item_ids=["c0-i0", "c0-i1", "does-not-exist"], subsections=[]),
                    OutlineChapter(title="Vacío", purpose="", item_ids=["nope"], subsections=[]),
                ],
                key_points=[
                    OutlineKeyPoint(text="La divergencia no es una entrada.", item_ids=["c0-i0"]),
                    OutlineKeyPoint(text="Idea sin respaldo.", item_ids=["invented"]),
                ],
                omitted_item_ids=["c0-i3"],
            )
        if schema is WrittenChapter:
            payload = json.loads(input_text)
            return WrittenChapter(
                section=DocumentSection(
                    title=payload["chapter_to_write"]["title"],
                    blocks=[
                        block("Afirmación respaldada.", [(35, 40)]),
                        block("Afirmación con timestamp inventado.", [(700, 710)]),
                        block("   ", []),
                    ],
                    source_ranges=[SourceRange(start=1, end=2)],
                    subsections=[],
                )
            )
        if schema is VerifiedChapter:
            payload = json.loads(input_text)
            section = DocumentSection.model_validate(payload["section"])
            section.blocks = section.blocks[:1]
            return VerifiedChapter(
                issues=[VerificationIssue(type="unsupported_claim", description="x", action_taken="eliminada")],
                section=section,
            )
        raise AssertionError(schema)


def test_pipeline_stages_and_fidelity_guards(fixture_transcript: Transcript) -> None:
    llm = ScriptedLLM()
    progress: list[dict] = []
    result = DocumentPipeline(llm, PipelineConfig(max_workers=1)).run(
        fixture_transcript, VIDEO, DocumentType.FULL_NOTES, "es", progress.append
    )

    assert [r[0] for r in llm.requests] == ["extraction", "outline", "writer", "verification"]
    # The base fidelity prompt and the mode prompt are always sent.
    assert all("No utilices conocimientos externos" in r[1] for r in llm.requests)
    assert "APUNTES COMPLETOS" in llm.requests[1][1]
    assert "[7.3] Hoy vamos a explicar" in llm.requests[0][2]
    assert "generada automáticamente" in llm.requests[0][2]  # auto-caption warning for extraction
    assert "stop ≠ take profit" in llm.requests[0][1]
    # Verification sees the original transcript around the evidence, not only the extracted items.
    verification_payload = json.loads(llm.requests[3][2])
    assert "[34.8] Una divergencia por sí sola no es una entrada." in verification_payload["source_transcript"]
    assert "[16.8]" not in verification_payload["source_transcript"]  # not near any evidence of the chapter

    # Empty chapter dropped; the orphan item with an invented timestamp is re-attached (not lost),
    # with its timestamp clamped to the chunk it came from.
    assert [s.title for s in result.sections] == ["Entrada"]
    outline_input = llm.requests[1][2]
    orphan = next(json.loads(line) for line in outline_input.splitlines() if "inventado" in line)
    assert orphan["start"] <= fixture_transcript.duration

    section = result.sections[0]
    # Verification removed the unsupported block; invented block ranges never survive.
    assert [b.text for b in section.blocks] == ["Afirmación respaldada."]
    assert all(r.end <= fixture_transcript.duration for b in section.blocks for r in b.source_ranges)
    # Section ranges are derived from evidence, not from the model's (1, 2).
    assert section.source_ranges and section.source_ranges[0].start >= 34.8 - 0.01
    # Key points without evidence are dropped.
    assert [k.text for k in result.key_points] == ["La divergencia no es una entrada."]
    assert result.verification_notes and "unsupported_claim" in result.verification_notes[0]

    assert result.usage.calls == 4 and result.usage.by_stage["writer"].calls == 1
    stages = [p["stage"] for p in progress]
    assert stages.index("extracting") < stages.index("outlining") < stages.index("writing") < stages.index("verifying")


def test_pipeline_without_verification(fixture_transcript: Transcript) -> None:
    llm = ScriptedLLM()
    result = DocumentPipeline(llm, PipelineConfig(enable_verification=False, max_workers=1)).run(
        fixture_transcript, VIDEO, DocumentType.TRADING, "en", None
    )
    assert [r[0] for r in llm.requests] == ["extraction", "outline", "writer"]
    assert "stop_loss" in llm.requests[0][1]  # trading categories in the extraction prompt
    assert "inglés" in llm.requests[0][1]  # output language instruction
    # Without verification the invented-timestamp block text is kept but its range is dropped.
    invented = next(b for b in result.sections[0].blocks if "inventado" in b.text)
    assert invented.source_ranges == []


def test_fake_llm_runs_full_pipeline_in_parallel(fixture_transcript: Transcript) -> None:
    long = fixture_transcript.model_copy(deep=True)
    base = list(long.segments)
    for rep in range(1, 30):
        long.segments += [s.model_copy(update={"start": s.start + rep * 110}) for s in base]
    llm = FakeStructuredLLM()
    result = DocumentPipeline(llm, PipelineConfig(target_words=500, max_words=700, max_workers=4)).run(
        long, VIDEO, DocumentType.STUDY_GUIDE, "es", None
    )
    extraction_calls = llm.calls.count("extraction")
    assert extraction_calls > 5
    assert llm.calls.count("writer") == extraction_calls  # fake outline: one chapter per chunk
    assert [s.title for s in result.sections] == [f"Parte {i + 1}" for i in range(extraction_calls)]  # order preserved


def test_clean_transcript_mode(fixture_transcript: Transcript) -> None:
    llm = FakeStructuredLLM()
    result = DocumentPipeline(llm, PipelineConfig(max_workers=1)).run(
        fixture_transcript, VIDEO, DocumentType.CLEAN_TRANSCRIPT, "es", None
    )
    assert llm.calls == ["clean_transcript"]
    assert result.key_points == []
    paragraphs = [b for s in result.sections for b in s.blocks]
    assert paragraphs and all(b.type == "paragraph" and b.source_ranges for b in paragraphs)


def test_pipeline_fails_clearly_without_content(fixture_transcript: Transcript) -> None:
    class EmptyLLM(ScriptedLLM):
        def parse(self, *, stage, instructions, input_text, schema, usage):
            return ChunkExtraction(topics=[], items=[], procedures=[])

    with pytest.raises(AppError) as err:
        DocumentPipeline(EmptyLLM(), PipelineConfig(max_workers=1)).run(
            fixture_transcript, VIDEO, DocumentType.SUMMARY, "es", None
        )
    assert err.value.code == ErrorCode.AI_PROCESSING_FAILED


# ---------------------------------------------------------------- Structured Outputs


@pytest.mark.parametrize("schema", [ChunkExtraction, Outline, WrittenChapter, VerifiedChapter, CleanChunk])
def test_schemas_are_valid_strict_json_schemas(schema) -> None:
    fmt = type_to_text_format_param(schema)
    assert fmt["type"] == "json_schema" and fmt["strict"] is True


def test_structured_output_parsing_roundtrip() -> None:
    raw = {
        "topics": [{"name": "Divergencias", "summary": "s", "source_start": 7.3, "source_end": 30}],
        "items": [
            {"kind": "rule", "certainty": "tentative", "text": "t", "tags": ["entrada"], "source_start": 1, "source_end": 2}
        ],
        "procedures": [{"name": "p", "steps": ["a", "b"], "source_start": 3, "source_end": 4}],
    }
    parsed = ChunkExtraction.model_validate_json(json.dumps(raw))
    assert parsed.items[0].certainty == "tentative"
    with pytest.raises(ValueError):
        ChunkExtraction.model_validate({**raw, "items": [{**raw["items"][0], "kind": "invented_kind"}]})


class FakeResponses:
    def __init__(self, responses: list) -> None:
        self._responses = responses
        self.kwargs: list[dict] = []

    def parse(self, **kwargs):
        self.kwargs.append(kwargs)
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_llm(responses: list) -> tuple[OpenAIStructuredLLM, FakeResponses]:
    llm = OpenAIStructuredLLM(api_key="sk-test", model="test-model", reasoning={"effort": "low", "mode": "standard"})
    fake = FakeResponses(responses)
    llm._client = SimpleNamespace(responses=fake)  # type: ignore[assignment]
    return llm, fake


def response(parsed, status: str = "completed"):
    return SimpleNamespace(output_parsed=parsed, status=status, usage=SimpleNamespace(input_tokens=120, output_tokens=30))


def test_openai_llm_uses_stateless_responses_api_and_records_usage() -> None:
    expected = ChunkExtraction(topics=[], items=[], procedures=[])
    llm, fake = make_llm([response(expected)])
    usage = AIUsage()
    assert llm.parse(stage="extraction", instructions="i", input_text="x", schema=ChunkExtraction, usage=usage) is expected
    kwargs = fake.kwargs[0]
    assert kwargs["store"] is False and kwargs["text_format"] is ChunkExtraction
    assert kwargs["model"] == "test-model" and kwargs["reasoning"] == {"effort": "low", "mode": "standard"}
    assert "tools" not in kwargs and "previous_response_id" not in kwargs
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 120, 30)


def test_openai_llm_retries_unusable_output_then_fails() -> None:
    ok = ChunkExtraction(topics=[], items=[], procedures=[])
    llm, _ = make_llm([response(None), response(ok)])
    assert llm.parse(stage="s", instructions="i", input_text="x", schema=ChunkExtraction, usage=AIUsage()) is ok

    llm, _ = make_llm([response(ok, status="incomplete"), response(None)])
    with pytest.raises(AppError) as err:
        llm.parse(stage="s", instructions="i", input_text="x", schema=ChunkExtraction, usage=AIUsage())
    assert err.value.code == ErrorCode.AI_PROCESSING_FAILED


def test_openai_llm_maps_api_errors() -> None:
    import httpx

    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    auth = openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)
    llm, _ = make_llm([auth])
    with pytest.raises(AppError) as err:
        llm.parse(stage="s", instructions="i", input_text="x", schema=ChunkExtraction, usage=AIUsage())
    assert err.value.code == ErrorCode.AI_NOT_CONFIGURED
    assert "sk-test" not in err.value.message

    llm, _ = make_llm([openai.APITimeoutError(request=req)])
    with pytest.raises(AppError) as err:
        llm.parse(stage="s", instructions="i", input_text="x", schema=ChunkExtraction, usage=AIUsage())
    assert err.value.code == ErrorCode.AI_PROCESSING_FAILED


def test_openai_processor_requires_api_key(fixture_transcript: Transcript) -> None:
    processor = OpenAIProcessor(Settings(_env_file=None, openai_api_key=None, openai_model="m1"))  # type: ignore[call-arg]
    assert not processor.is_configured
    with pytest.raises(AppError) as err:
        processor.process(fixture_transcript, VIDEO, DocumentType.FULL_NOTES, "es")
    assert err.value.code == ErrorCode.AI_NOT_CONFIGURED


def test_model_is_configurable_and_part_of_fingerprint() -> None:
    a = OpenAIProcessor(Settings(_env_file=None, openai_model="model-a"))  # type: ignore[call-arg]
    b = OpenAIProcessor(Settings(_env_file=None, openai_model="model-b"))  # type: ignore[call-arg]
    assert a.llm.model == "model-a"
    assert a.config_fingerprint(DocumentType.TRADING) != b.config_fingerprint(DocumentType.TRADING)
    assert "trading-v3" in a.config_fingerprint(DocumentType.TRADING)


# ---------------------------------------------------------------- deterministic post-processing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Usar una media móvil. (ver 318.6–346.4)", "Usar una media móvil."),
        ("Stop en 52.500 (6.9-36.6)", "Stop en 52.500"),
        ("Entrada en 50,900 USD (antes de la ruptura)", "Entrada en 50,900 USD (antes de la ruptura)"),
        ("Rango (1 – 3 velas) típico", "Rango (1 – 3 velas) típico"),
    ],
)
def test_strip_inline_seconds(raw: str, expected: str) -> None:
    from app.services.ai.pipeline import strip_inline_seconds

    assert strip_inline_seconds(raw) == expected


def test_untitled_subsections_are_merged(fixture_transcript: Transcript) -> None:
    from app.services.ai.pipeline import finalize_section
    from app.services.ai.timestamps import TimestampIndex

    section = DocumentSection(
        title="Entrada",
        blocks=[block("a", [])],
        source_ranges=[],
        subsections=[
            DocumentSubsection(title="Condiciones", blocks=[block("b", [])], source_ranges=[]),
            DocumentSubsection(title="  ", blocks=[block("c", [])], source_ranges=[]),
        ],
    )
    chapter = OutlineChapter(title="Entrada", purpose="", item_ids=[], subsections=[])
    result = finalize_section(section, chapter, [], TimestampIndex(fixture_transcript.segments))
    assert [s.title for s in result.subsections] == ["Condiciones"]
    assert [b.text for b in result.subsections[0].blocks] == ["b", "c"]
