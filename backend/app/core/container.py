"""Builds the application service graph from settings (simple manual DI)."""

from __future__ import annotations

from app.core.config import Settings
from app.repositories.database import Database
from app.repositories.documents import DocumentRepository
from app.services.ai.base import AIProcessor
from app.services.document_service import DocumentService
from app.services.metadata import MetadataProvider, OEmbedMetadataProvider, StaticMetadataProvider
from app.services.transcripts.base import TranscriptProvider


def build_transcript_provider(settings: Settings) -> TranscriptProvider:
    if settings.transcript_provider == "fixture":
        from app.services.transcripts.fixture import FixtureTranscriptProvider

        return FixtureTranscriptProvider()
    from app.services.transcripts.youtube import YouTubeTranscriptProvider, build_youtube_api

    return YouTubeTranscriptProvider(api=build_youtube_api(settings))


def build_metadata_provider(settings: Settings) -> MetadataProvider:
    return StaticMetadataProvider() if settings.transcript_provider == "fixture" else OEmbedMetadataProvider()


def build_ai_processor(settings: Settings) -> AIProcessor:
    if settings.ai_provider == "fake":
        from app.services.ai.fake import build_fake_processor
        from app.services.ai.openai_processor import pipeline_config_from_settings

        return build_fake_processor(delay=0.4, config=pipeline_config_from_settings(settings))
    from app.services.ai.openai_processor import OpenAIProcessor

    return OpenAIProcessor(settings)


def build_service(
    settings: Settings,
    transcripts: TranscriptProvider | None = None,
    metadata: MetadataProvider | None = None,
    ai: AIProcessor | None = None,
) -> DocumentService:
    repo = DocumentRepository(Database(settings.db_path))
    return DocumentService(
        settings=settings,
        repo=repo,
        transcripts=transcripts or build_transcript_provider(settings),
        metadata=metadata or build_metadata_provider(settings),
        ai=ai or build_ai_processor(settings),
    )
