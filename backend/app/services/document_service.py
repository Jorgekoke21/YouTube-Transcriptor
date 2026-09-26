"""Application service: orchestrates URL → transcript → AI → files → persistence.

Endpoints only call this service; they never touch providers or OpenAI directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.document import DocumentType, ProcessedDocument
from app.models.transcript import ErrorInfo, InspectionResult, Transcript, VideoMetadata
from app.prompts import prompt_version_string
from app.repositories.documents import DocumentRecord, DocumentRepository
from app.repositories.extractions import ExtractionRepository
from app.services.ai.base import AIProcessor
from app.services.cache import compute_cache_key
from app.services.documents.builder import build_processed_document, write_document_files
from app.services.evidence_cache import FileEvidenceStore
from app.services.metadata import MetadataProvider
from app.services.transcripts.base import TranscriptProvider, select_transcript
from app.utils.youtube_url import canonical_url, is_valid_video_id, require_video_id

logger = logging.getLogger(__name__)

FORMAT_COLUMNS = {
    "markdown": "markdown_path",
    "docx": "docx_path",
    "pdf": "pdf_path",
    "txt": "txt_path",
}


class DocumentService:
    def __init__(
        self,
        settings: Settings,
        repo: DocumentRepository,
        transcripts: TranscriptProvider,
        metadata: MetadataProvider,
        ai: AIProcessor,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.transcripts = transcripts
        self.metadata = metadata
        self.ai = ai
        # Per-video extraction cache, shared with consolidated (multi-video) documents.
        self.evidence_store = FileEvidenceStore(ExtractionRepository(repo.db), settings.videos_dir)
        ai.attach_evidence_store(self.evidence_store)

    # ------------------------------------------------------------------ storage paths

    def _video_dir(self, video_id: str) -> Path:
        if not is_valid_video_id(video_id):  # defence in depth: ids become directory names
            raise AppError(ErrorCode.INVALID_YOUTUBE_URL)
        return self.settings.videos_dir / video_id

    def _save_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ inspect

    def inspect(self, url: str) -> InspectionResult:
        video_id = require_video_id(url)
        meta = self.metadata.get(video_id)
        try:
            available = self.transcripts.inspect(video_id)
        except AppError as err:
            if err.code in (ErrorCode.TRANSCRIPT_DISABLED, ErrorCode.TRANSCRIPT_NOT_AVAILABLE):
                return InspectionResult(video=meta, transcript_available=False, transcript_error=ErrorInfo(**err.to_dict()))
            raise
        selected = select_transcript(available, self.settings.transcript_language_list)
        return InspectionResult(
            video=meta,
            transcript_available=selected is not None,
            selected_transcript=selected,
            available_transcripts=available,
            transcript_error=None if selected else ErrorInfo(**AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE).to_dict()),
        )

    # ------------------------------------------------------------------ transcript

    def fetch_transcript(self, video_id: str, on_video_confirmed: Callable[[], None] | None = None) -> Transcript:
        """Select and fetch the preferred transcript.

        `on_video_confirmed` is called as soon as YouTube confirms the video exists (its transcript list was
        readable, or it exists but has no/disabled transcripts), so progress never claims a missing video was found.
        """
        try:
            available = self.transcripts.inspect(video_id)
        except AppError as err:
            if on_video_confirmed and err.code in (ErrorCode.TRANSCRIPT_DISABLED, ErrorCode.TRANSCRIPT_NOT_AVAILABLE):
                on_video_confirmed()
            raise
        if on_video_confirmed:
            on_video_confirmed()
        selected = select_transcript(available, self.settings.transcript_language_list)
        if selected is None:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE)
        transcript = self.transcripts.fetch(video_id, selected)
        if not transcript.segments:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE)
        self._save_json(self._video_dir(video_id) / "transcript.json", transcript.model_dump(mode="json"))
        return transcript

    def load_or_fetch_transcript(self, video_id: str) -> Transcript:
        """The stored transcript when there is one (no YouTube request), otherwise fetch and store it."""
        stored = self._video_dir(video_id) / "transcript.json"
        if stored.is_file():
            return Transcript.model_validate_json(stored.read_text(encoding="utf-8"))
        return self.fetch_transcript(video_id)

    def refresh_metadata(self, video_id: str) -> VideoMetadata:
        """Best-effort metadata, persisted in metadata.json and the `videos` table."""
        meta = self.metadata.get(video_id)
        self._save_json(self._video_dir(video_id) / "metadata.json", meta.model_dump(mode="json"))
        self.repo.upsert_video(video_id, meta.url, meta.title, meta.channel, meta.thumbnail)
        return meta

    def get_transcript(self, video_id: str) -> tuple[Transcript, VideoMetadata | None]:
        video_dir = self._video_dir(video_id)
        stored = video_dir / "transcript.json"
        transcript = (
            Transcript.model_validate_json(stored.read_text(encoding="utf-8"))
            if stored.exists()
            else self.fetch_transcript(video_id)
        )
        meta_path = video_dir / "metadata.json"
        meta = VideoMetadata.model_validate_json(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None
        return transcript, meta

    # ------------------------------------------------------------------ create & run

    def create(self, url: str, document_type: DocumentType, output_language: str) -> DocumentRecord:
        video_id = require_video_id(url)
        # Best-effort and fast (oEmbed): lets the progress screen show title/thumbnail right away.
        meta = self.metadata.get(video_id)
        video_db_id = self.repo.upsert_video(video_id, canonical_url(video_id), meta.title, meta.channel, meta.thumbnail)
        doc_id = self.repo.create_document(video_db_id, document_type.value, output_language)
        record = self.repo.get(doc_id)
        assert record is not None
        return record

    def run(self, document_id: int, force_regenerate: bool = False) -> None:
        """Process a document end to end. Designed to run in a background task."""
        record = self.repo.get(document_id)
        if record is None:
            return
        try:
            self._run(record, force_regenerate)
        except AppError as err:
            logger.info("Document %s failed: %s", document_id, err.code)
            self.repo.update(document_id, status="failed", error_code=err.code.value, error_message=err.message)
        except Exception:
            logger.exception("Unexpected error processing document %s", document_id)
            self.repo.update(
                document_id,
                status="failed",
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_message=AppError(ErrorCode.INTERNAL_ERROR).message,
            )

    def _run(self, record: DocumentRecord, force_regenerate: bool) -> None:
        doc_id = record.id
        video_id = record.youtube_id
        document_type = DocumentType(record.document_type)
        lang = record.output_language

        # 1. Video + transcript
        self.repo.update(doc_id, status="fetching_transcript")
        meta = self.metadata.get(video_id)
        self._save_json(self._video_dir(video_id) / "metadata.json", meta.model_dump(mode="json"))
        self.repo.upsert_video(video_id, meta.url, meta.title, meta.channel, meta.thumbnail)
        transcript = self.fetch_transcript(
            video_id, on_video_confirmed=lambda: self.repo.merge_progress(doc_id, {"video_found": True})
        )
        transcript_type = "generated" if transcript.is_generated else "manual"
        self.repo.upsert_video(video_id, meta.url, transcript_language=transcript.language, transcript_type=transcript_type)
        self.repo.merge_progress(
            doc_id,
            {
                "transcript_found": True,
                "transcript_language": transcript.language,
                "transcript_language_code": transcript.language_code,
                "transcript_type": transcript_type,
                "word_count": transcript.word_count,
                "segment_count": len(transcript.segments),
                "duration_seconds": round(transcript.duration, 1),
            },
        )

        # 2. Cache
        prompt_version = prompt_version_string(document_type)
        cache_key = compute_cache_key(
            transcript.content_hash(), document_type.value, lang, prompt_version, self.ai.config_fingerprint(document_type)
        )
        self.repo.update(doc_id, cache_key=cache_key, prompt_version=prompt_version)
        if not force_regenerate and self._reuse_cached(doc_id, cache_key):
            return

        # 3. AI processing
        self.repo.update(doc_id, status="processing")
        self.repo.merge_progress(doc_id, {"stages": self._planned_stages(document_type)})

        def on_progress(data: dict[str, Any]) -> None:
            update = {"stage": data.get("stage")}
            if data.get("extraction_cached"):
                update["extraction_cached"] = True
            if "done" in data:
                update |= {"stage_done": data["done"], "stage_total": data["total"]}
            else:
                update |= {"stage_done": None, "stage_total": None}
            self.repo.merge_progress(doc_id, update)

        result = self.ai.process(transcript, meta, document_type, lang, on_progress, refresh_extraction=force_regenerate)
        self.repo.update(doc_id, model=result.model, usage=result.usage.model_dump())

        # 4. Files
        self.repo.update(doc_id, status="generating_files")
        self.repo.merge_progress(doc_id, {"stage": "files", "stage_done": None, "stage_total": None})
        processed = build_processed_document(result, meta, transcript, document_type, lang)
        out_dir = self._video_dir(video_id) / "documents" / f"{doc_id}-{document_type.value}-{lang}"
        files = write_document_files(processed, out_dir)
        self.repo.update(
            doc_id,
            status="completed",
            title=processed.title,
            markdown_path=str(files.markdown),
            docx_path=str(files.docx),
            pdf_path=str(files.pdf),
            txt_path=str(files.txt),
            processed_path=str(files.processed_json),
        )

    def _planned_stages(self, document_type: DocumentType) -> list[str]:
        if document_type == DocumentType.CLEAN_TRANSCRIPT:
            return ["cleaning"]
        stages = ["extracting", "outlining", "writing"]
        if self.settings.ai_enable_verification:
            stages.append("verifying")
        return stages

    def _reuse_cached(self, doc_id: int, cache_key: str) -> bool:
        cached = self.repo.find_completed_by_cache_key(cache_key, exclude_id=doc_id)
        if cached is None or not all(p and Path(p).exists() for p in (cached.markdown_path, cached.processed_path)):
            return False
        logger.info("Document %s reuses cached document %s", doc_id, cached.id)
        self.repo.merge_progress(doc_id, {"cache_hit": True, "stage": "cached"})
        self.repo.update(
            doc_id,
            status="completed",
            reused_from_id=cached.id,
            title=cached.title,
            model=cached.model,
            usage=cached.usage,
            markdown_path=cached.markdown_path,
            docx_path=cached.docx_path,
            pdf_path=cached.pdf_path,
            txt_path=cached.txt_path,
            processed_path=cached.processed_path,
        )
        return True

    # ------------------------------------------------------------------ read

    def get_record(self, document_id: int) -> DocumentRecord:
        record = self.repo.get(document_id)
        if record is None:
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND)
        return record

    def load_content(self, record: DocumentRecord) -> tuple[str | None, ProcessedDocument | None]:
        if record.status != "completed" or not record.markdown_path:
            return None, None
        md_path, json_path = Path(record.markdown_path), Path(record.processed_path or "")
        markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else None
        processed = ProcessedDocument.model_validate_json(json_path.read_text(encoding="utf-8")) if json_path.is_file() else None
        return markdown, processed

    def file_for(self, document_id: int, fmt: str) -> tuple[DocumentRecord, Path]:
        record = self.get_record(document_id)
        if record.status != "completed":
            raise AppError(ErrorCode.DOCUMENT_NOT_READY)
        column = FORMAT_COLUMNS.get(fmt)
        if column is None:
            raise AppError(ErrorCode.INVALID_REQUEST, "Formato no soportado.")
        raw = getattr(record, column)
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "El archivo del documento no existe.")
        # Only serve files that live inside the data directory.
        data_dir = self.settings.resolved_data_dir.resolve()
        if not path.resolve().is_relative_to(data_dir):
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND)
        return record, path

    def list_recent(self, limit: int = 20) -> list[DocumentRecord]:
        return self.repo.list_recent(limit)
