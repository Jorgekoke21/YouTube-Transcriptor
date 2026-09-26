"""Deterministic offline LLM used for development (AI_PROVIDER=fake) and tests.

It exercises the full pipeline without calling OpenAI: it echoes the source
content back in each stage's schema. The output is NOT a real document.
"""

from __future__ import annotations

import json
import re
import time
from typing import TypeVar

from pydantic import BaseModel

from app.models.document import ContentBlock, DocumentSection, SourceRange
from app.models.usage import AIUsage
from app.services.ai.base import AIProcessor
from app.services.ai.llm import StructuredLLM
from app.services.ai.openai_processor import PipelineAIProcessor
from app.services.ai.pipeline import PipelineConfig
from app.services.ai.schemas import (
    ChunkExtraction,
    CitedBlock,
    CitedSection,
    CleanChunk,
    CleanParagraph,
    CleanSection,
    CollectionPlan,
    ConceptGroup,
    ExtractedItem,
    Outline,
    OutlineChapter,
    OutlineKeyPoint,
    VerifiedChapter,
    VerifiedCollectionChapter,
    WrittenChapter,
    WrittenCollectionChapter,
)

T = TypeVar("T", bound=BaseModel)
LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?)\]\s+(.*)$")


def _block(type_: str, text: str = "", items: list[str] | None = None, ranges: list[SourceRange] | None = None) -> ContentBlock:
    return ContentBlock(type=type_, text=text, items=items or [], table_headers=[], table_rows=[], source_ranges=ranges or [])  # type: ignore[arg-type]


