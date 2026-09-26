"""Assembles the final `ProcessedDocument` and writes every export format."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import AppError, ErrorCode
from app.models.document import (
    AnyDocument,
    ConsolidatedDocument,
    DocumentSection,
    DocumentType,
    KeyPoint,
    ProcessedDocument,
    SourceVideo,
)
from app.models.transcript import Transcript, VideoMetadata
from app.services.ai.base import AIResult, CollectionAIResult
from app.services.documents.docx_renderer import render_docx
from app.services.documents.markdown import render_markdown
from app.services.documents.pdf_renderer import render_pdf
from app.services.documents.text import render_text

logger = logging.getLogger(__name__)


@dataclass
class DocumentFiles:
    processed_json: Path
    markdown: Path
    docx: Path
    pdf: Path
    txt: Path


def source_video(video: VideoMetadata, transcript: Transcript) -> SourceVideo:
    return SourceVideo(
        video_id=video.video_id,
        url=video.url,
        title=video.title,
        channel=video.channel,
        thumbnail=video.thumbnail,
        transcript_language=transcript.language,
        transcript_language_code=transcript.language_code,
        transcript_is_generated=transcript.is_generated,
        duration_seconds=round(transcript.duration, 1) if transcript.segments else None,
    )


def build_processed_document(
    result: AIResult,
    video: VideoMetadata,
    transcript: Transcript,
    document_type: DocumentType,
    output_language: str,
) -> ProcessedDocument:
    return ProcessedDocument(
        title=result.title,
        summary=result.summary,
        sections=result.sections,
        key_points=result.key_points,
        source_video=source_video(video, transcript),
        document_type=document_type,
        output_language=output_language,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        prompt_versions=result.prompt_versions,
        model=result.model,
        verification_notes=result.verification_notes,
    )


def sources_by_first_appearance(sections: list[DocumentSection], key_points: list[KeyPoint], video_ids: list[str]) -> list[str]:
    """Order of first citation in the reading order of the document (content blocks, then key points).

    Sources never cited in a block come last, in canonical order. Deterministic and independent of the order in
    which the URLs were given (a collection is an unordered set).
    """
    seen: dict[str, None] = {}
    blocks = [b for s in sections for b in [*s.blocks, *(b for sub in s.subsections for b in sub.blocks)]]
    ranges = [r for b in blocks for r in b.source_ranges] + [r for kp in key_points for r in kp.source_ranges]
    for r in ranges:
        if r.video_id in video_ids:
            seen.setdefault(r.video_id)
    return list(seen) + sorted(set(video_ids) - set(seen))


def build_consolidated_document(
    result: CollectionAIResult,
    sources: list[tuple[VideoMetadata, Transcript]],
    document_type: DocumentType,
    output_language: str,
) -> ConsolidatedDocument:
    """The "Fuentes utilizadas" list follows the first appearance of each source in the document."""
    order = sources_by_first_appearance(result.sections, result.key_points, [video.video_id for video, _ in sources])
    by_id = {video.video_id: source_video(video, transcript) for video, transcript in sources}
    return ConsolidatedDocument(
        title=result.title,
        summary=result.summary,
        sections=result.sections,
        key_points=result.key_points,
        sources=[by_id[vid] for vid in order],
        citations=result.citations,
        document_type=document_type,
        output_language=output_language,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        prompt_versions=result.prompt_versions,
        model=result.model,
        verification_notes=result.verification_notes,
    )


def write_document_files(doc: AnyDocument, out_dir: Path) -> DocumentFiles:
    """Write processed.json + document.{md,docx,pdf,txt}. Markdown is always written first."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = DocumentFiles(
            processed_json=out_dir / "processed.json",
            markdown=out_dir / "document.md",
            docx=out_dir / "document.docx",
            pdf=out_dir / "document.pdf",
            txt=out_dir / "document.txt",
        )
        files.processed_json.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        files.markdown.write_text(render_markdown(doc), encoding="utf-8")
        files.txt.write_text(render_text(doc), encoding="utf-8")
        render_docx(doc, files.docx)
        render_pdf(doc, files.pdf)
        return files
    except Exception as exc:
        logger.exception("Document file generation failed in %s", out_dir)
        raise AppError(ErrorCode.DOCUMENT_GENERATION_FAILED) from exc
