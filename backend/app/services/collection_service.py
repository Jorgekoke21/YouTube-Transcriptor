"""Application service for multi-video batches.

- Batch inspection: every video is inspected independently; one invalid URL or one video without transcript
  never prevents inspecting the others.
- Document collections (`output_mode`):
  · individual: one `ProcessedDocument` per video, produced by exactly the existing single-video flow
    (`DocumentService.create` + `DocumentService.run`).
  · consolidated: one `ConsolidatedDocument` built from the per-video extractions (reused from the cache
    whenever possible), consolidated, written and verified (see `services/ai/collection.py`).
  · both: the individual documents first (they fill the extraction cache), then the consolidated one.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.document import ConsolidatedDocument, DocumentType
from app.models.transcript import (
    BatchInspectionResult,
    BatchLine,
    BatchVideoInspection,
    ErrorInfo,
    Transcript,
    VideoMetadata,
)
from app.models.usage import AIUsage
from app.prompts import prompt_version_string
from app.prompts.collection import COLLECTION_PROMPT_VERSION
from app.repositories.collections import CollectionRecord, CollectionRepository, CollectionVideoRecord
from app.services.ai.base import AIProcessor, VideoEvidence
from app.services.cache import compute_collection_key
from app.services.document_service import FORMAT_COLUMNS, DocumentService
from app.services.documents.builder import build_consolidated_document, write_document_files
from app.utils.youtube_url import BatchEntry, canonical_url, parse_url_batch

logger = logging.getLogger(__name__)

OUTPUT_MODES = ("individual", "consolidated", "both")
INSPECT_WORKERS = 4
# Errors that mean "this video cannot be used" (as opposed to a global failure such as the AI being down).
VIDEO_ERRORS = {
    ErrorCode.INVALID_YOUTUBE_URL.value,
    ErrorCode.VIDEO_NOT_FOUND.value,
    ErrorCode.TRANSCRIPT_NOT_AVAILABLE.value,
    ErrorCode.TRANSCRIPT_DISABLED.value,
    ErrorCode.TRANSCRIPT_FETCH_FAILED.value,
}


def _error_info(err: AppError) -> ErrorInfo:
    return ErrorInfo(**err.to_dict())


class CollectionService:
    def __init__(self, documents: DocumentService, repo: CollectionRepository) -> None:
        self.documents = documents
        self.repo = repo

    @property
    def settings(self) -> Settings:
        return self.documents.settings

    @property
    def ai(self) -> AIProcessor:
        return self.documents.ai

    def _check_size(self, count: int) -> None:
        limit = self.settings.max_videos_per_batch
        if count > limit:
            raise AppError(
                ErrorCode.BATCH_TOO_LARGE,
                f"Has incluido {count} vídeos distintos; el máximo por lote es {limit} (MAX_VIDEOS_PER_BATCH).",
            )

    # ------------------------------------------------------------------ inspect batch

    def inspect_batch(self, urls: list[str]) -> BatchInspectionResult:
        batch = parse_url_batch(urls)
        unique = batch.unique
        self._check_size(len(unique))
        with ThreadPoolExecutor(max_workers=INSPECT_WORKERS) as pool:
            videos = list(pool.map(self._inspect_one, range(len(unique)), unique))
        invalid_error = _error_info(AppError(ErrorCode.INVALID_YOUTUBE_URL))
        return BatchInspectionResult(
            videos=videos,
            invalid=[BatchLine(position=e.position, input=e.input, error=invalid_error) for e in batch.invalid],
            duplicates=[
                BatchLine(position=e.position, input=e.input, video_id=e.video_id, duplicate_of=e.duplicate_of)
                for e in batch.duplicates
            ],
            valid_count=len(unique),
            available_count=sum(v.transcript_available for v in videos),
            max_videos=self.settings.max_videos_per_batch,
        )

    def _inspect_one(self, position: int, entry: BatchEntry) -> BatchVideoInspection:
        assert entry.video_id is not None
        base = {"position": position, "input": entry.input, "duration_seconds": self._stored_duration(entry.video_id)}
        try:
            result = self.documents.inspect(entry.input)
            return BatchVideoInspection(**result.model_dump(), **base, error=result.transcript_error)
        except AppError as err:
            info = _error_info(err)
        except Exception:
            logger.exception("Unexpected error inspecting %s", entry.video_id)
            info = _error_info(AppError(ErrorCode.INTERNAL_ERROR))
        video = VideoMetadata(video_id=entry.video_id, url=canonical_url(entry.video_id))
        return BatchVideoInspection(video=video, transcript_available=False, transcript_error=info, error=info, **base)

    def _stored_duration(self, video_id: str) -> float | None:
        path = self.settings.videos_dir / video_id / "transcript.json"
        if not path.is_file():
            return None
        try:
            return round(Transcript.model_validate_json(path.read_text(encoding="utf-8")).duration, 1)
        except ValueError:
            return None

    # ------------------------------------------------------------------ create

    def create(self, urls: list[str], document_type: DocumentType, output_language: str, output_mode: str) -> CollectionRecord:
        if output_mode not in OUTPUT_MODES:
            raise AppError(ErrorCode.INVALID_REQUEST, "Modo de salida no soportado.")
        batch = parse_url_batch(urls)
        if batch.invalid:
            raise AppError(ErrorCode.INVALID_YOUTUBE_URL, f"URL no válida: {batch.invalid[0].input[:200]}")
        entries = batch.unique
        if not entries:
            raise AppError(ErrorCode.INVALID_REQUEST, "No se ha indicado ningún vídeo.")
        self._check_size(len(entries))
        if output_mode != "individual":
            if len(entries) < 2:
                raise AppError(ErrorCode.NOT_ENOUGH_VIDEOS, "Selecciona al menos dos vídeos para un documento consolidado.")
            if document_type == DocumentType.CLEAN_TRANSCRIPT:
                raise AppError(
                    ErrorCode.INVALID_REQUEST,
                    "La transcripción limpia no admite documento consolidado: usa documentos individuales.",
                )

        def register(entry: BatchEntry) -> tuple[int, str, int | None]:
            assert entry.video_id is not None
            if output_mode != "consolidated":
                # Exactly the single-video flow: one regular document per video.
                record = self.documents.create(canonical_url(entry.video_id), document_type, output_language)
                return record.video_db_id, entry.input, record.id
            meta = self.documents.metadata.get(entry.video_id)
            video_db_id = self.documents.repo.upsert_video(entry.video_id, meta.url, meta.title, meta.channel, meta.thumbnail)
            return video_db_id, entry.input, None

        with ThreadPoolExecutor(max_workers=INSPECT_WORKERS) as pool:
            rows = list(pool.map(register, entries))
        collection_id = self.repo.create(document_type.value, output_language, output_mode, rows)
        return self.get_record(collection_id)

    # ------------------------------------------------------------------ run

    def run(self, collection_id: int, force_regenerate: bool = False) -> None:
        """Process a collection end to end. Designed to run in a background task."""
        record = self.repo.get(collection_id)
        if record is None:
            return
        try:
            self._run(record, force_regenerate)
        except AppError as err:
            logger.info("Collection %s failed: %s", collection_id, err.code)
            self.repo.update(collection_id, status="failed", error_code=err.code.value, error_message=err.message)
        except Exception:
            logger.exception("Unexpected error processing collection %s", collection_id)
            self.repo.update(
                collection_id,
                status="failed",
                error_code=ErrorCode.INTERNAL_ERROR.value,
                error_message=AppError(ErrorCode.INTERNAL_ERROR).message,
            )

    def _run(self, rec: CollectionRecord, force_regenerate: bool) -> None:
        cid = rec.id
        self.repo.merge_progress(cid, {"videos_total": len(rec.videos)})
        if rec.wants_individual:
            self._run_individual(rec, force_regenerate)
            if not rec.wants_consolidated:
                self._finish_individual(cid)
                return
        self._run_consolidated(cid, force_regenerate)

    # ---- individual documents (existing pipeline, untouched)

    def _run_individual(self, rec: CollectionRecord, force_regenerate: bool) -> None:
        cid = rec.id
        total = sum(v.document_id is not None for v in rec.videos)
        self.repo.update(cid, status="processing")
        self.repo.merge_progress(cid, {"phase": "individual", "individual_total": total, "individual_done": 0})
        done = 0
        for video in rec.videos:
            if video.document_id is None:
                continue
            self.repo.merge_progress(cid, {"current_video": video.position})
            self.documents.run(video.document_id, force_regenerate)
            doc = self.documents.repo.get(video.document_id)
            if doc is not None and doc.status == "failed":
                self.repo.update_video(
                    cid, video.position, status="failed", error_code=doc.error_code, error_message=doc.error_message
                )
            else:
                self.repo.update_video(cid, video.position, status="document_ready")
            done += 1
            self.repo.merge_progress(cid, {"individual_done": done})

    def _finish_individual(self, cid: int) -> None:
        rec = self.get_record(cid)
        failed = [v for v in rec.videos if v.status == "failed"]
        if len(failed) == len(rec.videos):
            first = failed[0]
            self.repo.update(
                cid,
                status="failed",
                error_code=first.error_code or ErrorCode.INTERNAL_ERROR.value,
                error_message="No se ha podido generar ninguno de los documentos."
                + (f" {first.error_message}" if first.error_message else ""),
            )
            return
        self.repo.merge_progress(cid, {"phase": "done", "current_video": None})
        self.repo.update(cid, status="completed")

    # ---- consolidated document

    def _run_consolidated(self, cid: int, force_regenerate: bool) -> None:
        rec = self.get_record(cid)
        document_type = DocumentType(rec.document_type)
        lang = rec.output_language

        # 1. Transcripts (stored ones are reused: no new YouTube request after the individual documents).
        self.repo.update(cid, status="fetching_transcript")
        self.repo.merge_progress(cid, {"phase": "transcripts", "videos_ready": 0, "current_video": None})
        ready: list[tuple[CollectionVideoRecord, VideoMetadata, Transcript]] = []
        for video in rec.videos:
            if video.status == "failed" and video.error_code in VIDEO_ERRORS:
                continue  # its individual document already proved it has no usable transcript
            self.repo.merge_progress(cid, {"current_video": video.position})
            try:
                meta = self.documents.refresh_metadata(video.youtube_id)
                transcript = self.documents.load_or_fetch_transcript(video.youtube_id)
            except AppError as err:
                self.repo.update_video(cid, video.position, status="failed", error_code=err.code.value, error_message=err.message)
                continue
            kind = "generated" if transcript.is_generated else "manual"
            self.documents.repo.upsert_video(
                video.youtube_id, meta.url, transcript_language=transcript.language, transcript_type=kind
            )
            self.repo.update_video(
                cid,
                video.position,
                status="transcript_ready",
                transcript_hash=transcript.content_hash(),
                error_code=None,
                error_message=None,
            )
            ready.append((video, meta, transcript))
            self.repo.merge_progress(cid, {"videos_ready": len(ready)})
        if len(ready) < 2:
            raise AppError(
                ErrorCode.NOT_ENOUGH_VIDEOS,
                f"Solo {len(ready)} de {len(rec.videos)} vídeos tienen una transcripción utilizable; "
                "un documento consolidado necesita al menos dos.",
            )

        # 2. Collection cache.
        prompt_version = f"{prompt_version_string(document_type)}+{COLLECTION_PROMPT_VERSION}"
        cache_key = compute_collection_key(
            [(v.youtube_id, t.content_hash()) for v, _, t in ready],
            document_type.value,
            lang,
            prompt_version,
            self.ai.collection_fingerprint(document_type),
        )
        self.repo.update(cid, cache_key=cache_key, prompt_version=prompt_version)
        if not force_regenerate and self._reuse_cached(cid, cache_key, ready):
            return

        # 3. Per-video extraction (cache first), then consolidation → writer → verification.
        stages = ["extracting", "consolidating", "writing"] + (["verifying"] if self.settings.ai_enable_verification else [])
        self.repo.update(cid, status="processing")
        self.repo.merge_progress(
            cid,
            {
                "phase": "consolidated",
                "stages": stages,
                "stage": "extracting",
                "stage_done": 0,
                "stage_total": len(ready),
                "extraction_cache_hits": 0,
            },
        )
        usage = AIUsage()
        sources: list[VideoEvidence] = []
        hits = 0
        for n, (video, meta, transcript) in enumerate(ready, 1):
            self.repo.merge_progress(cid, {"current_video": video.position})
            # A forced regeneration of the collection re-extracts, except videos just (re)extracted by their
            # individual document in this same run.
            refresh = force_regenerate and not (rec.wants_individual and video.document_id is not None)
            outcome = self.ai.extract(transcript, meta, document_type, lang, None, usage, refresh)
            hits += int(outcome.cached)
            self.repo.update_video(
                cid, video.position, status="extracted", extraction_key=outcome.cache_key, extraction_cached=int(outcome.cached)
            )
            sources.append(VideoEvidence(video=meta, transcript=transcript, evidence=outcome.evidence))
            self.repo.merge_progress(cid, {"stage_done": n, "extraction_cache_hits": hits})

        def on_progress(data: dict[str, Any]) -> None:
            update: dict[str, Any] = {"stage": data.get("stage"), "current_video": None}
            if "done" in data:
                update |= {"stage_done": data["done"], "stage_total": data["total"]}
            else:
                update |= {"stage_done": None, "stage_total": None}
            self.repo.merge_progress(cid, update)

        result = self.ai.consolidate(sources, document_type, lang, on_progress, usage)
        usage.model = result.model
        self.repo.update(cid, model=result.model, usage=usage.model_dump())

        # 4. Files.
        self.repo.update(cid, status="generating_files")
        self.repo.merge_progress(cid, {"stage": "files", "stage_done": None, "stage_total": None})
        document = build_consolidated_document(result, [(m, t) for _, m, t in ready], document_type, lang)
        files = write_document_files(document, self.settings.collections_dir / str(cid))
        for video, _, _ in ready:
            self.repo.update_video(cid, video.position, status="included")
        self.repo.update(
            cid,
            status="completed",
            title=document.title,
            markdown_path=str(files.markdown),
            docx_path=str(files.docx),
            pdf_path=str(files.pdf),
            txt_path=str(files.txt),
            processed_path=str(files.processed_json),
        )

    def _reuse_cached(
        self, cid: int, cache_key: str, ready: list[tuple[CollectionVideoRecord, VideoMetadata, Transcript]]
    ) -> bool:
        cached = self.repo.find_completed_by_cache_key(cache_key, exclude_id=cid)
        if cached is None or not all(p and Path(p).is_file() for p in (cached.markdown_path, cached.processed_path)):
            return False
        logger.info("Collection %s reuses cached consolidated document of collection %s", cid, cached.id)
        self.repo.merge_progress(cid, {"cache_hit": True, "stage": "cached", "current_video": None})
        for video, _, _ in ready:
            self.repo.update_video(cid, video.position, status="included")
        self.repo.update(
            cid,
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

    def get_record(self, collection_id: int) -> CollectionRecord:
        record = self.repo.get(collection_id)
        if record is None:
            raise AppError(ErrorCode.COLLECTION_NOT_FOUND)
        return record

    def list_recent(self, limit: int = 20) -> list[CollectionRecord]:
        return self.repo.list_recent(limit)

    def load_content(self, record: CollectionRecord) -> tuple[str | None, ConsolidatedDocument | None]:
        if record.status != "completed" or not record.markdown_path:
            return None, None
        md_path, json_path = Path(record.markdown_path), Path(record.processed_path or "")
        markdown = md_path.read_text(encoding="utf-8") if md_path.is_file() else None
        document = (
            ConsolidatedDocument.model_validate_json(json_path.read_text(encoding="utf-8")) if json_path.is_file() else None
        )
        return markdown, document

    def file_for(self, collection_id: int, fmt: str) -> tuple[CollectionRecord, Path]:
        record = self.get_record(collection_id)
        if record.status != "completed" or not record.wants_consolidated:
            raise AppError(ErrorCode.DOCUMENT_NOT_READY)
        column = FORMAT_COLUMNS.get(fmt)
        if column is None:
            raise AppError(ErrorCode.INVALID_REQUEST, "Formato no soportado.")
        raw = getattr(record, column)
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND, "El archivo del documento no existe.")
        if not path.resolve().is_relative_to(self.settings.resolved_data_dir.resolve()):
            raise AppError(ErrorCode.DOCUMENT_NOT_FOUND)
        return record, path
