"""AI processor abstraction used by the rest of the application."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError, ErrorCode
from app.models.document import Citation, DocumentSection, DocumentType, KeyPoint
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.services.ai.evidence import EvidenceItem, EvidenceStore

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class AIResult:
    title: str
    summary: str
    sections: list[DocumentSection]
    key_points: list[KeyPoint]
    usage: AIUsage
    prompt_versions: dict[str, str]
    model: str
    verification_notes: list[str] = field(default_factory=list)


@dataclass
class ExtractionOutcome:
    evidence: list[EvidenceItem]
    cached: bool
    cache_key: str


@dataclass
class VideoEvidence:
    """One source of a consolidated document: its video, transcript and structured extraction."""

    video: VideoMetadata
    transcript: Transcript
    evidence: list[EvidenceItem]


@dataclass
class CollectionAIResult:
    title: str
    summary: str
    sections: list[DocumentSection]
    key_points: list[KeyPoint]
    citations: list[Citation]
    usage: AIUsage
    prompt_versions: dict[str, str]
    model: str
    verification_notes: list[str] = field(default_factory=list)


def _unsupported() -> AppError:
    return AppError(ErrorCode.AI_PROCESSING_FAILED, "El procesador de IA configurado no admite documentos consolidados.")


class AIProcessor(ABC):
    evidence_store: EvidenceStore | None = None

    @abstractmethod
    def config_fingerprint(self, document_type: DocumentType) -> str:
        """Everything about the AI configuration that changes the output (model, prompts, flags)."""

    @abstractmethod
    def process(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        refresh_extraction: bool = False,
    ) -> AIResult:
        """`refresh_extraction`: ignore a cached extraction (explicit "regenerate without cache")."""

    # ---- optional capabilities (multi-video). Processors that don't support them fail clearly.

    def attach_evidence_store(self, store: EvidenceStore) -> None:
        """Persistent cache for per-video extractions (stage A)."""
        self.evidence_store = store

    def collection_fingerprint(self, document_type: DocumentType) -> str:
        raise _unsupported()

    def extract(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        usage: AIUsage | None = None,
        refresh: bool = False,
    ) -> ExtractionOutcome:
        raise _unsupported()

    def consolidate(
        self,
        sources: Sequence[VideoEvidence],
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        usage: AIUsage | None = None,
    ) -> CollectionAIResult:
        raise _unsupported()
