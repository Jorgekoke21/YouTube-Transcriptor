"""Extraction prompt (pipeline-v6) and the rule "a collection is an unordered set of videos". No OpenAI/YouTube."""

from __future__ import annotations

from pathlib import Path

from app.models.document import DocumentType
from app.models.transcript import Transcript, TranscriptSegment, VideoMetadata
from app.models.usage import AIUsage
from app.prompts import BASE_PROMPT, get_prompt_versions
from app.prompts.stages import EXTRACTION_PROMPT
from app.repositories.database import Database
from app.repositories.extractions import ExtractionRepository
from app.services.ai.base import VideoEvidence
from app.services.ai.chunking import chunk_transcript
from app.services.ai.collection import CollectionPipeline, EvidencePool
from app.services.ai.evidence import ExtractionMeta, compute_extraction_key
from app.services.ai.fake import FakeStructuredLLM, build_fake_processor
from app.services.ai.llm import StructuredLLM
from app.services.ai.pipeline import DocumentPipeline, PipelineConfig
from app.services.ai.schemas import ChunkExtraction, CollectionPlan, OutlineChapter
from app.services.cache import canonical_video_ids, compute_collection_identity, compute_collection_key
from app.services.documents.builder import build_consolidated_document
from app.services.evidence_cache import FileEvidenceStore
from app.utils.timecode import format_range
from app.utils.youtube_url import parse_url_batch
from tests.test_collection_pipeline import A, B, ScriptedCollectionLLM, conflict_plan, ev

C = "CCCCCCCCCCC"


# ---------------------------------------------------------------- extraction prompt


class RecordingLLM(StructuredLLM):
    def __init__(self) -> None:
        self.model = "recording"
        self.requests: list[tuple[str, str]] = []

    def parse(self, *, stage, instructions, input_text, schema, usage):
        self.requests.append((instructions, input_text))
        return ChunkExtraction(topics=[], items=[], procedures=[])


def three_chunk_transcript() -> Transcript:
    words = "una dos tres cuatro cinco seis siete ocho nueve"
    segments = [TranscriptSegment(text=f"Frase {n} {words}.", start=n * 10.0, duration=9.0) for n in range(9)]
    return Transcript(video_id=A, language="Español", language_code="es", is_generated=False, segments=segments)


def test_extraction_prompt_has_no_duplicated_parts() -> None:
    transcript = three_chunk_transcript()
    config = PipelineConfig(target_words=30, max_words=40, max_workers=1)
    chunks = chunk_transcript(transcript, config.target_words, config.max_words)
    assert len(chunks) == 3
    llm = RecordingLLM()
    video = VideoMetadata(video_id=A, url=f"https://www.youtube.com/watch?v={A}", title="Divergencias con RSI")
    DocumentPipeline(llm, config).extract(transcript, video, DocumentType.FULL_NOTES, "es", AIUsage())

    assert len(llm.requests) == 3
    for n, ((instructions, text), chunk) in enumerate(zip(llm.requests, chunks, strict=True), 1):
        assert text.count(f"Fragmento {n} de 3") == 1
        assert text.count("Fragmento") == 1
        assert text.count(chunk.to_prompt_text()) == 1  # the chunk text, once
        assert text.count(format_range(chunk.start_time, chunk.end_time)) == 1  # the time range, once
        assert text.count(f"segundos {chunk.start_time:.1f}–{chunk.end_time:.1f}") == 1
        assert text.count("Vídeo: Divergencias con RSI") == 1 and text.count("TRANSCRIPCIÓN:") == 1
        assert text.count("Transcripción manual.") == 1
        assert instructions.count(BASE_PROMPT) == 1  # base instructions, once
        assert instructions.count(EXTRACTION_PROMPT) == 1
        assert instructions.count("IDIOMA DE SALIDA") == 1
    assert llm.requests[0][1].startswith(
        "Vídeo: Divergencias con RSI\nTranscripción manual.\nFragmento 1 de 3 (00:00 – 00:29; segundos 0.0–29.0)\n\n"
        "TRANSCRIPCIÓN:\n[0.0] Frase 0"
    )


def test_new_extraction_prompt_version_invalidates_old_extractions(tmp_path: Path) -> None:
    assert get_prompt_versions(DocumentType.TRADING)["pipeline"] == "pipeline-v6"
    processor = build_fake_processor(config=PipelineConfig(max_workers=1))
    store = FileEvidenceStore(ExtractionRepository(Database(tmp_path / "app.db")), tmp_path / "videos")
    processor.attach_evidence_store(store)
    transcript = three_chunk_transcript()
    video = VideoMetadata(video_id=A, url=f"https://www.youtube.com/watch?v={A}")

    fingerprint = processor.extraction_fingerprint(DocumentType.TRADING, "es")  # type: ignore[attr-defined]
    assert "pipeline-v6" in fingerprint  # extraction cache
    assert "pipeline-v6" in processor.config_fingerprint(DocumentType.TRADING)  # document cache
    assert "pipeline-v6" in processor.collection_fingerprint(DocumentType.TRADING)  # consolidated cache
    old_fingerprint = fingerprint.replace("pipeline-v6", "pipeline-v5")
    old_key = compute_extraction_key(A, transcript.content_hash(), old_fingerprint)
    meta = ExtractionMeta(A, transcript.content_hash(), "trading", "es", "base-v2+pipeline-v5+trading-v3", old_fingerprint)
    store.save(old_key, meta, [ev("c0-i0", "extracción antigua", 0.0, 9.0)])

    fresh = processor.extract(transcript, video, DocumentType.TRADING, "es")
    assert fresh.cached is False and fresh.cache_key != old_key  # the old extraction is never reused
    assert all(e.text != "extracción antigua" for e in fresh.evidence)
    assert processor.extract(transcript, video, DocumentType.TRADING, "es").cached is True


