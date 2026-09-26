"""Consolidated (multi-video) pipeline: provenance, agreements, differences, renderers. No OpenAI calls."""

from __future__ import annotations

import json
import re

import pytest
from docx import Document
from openai.lib._parsing._responses import type_to_text_format_param

from app.models.document import DocumentType, SourceRange
from app.services.ai.base import VideoEvidence
from app.services.ai.collection import CollectionPipeline, EvidencePool, normalize_plan, strip_source_refs
from app.services.ai.evidence import EvidenceItem
from app.services.ai.llm import StructuredLLM
from app.services.ai.pipeline import PipelineConfig
from app.services.ai.schemas import (
    CitedBlock,
    CitedSection,
    CollectionPlan,
    ConceptGroup,
    OutlineChapter,
    OutlineKeyPoint,
    SourceDifference,
    SourcePosition,
    VerifiedCollectionChapter,
    WrittenChapter,
    WrittenCollectionChapter,
)
from app.services.documents.builder import build_consolidated_document, write_document_files
from app.services.documents.markdown import render_markdown
from app.services.documents.text import render_text
from app.utils.youtube_url import parse_url_batch

A, B = "AAAAAAAAAAA", "BBBBBBBBBBB"
LINK_RE = re.compile(r"\]\(https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})&t=(\d+)s\)")


def ev(id_: str, text: str, start: float, end: float) -> EvidenceItem:
    return EvidenceItem(id=id_, kind="rule", certainty="stated", text=text, tags=[], start=start, end=end, chunk_index=0)


def cited(type_: str, text: str = "", ids: list[str] | None = None, items: list[str] | None = None) -> CitedBlock:
    return CitedBlock(type=type_, text=text, items=items or [], table_headers=[], table_rows=[], evidence_ids=ids or [])  # type: ignore[arg-type]


def conflict_plan() -> CollectionPlan:
    return CollectionPlan(
        title="Divergencias: visión conjunta",
        summary="Dos vídeos sobre divergencias.",
        concept_groups=[
            # the model says "complementary", but it is the same point in two videos
            ConceptGroup(statement="Una divergencia no es una entrada.", item_ids=["V1.c0-i0", "V2.c0-i0"], relation="agreement"),
        ],
        differences=[
            SourceDifference(
                topic="Colocación del stop",
                positions=[
                    # V2's item smuggled into V1's position must be dropped
                    SourcePosition(source="V1", statement="Bajo el último mínimo.", item_ids=["V1.c0-i1", "V2.c0-i1"]),
                    SourcePosition(source="v2", statement="Bajo la media de 20 periodos.", item_ids=["V2.c0-i1"]),
                ],
            ),
            # a "difference" with a single source is not a difference
            SourceDifference(
                topic="Solo una fuente", positions=[SourcePosition(source="V1", statement="x", item_ids=["V1.c0-i2"])]
            ),
        ],
        chapters=[
            OutlineChapter(
                title="Divergencias",
                purpose="",
                # V1.c0-i1 is part of a difference: it must not be fused into a chapter
                item_ids=["V1.c0-i0", "V2.c0-i0", "V1.c0-i1", "V9.c0-i0"],
                subsections=[],
            )
        ],
        key_points=[OutlineKeyPoint(text="La divergencia no basta (V1, V2).", item_ids=["V1.c0-i0", "V2.c0-i0", "nope"])],
        omitted_item_ids=[],
    )


