"""Structured document representation.

The AI returns these structures (via Structured Outputs). Markdown, DOCX, PDF and
TXT are all rendered by the backend from this representation.

NOTE: classes used as OpenAI Structured Output schemas must keep every field
required and without defaults (strict JSON Schema mode).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema


class DocumentType(StrEnum):
    FULL_NOTES = "full_notes"
    SUMMARY = "summary"
    STEP_BY_STEP = "step_by_step"
    STUDY_GUIDE = "study_guide"
    CLEAN_TRANSCRIPT = "clean_transcript"
    TRADING = "trading"


DOCUMENT_TYPE_LABELS: dict[str, dict[DocumentType, str]] = {
    "es": {
        DocumentType.FULL_NOTES: "Apuntes completos",
        DocumentType.SUMMARY: "Resumen",
        DocumentType.STEP_BY_STEP: "Guía paso a paso",
        DocumentType.STUDY_GUIDE: "Manual de estudio",
        DocumentType.CLEAN_TRANSCRIPT: "Transcripción limpia",
        DocumentType.TRADING: "Trading",
    },
    "en": {
        DocumentType.FULL_NOTES: "Full notes",
        DocumentType.SUMMARY: "Summary",
        DocumentType.STEP_BY_STEP: "Step-by-step guide",
        DocumentType.STUDY_GUIDE: "Study guide",
        DocumentType.CLEAN_TRANSCRIPT: "Clean transcript",
        DocumentType.TRADING: "Trading",
    },
}

OutputLanguage = Literal["es", "en"]


class SourceRange(BaseModel):
    start: float = Field(description="Start time in seconds, taken from the source transcript.")
    end: float = Field(description="End time in seconds, taken from the source transcript.")
    # Which video the range belongs to. Only set in multi-video (consolidated) documents; `None` means the
    # document's own video. Invisible to the model (not in the JSON schema) and omitted when unset, so
    # single-video documents, prompts and schemas are unchanged. Always assigned by code, never by the model.
    video_id: SkipJsonSchema[str | None] = Field(default=None, exclude_if=lambda v: v is None)


BlockType = Literal["paragraph", "bullet_list", "numbered_list", "checklist", "table", "quote", "note"]


class ContentBlock(BaseModel):
    type: BlockType = Field(
        description=(
            "paragraph: text. bullet_list/numbered_list/checklist: items. table: table_headers + "
            "table_rows. quote: highlighted statement. note: remark such as an explicit absence "
            "of information in the video."
        )
    )
    text: str = Field(description="Text for paragraph, quote and note blocks. Empty string otherwise.")
    items: list[str] = Field(description="Items for list/checklist blocks. Empty list otherwise.")
    table_headers: list[str] = Field(description="Column headers for table blocks. Empty otherwise.")
    table_rows: list[list[str]] = Field(description="Rows for table blocks. Empty otherwise.")
    source_ranges: list[SourceRange] = Field(
        description="Optional precise time ranges supporting this block. Empty list if not needed."
    )


class DocumentSubsection(BaseModel):
    title: str
    blocks: list[ContentBlock]
    source_ranges: list[SourceRange]


class DocumentSection(BaseModel):
    title: str
    blocks: list[ContentBlock]
    source_ranges: list[SourceRange]
    subsections: list[DocumentSubsection]


class KeyPoint(BaseModel):
    text: str
    source_ranges: list[SourceRange]


class SourceVideo(BaseModel):
    video_id: str
    url: str
    title: str | None = None
    channel: str | None = None
    thumbnail: str | None = None
    transcript_language: str | None = None
    transcript_language_code: str | None = None
    transcript_is_generated: bool | None = None
    duration_seconds: float | None = None


class ProcessedDocument(BaseModel):
    """Final structured document (persisted as processed.json)."""

    title: str
    summary: str
    sections: list[DocumentSection]
    key_points: list[KeyPoint]
    source_video: SourceVideo
    document_type: DocumentType
    output_language: str
    generated_at: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model: str | None = None
    verification_notes: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    """Provenance of one evidence item cited by a consolidated document."""

    evidence_id: str
    youtube_id: str
    video_title: str | None
    source_start: float
    source_end: float
    source_url: str
    text: str


class ConsolidatedDocument(BaseModel):
    """A single document built from several videos (persisted as processed.json of a collection).

    Every `SourceRange` inside carries its `video_id`; `sources` keeps the order the user pasted the URLs.
    """

    kind: Literal["collection"] = "collection"
    title: str
    summary: str
    sections: list[DocumentSection]
    key_points: list[KeyPoint]
    sources: list[SourceVideo]
    citations: list[Citation]
    document_type: DocumentType
    output_language: str
    generated_at: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    model: str | None = None
    verification_notes: list[str] = Field(default_factory=list)


AnyDocument = ProcessedDocument | ConsolidatedDocument