class FakeStructuredLLM(StructuredLLM):
    def __init__(self, delay: float = 0.0) -> None:
        self.model = "fake-model"
        self.delay = delay
        self.calls: list[str] = []

    def parse(self, *, stage: str, instructions: str, input_text: str, schema: type[T], usage: AIUsage) -> T:
        self.calls.append(stage)
        if self.delay:
            time.sleep(self.delay)
        usage.record(stage, len(input_text) // 4, 100)
        handler = {
            ChunkExtraction: self._extraction,
            Outline: self._outline,
            WrittenChapter: self._writer,
            VerifiedChapter: self._verification,
            CleanChunk: self._clean,
            CollectionPlan: self._collection_plan,
            WrittenCollectionChapter: self._collection_writer,
            VerifiedCollectionChapter: self._collection_verification,
        }[schema]
        return handler(input_text)  # type: ignore[return-value]

    @staticmethod
    def _lines(input_text: str) -> list[tuple[float, str]]:
        out = []
        for line in input_text.splitlines():
            m = LINE_RE.match(line.strip())
            if m:
                out.append((float(m.group(1)), m.group(2)))
        return out

    def _extraction(self, input_text: str) -> ChunkExtraction:
        lines = self._lines(input_text)
        items = []
        for i, (start, text) in enumerate(lines):
            end = lines[i + 1][0] if i + 1 < len(lines) else start + 3
            items.append(
                ExtractedItem(kind="important_point", certainty="stated", text=text, tags=[], source_start=start, source_end=end)
            )
        return ChunkExtraction(topics=[], items=items, procedures=[])

    def _outline(self, input_text: str) -> Outline:
        records = [json.loads(line) for line in input_text.splitlines() if line.startswith("{")]
        chapters: dict[str, list[str]] = {}
        for r in records:
            chapters.setdefault(r["id"].split("-")[0], []).append(r["id"])
        outline_chapters = [
            OutlineChapter(title=f"Parte {n + 1}", purpose="", item_ids=ids, subsections=[])
            for n, ids in enumerate(chapters.values())
        ]
        key_points = [OutlineKeyPoint(text=r["text"], item_ids=[r["id"]]) for r in records[:3]]
        return Outline(
            title="Documento de ejemplo (IA simulada)",
            summary="Documento generado en modo de desarrollo sin IA real: reproduce el contenido de la transcripción.",
            chapters=outline_chapters,
            key_points=key_points,
            omitted_item_ids=[],
        )

    def _writer(self, input_text: str) -> WrittenChapter:
        payload = json.loads(input_text)
        evidence = payload["evidence"]
        blocks = [
            _block(
                "paragraph", text=evidence[0]["text"], ranges=[SourceRange(start=evidence[0]["start"], end=evidence[0]["end"])]
            ),
        ]
        if len(evidence) > 1:
            blocks.append(_block("bullet_list", items=[e["text"] for e in evidence[1:]]))
        section = DocumentSection(title=payload["chapter_to_write"]["title"], blocks=blocks, source_ranges=[], subsections=[])
        return WrittenChapter(section=section)

    def _verification(self, input_text: str) -> VerifiedChapter:
        payload = json.loads(input_text)
        return VerifiedChapter(issues=[], section=DocumentSection.model_validate(payload["section"]))

    def _clean(self, input_text: str) -> CleanChunk:
        lines = self._lines(input_text)
        paragraphs = []
        for i in range(0, len(lines), 4):
            group = lines[i : i + 4]
            text = " ".join(t for _, t in group).strip()
            paragraphs.append(
                CleanParagraph(text=text[:1].upper() + text[1:] + ".", source_start=group[0][0], source_end=group[-1][0])
            )
        return CleanChunk(sections=[CleanSection(heading="", paragraphs=paragraphs)])

    # ---- consolidated documents: identical texts in different videos become agreements

    def _collection_plan(self, input_text: str) -> CollectionPlan:
        records = [json.loads(line) for line in input_text.splitlines() if line.startswith("{")]
        by_text: dict[str, list[dict]] = {}
        chapters: dict[str, list[str]] = {}
        for r in records:
            by_text.setdefault(r["text"], []).append(r)
            chapters.setdefault(r["id"].split(".")[1].split("-")[0], []).append(r["id"])
        groups = [
            ConceptGroup(
                statement=text,
                item_ids=[r["id"] for r in rs],
                relation="agreement" if len({r["source"] for r in rs}) > 1 else "repetition",
            )
            for text, rs in by_text.items()
            if len(rs) > 1
        ]
        return CollectionPlan(
            title="Documento consolidado de ejemplo (IA simulada)",
            summary="Documento generado en modo de desarrollo sin IA real: integra el contenido de varios vídeos.",
            concept_groups=groups,
            differences=[],
            chapters=[
                OutlineChapter(title=f"Parte {n + 1}", purpose="", item_ids=ids, subsections=[])
                for n, ids in enumerate(chapters.values())
            ],
            key_points=[OutlineKeyPoint(text=r["text"], item_ids=[r["id"]]) for r in records[:3]],
            omitted_item_ids=[],
        )

    def _collection_writer(self, input_text: str) -> WrittenCollectionChapter:
        payload = json.loads(input_text)
        evidence = payload["evidence"]
        blocks: list[CitedBlock] = []
        grouped: set[str] = set()
        for g in payload["concept_groups"]:
            blocks.append(_cited("paragraph", text=g["statement"], ids=g["item_ids"]))
            grouped |= set(g["item_ids"])
        for e in evidence:
            if e["id"] not in grouped:
                blocks.append(_cited("paragraph", text=e["text"], ids=[e["id"]]))
        section = CitedSection(title=payload["chapter_to_write"]["title"], blocks=blocks, subsections=[])
        return WrittenCollectionChapter(section=section)

    def _collection_verification(self, input_text: str) -> VerifiedCollectionChapter:
        payload = json.loads(input_text)
        return VerifiedCollectionChapter(issues=[], section=CitedSection.model_validate(payload["section"]))


def _cited(type_: str, text: str = "", items: list[str] | None = None, ids: list[str] | None = None) -> CitedBlock:
    return CitedBlock(type=type_, text=text, items=items or [], table_headers=[], table_rows=[], evidence_ids=ids or [])  # type: ignore[arg-type]


def build_fake_processor(delay: float = 0.0, config: PipelineConfig | None = None) -> AIProcessor:
    return PipelineAIProcessor(FakeStructuredLLM(delay=delay), config or PipelineConfig(), provider_name="fake")