class ScriptedCollectionLLM(StructuredLLM):
    def __init__(self, plan: CollectionPlan, fuse_differences_in_verification: bool = False) -> None:
        self.model = "scripted"
        self.plan = plan
        self.fuse = fuse_differences_in_verification
        self.requests: list[tuple[str, str]] = []

    def parse(self, *, stage, instructions, input_text, schema, usage):
        self.requests.append((stage, input_text))
        usage.record(stage, 100, 10)
        if schema is CollectionPlan:
            return self.plan.model_copy(deep=True)
        if schema is WrittenCollectionChapter:
            payload = json.loads(input_text)
            section = CitedSection(
                title=payload["chapter_to_write"]["title"],
                blocks=[
                    # invented id and a real one → only the real ones survive
                    cited(
                        "paragraph",
                        "Una divergencia no es una entrada por sí sola (V1, V2).",
                        ["V1.c0-i0", "V2.c0-i0", "V3.c9-i9"],
                    ),
                    cited("paragraph", "Afirmación sin evidencias."),
                    cited("note", "Ninguna fuente da un objetivo de beneficio."),
                    # an id outside this chapter (it belongs to the differences section) → block dropped
                    cited("paragraph", "El stop va bajo el mínimo.", ["V1.c0-i1"]),
                ],
                subsections=[],
            )
            return WrittenCollectionChapter(section=section)
        if schema is VerifiedCollectionChapter:
            payload = json.loads(input_text)
            section = CitedSection.model_validate(payload["section"])
            if self.fuse and section.title == "Diferencias entre enfoques":
                fused = cited("paragraph", "El stop va bajo el mínimo o la media.", ["V1.c0-i1", "V2.c0-i1"])
                section = CitedSection(title=section.title, blocks=[fused], subsections=[])
            return VerifiedCollectionChapter(issues=[], section=section)
        raise AssertionError(schema)


def run_pipeline(sources: list[VideoEvidence], llm: StructuredLLM):
    pipeline = CollectionPipeline(llm, PipelineConfig(max_workers=1))
    return pipeline.run_collection(sources, DocumentType.TRADING, "es")


# ---------------------------------------------------------------- URL batches


def test_parse_url_batch_normalises_and_dedupes() -> None:
    batch = parse_url_batch(
        "\n  https://youtube.com/watch?v=AAAAAAAAAAA  \n\nhttps://youtu.be/BBBBBBBBBBB?si=x\nnot a url\n"
        "https://www.youtube.com/shorts/AAAAAAAAAAA\nm.youtube.com/watch?v=CCCCCCCCCCC&t=10s\n"
    )
    assert [e.input for e in batch.entries][0] == "https://youtube.com/watch?v=AAAAAAAAAAA"  # trimmed, empty lines gone
    assert [e.video_id for e in batch.unique] == [A, B, "CCCCCCCCCCC"]
    assert [e.input for e in batch.invalid] == ["not a url"]
    assert [(e.video_id, e.duplicate_of) for e in batch.duplicates] == [(A, 0)]
    # a list of lines behaves like a pasted block
    assert [e.video_id for e in parse_url_batch(["https://youtu.be/BBBBBBBBBBB", "", A]).unique] == [B]


def test_url_variants_of_the_same_video_are_one_video() -> None:  # TEST 4
    variants = [
        f"youtube.com/watch?v={A}",
        f"https://youtu.be/{A}",
        f"https://www.youtube.com/watch?v={A}&t=20",
        f"m.youtube.com/shorts/{A}",
    ]
    batch = parse_url_batch(variants)
    assert [e.video_id for e in batch.unique] == [A]
    assert len(batch.duplicates) == 3


# ---------------------------------------------------------------- plan normalisation


def test_normalize_plan_enforces_sources(sources: list[VideoEvidence]) -> None:
    pool = EvidencePool.build(sources)
    plan = normalize_plan(conflict_plan(), pool)

    assert len(plan.differences) == 1
    positions = plan.differences[0].positions
    assert [(p.source, p.item_ids) for p in positions] == [("V1", ["V1.c0-i1"]), ("V2", ["V2.c0-i1"])]
    assert plan.concept_groups[0].relation == "agreement"
    chapter_ids = plan.chapters[0].item_ids
    assert "V1.c0-i1" not in chapter_ids and "V2.c0-i1" not in chapter_ids  # conflicts live in the differences section
    assert "V9.c0-i0" not in chapter_ids
    assert "V1.c0-i2" in chapter_ids  # forgotten idea re-attached, not lost
    assert plan.key_points[0].item_ids == ["V1.c0-i0", "V2.c0-i0"]


