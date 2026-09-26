"""Multi-stage document pipeline, designed for long videos.

Transcript → chunks → (A) extraction per chunk → (B) global outline →
(C) writer per chapter → (D) verification per chapter → deterministic
timestamp sanitisation.

`clean_transcript` uses a simpler path: per-chunk cleaning, kept in order.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, TypeVar

from app.core.errors import AppError, ErrorCode
from app.models.document import (
    ContentBlock,
    DocumentSection,
    DocumentSubsection,
    DocumentType,
    KeyPoint,
    SourceRange,
)
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.prompts import BASE_PROMPT, MODE_PROMPTS, get_prompt_versions, language_instruction
from app.prompts.stages import (
    CLEAN_TRANSCRIPT_PROMPT,
    EXTRACTION_PROMPT,
    OUTLINE_PROMPT,
    VERIFICATION_PROMPT,
    WRITER_PROMPT,
)
from app.services.ai.base import AIResult, ProgressCallback
from app.services.ai.chunking import TranscriptChunk, chunk_transcript
from app.services.ai.evidence import EvidenceItem
from app.services.ai.llm import StructuredLLM
from app.services.ai.schemas import (
    ChunkExtraction,
    CleanChunk,
    Outline,
    OutlineChapter,
    VerifiedChapter,
    WrittenChapter,
)
from app.services.ai.timestamps import TimestampIndex, merge_ranges, sanitize_ranges
from app.utils.timecode import format_range, format_timestamp

logger = logging.getLogger(__name__)

A = TypeVar("A")
R = TypeVar("R")


@dataclass
class PipelineConfig:
    target_words: int = 1400
    max_words: int = 1900
    enable_verification: bool = True
    max_workers: int = 4


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class DocumentPipeline:
    def __init__(self, llm: StructuredLLM, config: PipelineConfig | None = None) -> None:
        self.llm = llm
        self.config = config or PipelineConfig()

    # ------------------------------------------------------------------ helpers

    def _instructions(self, stage_prompt: str, document_type: DocumentType, output_language: str, mode_text: str) -> str:
        parts = [BASE_PROMPT, language_instruction(output_language), stage_prompt]
        if mode_text:
            parts.append(f"INSTRUCCIONES DEL MODO ({document_type.value}):\n{mode_text}")
        return "\n\n".join(parts)

    def _map(self, fn: Callable[[A], R], items: Sequence[A], on_done: Callable[[int], None]) -> list[R]:
        """Run `fn` over items (in parallel), preserving order and reporting progress."""
        results: list[R | None] = [None] * len(items)
        done = 0
        if len(items) <= 1 or self.config.max_workers <= 1:
            for i, item in enumerate(items):
                results[i] = fn(item)
                done += 1
                on_done(done)
            return results  # type: ignore[return-value]
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                done += 1
                on_done(done)
        return results  # type: ignore[return-value]

    # ------------------------------------------------------------------ entry point

    def run(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        evidence: list[EvidenceItem] | None = None,
        usage: AIUsage | None = None,
    ) -> AIResult:
        """Run the whole pipeline. `evidence` skips stage A (e.g. an extraction reused from the cache)."""
        progress = on_progress or (lambda _data: None)
        usage = usage or AIUsage(model=self.llm.model)
        index = TimestampIndex(transcript.segments)
        chunks = chunk_transcript(transcript, self.config.target_words, self.config.max_words)
        if not chunks:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE, "La transcripción está vacía.")

        if document_type == DocumentType.CLEAN_TRANSCRIPT:
            sections = self._clean_transcript(chunks, index, output_language, usage, progress)
            return AIResult(
                title=video.title or ("Transcripción limpia" if output_language == "es" else "Clean transcript"),
                summary="",
                sections=sections,
                key_points=[],
                usage=usage,
                prompt_versions=get_prompt_versions(document_type),
                model=self.llm.model,
            )

        if evidence is None:
            evidence = self._extract(chunks, index, video, transcript, document_type, output_language, usage, progress)
        if not evidence:
            raise AppError(
                ErrorCode.AI_PROCESSING_FAILED, "No se ha encontrado contenido formativo aprovechable en la transcripción."
            )
        by_id = {e.id: e for e in evidence}

        progress({"stage": "outlining"})
        outline = self._outline(evidence, video, document_type, output_language, usage, transcript.duration)
        outline = normalize_outline(outline, by_id)

        sections = self._write(outline, by_id, index, video, document_type, output_language, usage, progress)

        notes: list[str] = []
        if self.config.enable_verification:
            sections, notes = self._verify(outline, sections, by_id, index, document_type, output_language, usage, progress)

        sections = [s for s in sections if s.blocks or s.subsections]
        key_points = build_key_points(outline, by_id)
        return AIResult(
            title=outline.title.strip() or (video.title or "Documento"),
            summary=outline.summary.strip(),
            sections=sections,
            key_points=key_points,
            usage=usage,
            prompt_versions=get_prompt_versions(document_type),
            model=self.llm.model,
            verification_notes=notes,
        )

    # ------------------------------------------------------------------ A. extraction

    def extract(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        usage: AIUsage,
        on_progress: ProgressCallback | None = None,
    ) -> list[EvidenceItem]:
        """Stage A only: the structured extraction of one video (cacheable, reused by consolidated documents)."""
        index = TimestampIndex(transcript.segments)
        chunks = chunk_transcript(transcript, self.config.target_words, self.config.max_words)
        if not chunks:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE, "La transcripción está vacía.")
        progress = on_progress or (lambda _data: None)
        return self._extract(chunks, index, video, transcript, document_type, output_language, usage, progress)

    def _extract(self, chunks, index, video, transcript, document_type, output_language, usage, progress) -> list[EvidenceItem]:
        mode = MODE_PROMPTS[document_type]
        instructions = self._instructions(EXTRACTION_PROMPT, document_type, output_language, mode.extraction_focus)
        total = len(chunks)
        progress({"stage": "extracting", "done": 0, "total": total})
        source_note = (
            "Transcripción generada automáticamente: puede contener errores de reconocimiento de voz.\n"
            if transcript.is_generated
            else "Transcripción manual.\n"
        )

        def run_chunk(chunk: TranscriptChunk) -> ChunkExtraction:
            header = (
                f"Fragmento {chunk.index + 1} de {total} "
                f"({format_range(chunk.start_time, chunk.end_time)}; segundos {chunk.start_time:.1f}–{chunk.end_time:.1f})"
            )
            text = f"Vídeo: {video.title or video.video_id}\n{source_note}{header}\n\nTRANSCRIPCIÓN:\n{chunk.to_prompt_text()}"
            return self.llm.parse(
                stage="extraction", instructions=instructions, input_text=text, schema=ChunkExtraction, usage=usage
            )

        extractions = self._map(run_chunk, chunks, lambda d: progress({"stage": "extracting", "done": d, "total": total}))
        return collect_evidence(chunks, extractions, index)

    # ------------------------------------------------------------------ B. outline

    def _outline(self, evidence, video, document_type, output_language, usage, duration: float) -> Outline:
        mode = MODE_PROMPTS[document_type]
        instructions = self._instructions(OUTLINE_PROMPT, document_type, output_language, mode.outline_guidance)
        text = (
            f"Vídeo: {video.title or video.video_id}\n"
            f"Canal: {video.channel or '-'}\n"
            f"Duración del vídeo: {format_timestamp(duration)}\n\n"
            f"IDEAS EXTRAÍDAS ({len(evidence)}), en orden temporal, una por línea:\n"
            + "\n".join(_dump(e.to_prompt()) for e in evidence)
        )
        return self.llm.parse(stage="outline", instructions=instructions, input_text=text, schema=Outline, usage=usage)

    # ------------------------------------------------------------------ C. writer

    def _write(self, outline, by_id, index, video, document_type, output_language, usage, progress) -> list[DocumentSection]:
        mode = MODE_PROMPTS[document_type]
        instructions = self._instructions(WRITER_PROMPT, document_type, output_language, mode.writing_guidance)
        overview = [{"title": c.title, "purpose": c.purpose} for c in outline.chapters]
        total = len(outline.chapters)
        progress({"stage": "writing", "done": 0, "total": total})

        def write_chapter(chapter: OutlineChapter) -> DocumentSection:
            items = chapter_evidence(chapter, by_id)
            payload = {
                "document_title": outline.title,
                "video_title": video.title,
                "outline": overview,
                "chapter_to_write": {
                    "title": chapter.title,
                    "purpose": chapter.purpose,
                    "item_ids": chapter.item_ids,
                    "subsections": [{"title": s.title, "item_ids": s.item_ids} for s in chapter.subsections],
                },
                "evidence": [e.to_prompt() for e in items],
            }
            written = self.llm.parse(
                stage="writer", instructions=instructions, input_text=_dump(payload), schema=WrittenChapter, usage=usage
            )
            return finalize_section(written.section, chapter, items, index)

        return self._map(write_chapter, outline.chapters, lambda d: progress({"stage": "writing", "done": d, "total": total}))

    # ------------------------------------------------------------------ D. verification

    def _verify(self, outline, sections, by_id, index, document_type, output_language, usage, progress):
        instructions = self._instructions(VERIFICATION_PROMPT, document_type, output_language, "")
        total = len(sections)
        progress({"stage": "verifying", "done": 0, "total": total})
        pairs = list(zip(outline.chapters, sections, strict=True))

        def verify(pair: tuple[OutlineChapter, DocumentSection]) -> tuple[DocumentSection | None, list[str]]:
            chapter, section = pair
            items = chapter_evidence(chapter, by_id)
            payload = {
                "other_chapters": [c.title for c in outline.chapters if c is not chapter],
                "section": section.model_dump(),
                "evidence": [e.to_prompt() for e in items],
                "source_transcript": index.excerpt([e.range for e in items]),
            }
            result = self.llm.parse(
                stage="verification", instructions=instructions, input_text=_dump(payload), schema=VerifiedChapter, usage=usage
            )
            notes = [f"[{chapter.title}] {i.type}: {i.description} → {i.action_taken}" for i in result.issues]
            verified = finalize_section(result.section, chapter, items, index)
            if not verified.blocks and not verified.subsections:
                return None, notes
            return verified, notes

        results = self._map(verify, pairs, lambda d: progress({"stage": "verifying", "done": d, "total": total}))
        verified_sections = [s for s, _ in results if s is not None]
        notes = [n for _, ns in results for n in ns]
        return verified_sections, notes

    # ------------------------------------------------------------------ clean transcript

    def _clean_transcript(self, chunks, index, output_language, usage, progress) -> list[DocumentSection]:
        mode = MODE_PROMPTS[DocumentType.CLEAN_TRANSCRIPT]
        instructions = self._instructions(
            CLEAN_TRANSCRIPT_PROMPT, DocumentType.CLEAN_TRANSCRIPT, output_language, mode.writing_guidance
        )
        total = len(chunks)
        progress({"stage": "cleaning", "done": 0, "total": total})

        def clean(chunk: TranscriptChunk) -> CleanChunk:
            text = f"Fragmento {chunk.index + 1} de {total}\n\nTRANSCRIPCIÓN:\n{chunk.to_prompt_text()}"
            return self.llm.parse(
                stage="clean_transcript", instructions=instructions, input_text=text, schema=CleanChunk, usage=usage
            )

        results = self._map(clean, chunks, lambda d: progress({"stage": "cleaning", "done": d, "total": total}))
        return assemble_clean_sections(chunks, results, index)


# ---------------------------------------------------------------------- pure helpers (unit tested)


def _trusted_range(start: float, end: float, chunk_range: SourceRange, index: TimestampIndex) -> SourceRange:
    """Clamp a model-provided range to the chunk it came from and snap it to real segments."""
    lo, hi = sorted((start, end))
    lo = min(max(lo, chunk_range.start), chunk_range.end)
    hi = min(max(hi, lo), chunk_range.end)
    return index.snap(SourceRange(start=lo, end=hi)) or chunk_range


def collect_evidence(
    chunks: Sequence[TranscriptChunk], extractions: Sequence[ChunkExtraction], index: TimestampIndex
) -> list[EvidenceItem]:
    """Turn per-chunk extractions into evidence items with ids and trusted timestamps.

    Timestamps are clamped to the chunk they came from (plus a small tolerance)
    and snapped to real segment boundaries.
    """
    evidence: list[EvidenceItem] = []
    for chunk, extraction in zip(chunks, extractions, strict=True):
        chunk_range = SourceRange(start=chunk.start_time, end=chunk.end_time)
        for i, item in enumerate(extraction.items):
            if not item.text.strip():
                continue
            rng = _trusted_range(item.source_start, item.source_end, chunk_range, index)
            evidence.append(
                EvidenceItem(
                    id=f"c{chunk.index}-i{i}",
                    kind=item.kind,
                    certainty=item.certainty,
                    text=item.text.strip(),
                    tags=[t for t in item.tags if t.strip()],
                    start=rng.start,
                    end=rng.end,
                    chunk_index=chunk.index,
                )
            )
        for j, proc in enumerate(extraction.procedures):
            steps = [s.strip() for s in proc.steps if s.strip()]
            if not steps:
                continue
            rng = _trusted_range(proc.source_start, proc.source_end, chunk_range, index)
            evidence.append(
                EvidenceItem(
                    id=f"c{chunk.index}-p{j}",
                    kind="procedure",
                    certainty="stated",
                    text=proc.name.strip() or "Procedimiento",
                    tags=[],
                    start=rng.start,
                    end=rng.end,
                    chunk_index=chunk.index,
                    steps=steps,
                )
            )
    evidence.sort(key=lambda e: (e.start, e.id))
    return evidence


def normalize_outline(outline: Outline, by_id: dict[str, EvidenceItem]) -> Outline:
    """Drop unknown ids and empty chapters; re-attach orphan items so no idea is silently lost."""
    known = set(by_id)
    omitted = {i for i in outline.omitted_item_ids if i in known}
    used: set[str] = set()
    chapters: list[OutlineChapter] = []
    for ch in outline.chapters:
        ch.item_ids = [i for i in dict.fromkeys(ch.item_ids) if i in known]
        subs = []
        for sub in ch.subsections:
            sub.item_ids = [i for i in dict.fromkeys(sub.item_ids) if i in known]
            if sub.item_ids:
                subs.append(sub)
        ch.subsections = subs
        ids = set(ch.item_ids) | {i for s in subs for i in s.item_ids}
        if ids:
            used |= ids
            chapters.append(ch)

    orphans = [by_id[i] for i in by_id if i not in used and i not in omitted]
    if orphans and not chapters:
        chapters.append(OutlineChapter(title=outline.title or "Contenido", purpose="", item_ids=[], subsections=[]))
    for item in orphans:
        best = min(chapters, key=lambda c: _distance_to_chapter(item, c, by_id))
        best.item_ids.append(item.id)
        logger.info("Outline: orphan item %s attached to chapter '%s'", item.id, best.title)

    for kp in outline.key_points:
        kp.item_ids = [i for i in kp.item_ids if i in known]
    outline.chapters = chapters
    return outline


def _distance_to_chapter(item: EvidenceItem, chapter: OutlineChapter, by_id: dict[str, EvidenceItem]) -> float:
    ids = list(chapter.item_ids) + [i for s in chapter.subsections for i in s.item_ids]
    if not ids:
        return float("inf")
    return min(abs(by_id[i].start - item.start) for i in ids)


def chapter_evidence(chapter: OutlineChapter, by_id: dict[str, EvidenceItem]) -> list[EvidenceItem]:
    ids = list(dict.fromkeys(list(chapter.item_ids) + [i for s in chapter.subsections for i in s.item_ids]))
    return sorted((by_id[i] for i in ids if i in by_id), key=lambda e: e.start)


# Raw seconds written inside the text, e.g. "(ver 318.6–346.4)" — timestamps belong in source_ranges.
_INLINE_SECONDS_RE = re.compile(
    r"\s*\((?:ver|see|v[ií]deo|video|seg\.?|s\.?)?:?\s*\d+(?:[.,]\d+)?\s*s?\s*[–-]\s*\d+(?:[.,]\d+)?\s*s?\)", re.I
)


def strip_inline_seconds(text: str) -> str:
    return _INLINE_SECONDS_RE.sub("", text).strip()


def _clean_blocks(blocks: list[ContentBlock], index: TimestampIndex, evidence: list[SourceRange]) -> list[ContentBlock]:
    cleaned: list[ContentBlock] = []
    for b in blocks:
        b.items = [strip_inline_seconds(i) for i in b.items if i and i.strip()]
        b.items = [i for i in b.items if i]
        b.table_rows = [[strip_inline_seconds(c) for c in r] for r in b.table_rows if any(c.strip() for c in r)]
        b.text = strip_inline_seconds(b.text)
        if b.type in ("paragraph", "quote", "note") and not b.text:
            continue
        if b.type in ("bullet_list", "numbered_list", "checklist") and not b.items:
            continue
        if b.type == "table" and not b.table_rows:
            continue
        b.source_ranges = sanitize_ranges(b.source_ranges, index, evidence)
        cleaned.append(b)
    return cleaned


def finalize_section(
    section: DocumentSection, chapter: OutlineChapter, items: list[EvidenceItem], index: TimestampIndex
) -> DocumentSection:
    """Validate a written section: trusted timestamps only, no empty blocks."""
    evidence_ranges = [e.range for e in items]
    section.title = section.title.strip() or chapter.title
    section.blocks = _clean_blocks(section.blocks, index, evidence_ranges)
    # Section-level ranges are computed deterministically from its evidence.
    section.source_ranges = merge_ranges(evidence_ranges, max_gap=60.0)

    outline_subs = {s.title.strip().lower(): s for s in chapter.subsections}
    subsections: list[DocumentSubsection] = []
    for sub in section.subsections:
        sub.blocks = _clean_blocks(sub.blocks, index, evidence_ranges)
        if not sub.blocks:
            continue
        sub.title = sub.title.strip()
        if not sub.title:
            # An untitled subsection would render as an empty heading: keep its content under the previous one.
            (subsections[-1].blocks if subsections else section.blocks).extend(sub.blocks)
            continue
        match = outline_subs.get(sub.title.lower())
        if match:
            sub_ranges = [e.range for e in items if e.id in set(match.item_ids)]
            sub.source_ranges = merge_ranges(sub_ranges, max_gap=60.0)
        else:
            block_ranges = [r for b in sub.blocks for r in b.source_ranges]
            model_ranges = sanitize_ranges(sub.source_ranges, index, evidence_ranges)
            sub.source_ranges = merge_ranges(block_ranges or model_ranges, max_gap=60.0)
        subsections.append(sub)
    section.subsections = subsections
    return section


def build_key_points(outline: Outline, by_id: dict[str, EvidenceItem]) -> list[KeyPoint]:
    """Key points without supporting evidence are dropped (fidelity > completeness)."""
    points: list[KeyPoint] = []
    for kp in outline.key_points:
        ranges = [by_id[i].range for i in kp.item_ids if i in by_id]
        if not kp.text.strip() or not ranges:
            continue
        points.append(KeyPoint(text=kp.text.strip(), source_ranges=merge_ranges(ranges, max_gap=60.0)))
    return points


def assemble_clean_sections(
    chunks: Sequence[TranscriptChunk], results: Sequence[CleanChunk], index: TimestampIndex
) -> list[DocumentSection]:
    sections: list[DocumentSection] = []
    for chunk, result in zip(chunks, results, strict=True):
        chunk_range = [SourceRange(start=chunk.start_time, end=chunk.end_time)]
        for clean_section in result.sections:
            blocks = []
            for p in clean_section.paragraphs:
                if not p.text.strip():
                    continue
                ranges = sanitize_ranges([SourceRange(start=p.source_start, end=p.source_end)], index, chunk_range, 0.0)
                blocks.append(
                    ContentBlock(
                        type="paragraph", text=p.text.strip(), items=[], table_headers=[], table_rows=[], source_ranges=ranges
                    )
                )
            if not blocks:
                continue
            heading = clean_section.heading.strip()
            if not heading and sections:
                sections[-1].blocks.extend(blocks)
            else:
                sections.append(DocumentSection(title=heading, blocks=blocks, source_ranges=[], subsections=[]))
    for s in sections:
        ranges = [r for b in s.blocks for r in b.source_ranges]
        if ranges:
            s.source_ranges = [SourceRange(start=min(r.start for r in ranges), end=max(r.end for r in ranges))]
    return sections
