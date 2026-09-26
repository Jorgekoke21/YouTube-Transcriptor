"""HTTP request / response schemas."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.core.errors import error_dict
from app.models.document import DOCUMENT_TYPE_LABELS, ConsolidatedDocument, DocumentType, ProcessedDocument
from app.models.transcript import ErrorInfo, TranscriptSegment
from app.repositories.collections import CollectionRecord
from app.repositories.documents import DocumentRecord
from app.services.cache import compute_collection_identity


class InspectRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class CreateDocumentRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    document_type: DocumentType = DocumentType.FULL_NOTES
    output_language: Literal["es", "en"] = "es"
    force_regenerate: bool = False


# A pasted batch may contain empty lines, duplicates and invalid URLs: the backend normalises it.
UrlLines = list[Annotated[str, Field(max_length=2048)]]
OutputMode = Literal["individual", "consolidated", "both"]


class BatchInspectRequest(BaseModel):
    urls: UrlLines = Field(min_length=1, max_length=500)


class CreateCollectionRequest(BaseModel):
    """`urls`: the selected videos. A collection is a set: their order and repetitions have no meaning."""

    urls: UrlLines = Field(min_length=1, max_length=500)
    document_type: DocumentType = DocumentType.FULL_NOTES
    output_language: Literal["es", "en"] = "es"
    output_mode: OutputMode = "consolidated"
    force_regenerate: bool = False


class VideoOut(BaseModel):
    video_id: str
    url: str
    title: str | None
    channel: str | None
    thumbnail: str | None
    transcript_language: str | None
    transcript_type: str | None


class DocumentSummaryOut(BaseModel):
    id: int
    status: str
    document_type: DocumentType
    document_type_label: str
    output_language: str
    title: str | None
    video: VideoOut
    created_at: str
    updated_at: str
    reused_from_id: int | None
    error: ErrorInfo | None


class DocumentOut(DocumentSummaryOut):
    progress: dict[str, Any]
    prompt_version: str | None
    model: str | None
    usage: dict[str, Any] | None
    markdown: str | None = None
    document: ProcessedDocument | None = None
    downloads: dict[str, str] = Field(default_factory=dict)


class TranscriptOut(BaseModel):
    video_id: str
    url: str
    title: str | None
    channel: str | None
    language: str
    language_code: str
    is_generated: bool
    duration_seconds: float
    word_count: int
    segments: list[TranscriptSegment]


def summary_from_record(r: DocumentRecord) -> dict[str, Any]:
    labels = DOCUMENT_TYPE_LABELS.get(r.output_language, DOCUMENT_TYPE_LABELS["es"])
    doc_type = DocumentType(r.document_type)
    return {
        "id": r.id,
        "status": r.status,
        "document_type": doc_type,
        "document_type_label": labels[doc_type],
        "output_language": r.output_language,
        "title": r.title,
        "video": VideoOut(
            video_id=r.youtube_id,
            url=r.video_url,
            title=r.video_title,
            channel=r.video_channel,
            thumbnail=r.video_thumbnail,
            transcript_language=r.transcript_language,
            transcript_type=r.transcript_type,
        ),
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "reused_from_id": r.reused_from_id,
        "error": ErrorInfo(**error_dict(r.error_code, r.error_message or "")) if r.error_code else None,
    }


class CollectionVideoOut(BaseModel):
    position: int  # order in which the URL was given (metadata only)
    input_url: str
    video: VideoOut
    status: str
    document_id: int | None
    document_status: str | None
    extraction_cached: bool | None
    error: ErrorInfo | None


class CollectionSummaryOut(BaseModel):
    kind: Literal["collection"] = "collection"
    id: int
    identity: str  # hash of the canonical set of videos: same videos ⇒ same identity, whatever the input order
    status: str
    output_mode: OutputMode
    document_type: DocumentType
    document_type_label: str
    output_language: str
    title: str | None
    video_count: int
    videos: list[CollectionVideoOut]
    created_at: str
    updated_at: str
    reused_from_id: int | None
    error: ErrorInfo | None


class CollectionOut(CollectionSummaryOut):
    progress: dict[str, Any]
    prompt_version: str | None
    model: str | None
    usage: dict[str, Any] | None
    markdown: str | None = None
    document: ConsolidatedDocument | None = None
    downloads: dict[str, str] = Field(default_factory=dict)


def _error(code: str | None, message: str | None) -> ErrorInfo | None:
    return ErrorInfo(**error_dict(code, message or "")) if code else None


def collection_summary_from_record(r: CollectionRecord, source_order: list[str] | None = None) -> dict[str, Any]:
    """Videos in canonical order (youtube_id), or in `source_order` (the consolidated document's source list)."""
    labels = DOCUMENT_TYPE_LABELS.get(r.output_language, DOCUMENT_TYPE_LABELS["es"])
    doc_type = DocumentType(r.document_type)
    rank = {vid: i for i, vid in enumerate(source_order or [])}
    videos = sorted(r.videos, key=lambda v: (rank.get(v.youtube_id, len(rank)), v.youtube_id))
    return {
        "id": r.id,
        "identity": compute_collection_identity(v.youtube_id for v in r.videos),
        "status": r.status,
        "output_mode": r.output_mode,
        "document_type": doc_type,
        "document_type_label": labels[doc_type],
        "output_language": r.output_language,
        "title": r.title,
        "video_count": len(r.videos),
        "videos": [
            CollectionVideoOut(
                position=v.position,
                input_url=v.input_url,
                video=VideoOut(
                    video_id=v.youtube_id,
                    url=v.video_url,
                    title=v.video_title,
                    channel=v.video_channel,
                    thumbnail=v.video_thumbnail,
                    transcript_language=v.transcript_language,
                    transcript_type=v.transcript_type,
                ),
                status=v.status,
                document_id=v.document_id,
                document_status=v.document_status,
                extraction_cached=v.extraction_cached,
                error=_error(v.error_code, v.error_message),
            )
            for v in videos
        ],
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "reused_from_id": r.reused_from_id,
        "error": _error(r.error_code, r.error_message),
    }