def test_normalize_plan_groups_and_orphans(sources: list[VideoEvidence]) -> None:
    pool = EvidencePool.build(sources)
    plan = CollectionPlan(
        title="t",
        summary="s",
        concept_groups=[
            ConceptGroup(statement="misma idea", item_ids=["V1.c0-i0", "V2.c0-i0"], relation="complementary"),
            ConceptGroup(statement="solo V1", item_ids=["V1.c0-i1", "V1.c0-i2"], relation="agreement"),
        ],
        differences=[],
        chapters=[
            OutlineChapter(title="Uno", purpose="", item_ids=["V1.c0-i0"], subsections=[]),
            OutlineChapter(title="Dos", purpose="", item_ids=["V2.c0-i1", "V2.c0-i0"], subsections=[]),
        ],
        key_points=[],
        omitted_item_ids=[],
    )
    plan = normalize_plan(plan, pool)
    assert plan.concept_groups[0].relation == "complementary"
    assert plan.concept_groups[1].relation == "repetition"  # one source only: not an agreement
    uno, dos = plan.chapters
    assert uno.item_ids[:2] == ["V1.c0-i0", "V2.c0-i0"]  # equivalent ideas kept in one chapter (written once)
    assert {"V1.c0-i1", "V1.c0-i2"} <= set(uno.item_ids)  # forgotten V1 ideas go next to V1 content, not lost
    assert dos.item_ids == ["V2.c0-i1"]


# ---------------------------------------------------------------- full consolidated pipeline


def test_conflicts_are_explicit_and_attributed(sources: list[VideoEvidence]) -> None:
    llm = ScriptedCollectionLLM(conflict_plan(), fuse_differences_in_verification=True)
    result = run_pipeline(sources, llm)

    stages = [s for s, _ in llm.requests]
    assert stages.count("consolidation") == 1 and "extraction" not in stages  # transcripts are not re-sent
    consolidation_input = llm.requests[0][1]
    assert '"source":"V1"' in consolidation_input and "Divergencias con RSI" in consolidation_input
    assert "[34.8]" not in consolidation_input  # only evidence, never the transcript lines

    titles = [s.title for s in result.sections]
    assert titles == ["Divergencias", "Coincidencias entre fuentes", "Diferencias entre enfoques"]

    chapter = result.sections[0]
    assert [b.type for b in chapter.blocks] == ["paragraph", "note"]
    assert chapter.blocks[0].text == "Una divergencia no es una entrada por sí sola."  # internal refs removed
    assert {r.video_id for r in chapter.blocks[0].source_ranges} == {A, B}
    assert chapter.blocks[1].source_ranges == []
    assert all(r.video_id == A or r.video_id == B for r in chapter.source_ranges)
    assert any("missing_citation" in n for n in result.verification_notes)

    differences = result.sections[2]
    assert [s.title for s in differences.subsections] == ["Colocación del stop"]
    first, second = differences.subsections[0].blocks
    assert first.text == "**Divergencias con RSI:** Bajo el último mínimo."
    assert second.text == "**RSI y MACD:** Bajo la media de 20 periodos."
    assert [(r.video_id, r.start) for r in first.source_ranges] == [(A, 66.7)]
    assert [(r.video_id, r.start) for r in second.source_ranges] == [(B, 49.5)]
    # verification tried to fuse both rules into one: rejected, the attributed version is kept
    assert any("se conserva la versión anterior" in n for n in result.verification_notes)

    assert result.key_points[0].text == "La divergencia no basta."
    assert {r.video_id for r in result.key_points[0].source_ranges} == {A, B}


def test_every_citation_keeps_its_provenance(sources: list[VideoEvidence]) -> None:
    result = run_pipeline(sources, ScriptedCollectionLLM(conflict_plan()))
    by_id = {c.evidence_id: c for c in result.citations}
    assert set(by_id) == {"V1.c0-i0", "V2.c0-i0", "V1.c0-i1", "V2.c0-i1"}
    stop_a = by_id["V1.c0-i1"]
    assert (stop_a.youtube_id, stop_a.video_title, stop_a.source_start, stop_a.source_end) == (
        A,
        "Divergencias con RSI",
        66.7,
        72.0,
    )
    assert stop_a.source_url == f"https://www.youtube.com/watch?v={A}&t=66s"
    assert by_id["V2.c0-i1"].source_url == f"https://www.youtube.com/watch?v={B}&t=49s"
    assert by_id["V2.c0-i1"].text == "El stop se coloca bajo la media de 20 periodos."