# ---------------------------------------------------------------- collection = unordered set


def test_collection_identity_and_fingerprint_are_order_free() -> None:  # TEST 1, 2, 3, 7
    abc = [(A, "ha"), (B, "hb"), (C, "hc")]
    model = "fake:m|effort=high,mode=standard"
    key = compute_collection_key(abc, "trading", "es", "pv", model)
    assert compute_collection_key([(C, "hc"), (A, "ha"), (B, "hb")], "trading", "es", "pv", model) == key  # C,A,B
    assert compute_collection_key([(B, "hb"), (C, "hc"), (A, "ha")], "trading", "es", "pv", model) == key  # B,C,A
    assert compute_collection_key([(A, "ha"), (A, "ha"), (B, "hb"), (C, "hc")], "trading", "es", "pv", model) == key
    assert compute_collection_key(abc, "summary", "es", "pv", model) != key
    assert compute_collection_key([(A, "ha"), (B, "hb"), (C, "changed")], "trading", "es", "pv", model) != key

    identity = compute_collection_identity([A, B, C])
    assert compute_collection_identity([C, A, B]) == compute_collection_identity([B, C, A]) == identity
    assert compute_collection_identity([A, A, B, C]) == identity
    assert compute_collection_identity([A, B]) != identity
    assert canonical_video_ids([C, A, B, A]) == [A, B, C]  # canonical order: sorted youtube_id


def test_url_variants_deduplicate_before_identity() -> None:  # TEST 4
    batch = parse_url_batch(
        [
            f"youtube.com/watch?v={A}",
            f"https://youtu.be/{A}",
            f"https://www.youtube.com/watch?v={A}&t=20",
            f"https://youtu.be/{B}",
        ]
    )
    assert [e.video_id for e in batch.unique] == [A, B]
    assert compute_collection_identity(e.video_id for e in batch.unique if e.video_id) == compute_collection_identity([B, A])


def third_source(sources: list[VideoEvidence]) -> VideoEvidence:
    video = VideoMetadata(video_id=C, url=f"https://www.youtube.com/watch?v={C}", title="Gestión del stop", channel="Canal C")
    return VideoEvidence(
        video=video,
        transcript=sources[0].transcript.model_copy(update={"video_id": C}),
        evidence=[ev("c0-i0", "El stop nunca se mueve en contra.", 20.0, 30.0)],
    )


def test_input_order_does_not_affect_evidence_ids(sources: list[VideoEvidence]) -> None:  # TEST 6
    a, b = sources
    c = third_source(sources)

    def logical(pool: EvidencePool) -> dict[str, tuple[str, str, float]]:
        return {i: (e.video_id, e.text, e.start) for i, e in pool.by_id.items()}

    pools = [EvidencePool.build(order) for order in ([a, b, c], [c, a, b], [b, c, a], [a, a, b, c])]
    assert all(logical(p) == logical(pools[0]) for p in pools)
    assert [s.video.video_id for s in pools[0].sources] == [A, B, C]
    assert pools[0].refs == ["V1", "V2", "V3"] and pools[0].by_id["V3.c0-i0"].video_id == C


def test_same_set_sends_identical_prompts(sources: list[VideoEvidence]) -> None:
    a, b = sources
    c = third_source(sources)
    requests = []
    for order in ([a, b, c], [c, b, a]):
        llm = ScriptedCollectionLLM(conflict_plan())
        CollectionPipeline(llm, PipelineConfig(max_workers=1)).run_collection(order, DocumentType.TRADING, "es")
        requests.append(llm.requests)
    assert requests[0] == requests[1]  # the model sees exactly the same collection


class ThematicLLM(FakeStructuredLLM):
    """Fake LLM whose plan orders chapters by topic (stop first), not by source."""

    def _collection_plan(self, input_text: str) -> CollectionPlan:
        return CollectionPlan(
            title="Divergencias y stop",
            summary="s",
            concept_groups=[],
            differences=[],
            chapters=[
                OutlineChapter(title="Stop / invalidación", purpose="", item_ids=["V3.c0-i0", "V2.c0-i1"], subsections=[]),
                OutlineChapter(title="Divergencias", purpose="", item_ids=["V1.c0-i0", "V2.c0-i0"], subsections=[]),
                OutlineChapter(title="Entrada", purpose="", item_ids=["V1.c0-i2", "V1.c0-i1"], subsections=[]),
            ],
            key_points=[],
            omitted_item_ids=[],
        )


def test_thematic_order_and_visible_sources_ignore_input_order(sources: list[VideoEvidence]) -> None:  # TEST 8, 9, 10
    a, b = sources
    c = third_source(sources)
    docs = []
    for order in ([a, b, c], [c, a, b], [b, c, a]):
        result = CollectionPipeline(ThematicLLM(), PipelineConfig(max_workers=1)).run_collection(
            order, DocumentType.TRADING, "es"
        )
        doc = build_consolidated_document(result, [(s.video, s.transcript) for s in order], DocumentType.TRADING, "es")
        assert [s.title for s in doc.sections] == ["Stop / invalidación", "Divergencias", "Entrada"]  # the AI's thematic order
        # visible sources: first appearance in the document ("Stop" cites B then C; A appears later) — never the input order
        assert [s.video_id for s in doc.sources] == [B, C, A]
        first_block = doc.sections[0].blocks[0]
        assert first_block.source_ranges[0].video_id == B
        docs.append(doc.model_dump(exclude={"generated_at"}))
    assert docs[0] == docs[1] == docs[2]  # same set of videos ⇒ same document
