"""HTTP endpoints. Thin layer: validation + delegation to `DocumentService`."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import FileResponse

from app.core.errors import AppError, ErrorCode
from app.models.document import DOCUMENT_TYPE_LABELS, DocumentType
from app.models.transcript import BatchInspectionResult, InspectionResult
from app.schemas.api import (
    BatchInspectRequest,
    CollectionOut,
    CollectionSummaryOut,
    CreateCollectionRequest,
    CreateDocumentRequest,
    DocumentOut,
    DocumentSummaryOut,
    InspectRequest,
    TranscriptOut,
    collection_summary_from_record,
    summary_from_record,
)
from app.services.collection_service import CollectionService
from app.services.document_service import DocumentService
from app.utils.filenames import sanitize_filename
from app.utils.youtube_url import canonical_url, is_valid_video_id

router = APIRouter(prefix="/api")

MEDIA_TYPES = {
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "pdf": ("application/pdf", "pdf"),
    "txt": ("text/plain; charset=utf-8", "txt"),
}


def get_service(request: Request) -> DocumentService:
    return request.app.state.service


def get_collections(request: Request) -> CollectionService:
    return request.app.state.collections


@router.get("/health")
def health(service: DocumentService = Depends(get_service)) -> dict:
    ai = service.ai
    return {
        "status": "ok",
        "ai_provider": service.settings.ai_provider,
        "ai_configured": getattr(ai, "is_configured", True),
        "model": service.settings.openai_model if service.settings.ai_provider == "openai" else "fake-model",
        "reasoning": service.settings.openai_reasoning if service.settings.ai_provider == "openai" else None,
        "transcript_provider": service.settings.transcript_provider,
        "max_videos_per_batch": service.settings.max_videos_per_batch,
    }


@router.post("/videos/inspect", response_model=InspectionResult)
def inspect_video(body: InspectRequest, service: DocumentService = Depends(get_service)) -> InspectionResult:
    return service.inspect(body.url)


@router.post("/videos/inspect-batch", response_model=BatchInspectionResult)
def inspect_videos_batch(
    body: BatchInspectRequest, collections: CollectionService = Depends(get_collections)
) -> BatchInspectionResult:
    return collections.inspect_batch(body.urls)


@router.get("/videos/{video_id}/transcript", response_model=TranscriptOut)
def get_transcript(video_id: str, service: DocumentService = Depends(get_service)) -> TranscriptOut:
    if not is_valid_video_id(video_id):
        raise AppError(ErrorCode.INVALID_YOUTUBE_URL)
    transcript, meta = service.get_transcript(video_id)
    return TranscriptOut(
        video_id=video_id,
        url=canonical_url(video_id),
        title=meta.title if meta else None,
        channel=meta.channel if meta else None,
        language=transcript.language,
        language_code=transcript.language_code,
        is_generated=transcript.is_generated,
        duration_seconds=round(transcript.duration, 1),
        word_count=transcript.word_count,
        segments=transcript.segments,
    )


@router.post("/documents", response_model=DocumentOut, status_code=202)
def create_document(
    body: CreateDocumentRequest, background: BackgroundTasks, service: DocumentService = Depends(get_service)
) -> DocumentOut:
    record = service.create(body.url, body.document_type, body.output_language)
    background.add_task(service.run, record.id, body.force_regenerate)
    return DocumentOut(**summary_from_record(record), progress=record.progress, prompt_version=None, model=None, usage=None)


@router.get("/documents", response_model=list[DocumentSummaryOut])
def list_documents(
    limit: int = Query(default=20, ge=1, le=100), service: DocumentService = Depends(get_service)
) -> list[DocumentSummaryOut]:
    return [DocumentSummaryOut(**summary_from_record(r)) for r in service.list_recent(limit)]


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, service: DocumentService = Depends(get_service)) -> DocumentOut:
    record = service.get_record(document_id)
    markdown, processed = service.load_content(record)
    downloads = {fmt: f"/api/documents/{record.id}/download/{fmt}" for fmt in MEDIA_TYPES} if record.status == "completed" else {}
    return DocumentOut(
        **summary_from_record(record),
        progress=record.progress,
        prompt_version=record.prompt_version,
        model=record.model,
        usage=record.usage,
        markdown=markdown,
        document=processed,
        downloads=downloads,
    )


@router.get("/documents/{document_id}/download/{fmt}")
def download_document(document_id: int, fmt: str, service: DocumentService = Depends(get_service)) -> FileResponse:
    if fmt not in MEDIA_TYPES:
        raise AppError(ErrorCode.INVALID_REQUEST, "Formato no soportado.")
    record, path = service.file_for(document_id, fmt)
    media_type, ext = MEDIA_TYPES[fmt]
    type_label = DOCUMENT_TYPE_LABELS["en"][DocumentType(record.document_type)]
    stem = sanitize_filename(f"{record.video_title or record.title or record.youtube_id} - {type_label}")
    return FileResponse(path, media_type=media_type, filename=f"{stem}.{ext}")


# ---------------------------------------------------------------------- document collections (multi-video)


@router.post("/document-collections", response_model=CollectionOut, status_code=202)
def create_collection(
    body: CreateCollectionRequest, background: BackgroundTasks, collections: CollectionService = Depends(get_collections)
) -> CollectionOut:
    record = collections.create(body.urls, body.document_type, body.output_language, body.output_mode)
    background.add_task(collections.run, record.id, body.force_regenerate)
    return CollectionOut(
        **collection_summary_from_record(record), progress=record.progress, prompt_version=None, model=None, usage=None
    )


@router.get("/document-collections", response_model=list[CollectionSummaryOut])
def list_collections(
    limit: int = Query(default=20, ge=1, le=100), collections: CollectionService = Depends(get_collections)
) -> list[CollectionSummaryOut]:
    return [CollectionSummaryOut(**collection_summary_from_record(r)) for r in collections.list_recent(limit)]


@router.get("/document-collections/{collection_id}", response_model=CollectionOut)
def get_collection(collection_id: int, collections: CollectionService = Depends(get_collections)) -> CollectionOut:
    record = collections.get_record(collection_id)
    markdown, document = collections.load_content(record)
    downloads = (
        {fmt: f"/api/document-collections/{record.id}/download/{fmt}" for fmt in MEDIA_TYPES} if markdown is not None else {}
    )
    # Visible sources: the order in which they first appear in the consolidated document (never the input order).
    source_order = [s.video_id for s in document.sources] if document else None
    return CollectionOut(
        **collection_summary_from_record(record, source_order),
        progress=record.progress,
        prompt_version=record.prompt_version,
        model=record.model,
        usage=record.usage,
        markdown=markdown,
        document=document,
        downloads=downloads,
    )


@router.get("/document-collections/{collection_id}/download/{fmt}")
def download_collection(collection_id: int, fmt: str, collections: CollectionService = Depends(get_collections)) -> FileResponse:
    if fmt not in MEDIA_TYPES:
        raise AppError(ErrorCode.INVALID_REQUEST, "Formato no soportado.")
    record, path = collections.file_for(collection_id, fmt)
    media_type, ext = MEDIA_TYPES[fmt]
    type_label = DOCUMENT_TYPE_LABELS["en"][DocumentType(record.document_type)]
    stem = sanitize_filename(f"{record.title or 'Collection'} - {type_label}")
    return FileResponse(path, media_type=media_type, filename=f"{stem}.{ext}")