def test_consolidated_document_renders_multi_video_links(sources: list[VideoEvidence], tmp_path) -> None:
    result = run_pipeline(sources, ScriptedCollectionLLM(conflict_plan()))
    doc = build_consolidated_document(result, [(s.video, s.transcript) for s in sources], DocumentType.TRADING, "es")
    assert [s.video_id for s in doc.sources] == [A, B]

    md = render_markdown(doc)
    assert "> **Número de vídeos:** 2" in md
    assert "**Fuentes utilizadas:**" in md
    assert f"1. **Divergencias con RSI** — Canal A — [https://www.youtube.com/watch?v={A}]" in md
    assert f"2. **RSI y MACD** — Canal B — [https://www.youtube.com/watch?v={B}]" in md
    assert "## Diferencias entre enfoques" in md and "### Colocación del stop" in md
    assert f"[Divergencias con RSI · 01:06 – 01:12 ↗](https://www.youtube.com/watch?v={A}&t=66s)" in md
    assert f"[RSI y MACD · 00:49 – 00:54 ↗](https://www.youtube.com/watch?v={B}&t=49s)" in md
    assert "**Fuentes: [" in md
    # each timestamp opens the right video at the right second
    expected = {(c.youtube_id, int(c.source_start)) for c in doc.citations}
    assert set((vid, int(t)) for vid, t in LINK_RE.findall(md)) <= expected | {
        (r.video_id, int(r.start)) for s in doc.sections for r in s.source_ranges
    }

    txt = render_text(doc)
    assert "Fuentes utilizadas:" in txt and f"2. RSI y MACD — Canal B — https://www.youtube.com/watch?v={B}" in txt

    files = write_document_files(doc, tmp_path / "out")
    docx = Document(str(files.docx))
    text = "\n".join(p.text for p in docx.paragraphs)
    assert "Fuentes utilizadas:" in text and "RSI y MACD" in text
    targets = {r.target_ref for r in docx.part.rels.values() if "hyperlink" in r.reltype}
    assert f"https://www.youtube.com/watch?v={A}&t=66s" in targets and f"https://www.youtube.com/watch?v={B}&t=49s" in targets
    assert files.pdf.read_bytes().startswith(b"%PDF")
    reloaded = type(doc).model_validate_json(files.processed_json.read_text(encoding="utf-8"))
    assert reloaded.sections[2].subsections[0].blocks[1].source_ranges[0].video_id == B


def test_requires_two_sources_and_content(sources: list[VideoEvidence]) -> None:
    from app.core.errors import AppError, ErrorCode

    for too_few in (sources[:1], [sources[0], sources[0]]):  # the same video twice is still one video
        with pytest.raises(AppError) as exc:
            run_pipeline(too_few, ScriptedCollectionLLM(conflict_plan()))
        assert exc.value.code == ErrorCode.NOT_ENOUGH_VIDEOS
    empty = [VideoEvidence(video=s.video, transcript=s.transcript, evidence=[]) for s in sources]
    with pytest.raises(AppError) as exc:
        run_pipeline(empty, ScriptedCollectionLLM(conflict_plan()))
    assert exc.value.code == ErrorCode.AI_PROCESSING_FAILED


# ---------------------------------------------------------------- single-video documents are untouched


def test_single_video_schema_and_serialisation_unchanged() -> None:
    assert "video_id" not in json.dumps(type_to_text_format_param(WrittenChapter))
    assert SourceRange(start=1, end=2).model_dump() == {"start": 1.0, "end": 2.0}
    assert SourceRange(start=1, end=2, video_id=A).model_dump() == {"start": 1.0, "end": 2.0, "video_id": A}


@pytest.mark.parametrize("schema", [CollectionPlan, WrittenCollectionChapter, VerifiedCollectionChapter])
def test_collection_schemas_are_valid_strict_json_schemas(schema) -> None:
    fmt = type_to_text_format_param(schema)
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert "video_id" not in json.dumps(fmt)  # the model cites evidence ids; it never writes video ids


def test_strip_source_refs() -> None:
    assert strip_source_refs("Coinciden (V1, V2) en esto [V3].") == "Coinciden en esto."
    assert strip_source_refs("Veo 2 velas (ver 318.6–346.4)") == "Veo 2 velas"
