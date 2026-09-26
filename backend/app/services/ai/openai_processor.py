"""OpenAI implementation of `AIProcessor`."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.document import DocumentType
from app.models.transcript import Transcript, VideoMetadata
from app.models.usage import AIUsage
from app.prompts import get_prompt_versions, prompt_version_string
from app.prompts.collection import COLLECTION_PROMPT_VERSION
from app.services.ai.base import AIProcessor, AIResult, CollectionAIResult, ExtractionOutcome, ProgressCallback, VideoEvidence
from app.services.ai.collection import CollectionPipeline
from app.services.ai.evidence import ExtractionMeta, compute_extraction_key
from app.services.ai.llm import OpenAIStructuredLLM, StructuredLLM
from app.services.ai.pipeline import DocumentPipeline, PipelineConfig

logger = logging.getLogger(__name__)


def pipeline_config_from_settings(settings: Settings) -> PipelineConfig:
    return PipelineConfig(
        target_words=settings.chunk_target_words,
        max_words=settings.chunk_max_words,
        enable_verification=settings.ai_enable_verification,
    )


class PipelineAIProcessor(AIProcessor):
    """Runs the multi-stage pipeline on top of any `StructuredLLM`."""

    def __init__(self, llm: StructuredLLM, config: PipelineConfig, provider_name: str) -> None:
        self.llm = llm
        self.config = config
        self.provider_name = provider_name

    def config_fingerprint(self, document_type: DocumentType) -> str:
        return (
            f"{self.provider_name}:{self.llm.fingerprint}|{prompt_version_string(document_type)}"
            f"|verify={int(self.config.enable_verification)}|chunk={self.config.target_words}/{self.config.max_words}"
        )

    def extraction_fingerprint(self, document_type: DocumentType, output_language: str) -> str:
        """Everything that changes stage A's output: provider, model + reasoning, prompts, language, chunking."""
        v = get_prompt_versions(document_type)
        return (
            f"{self.provider_name}:{self.llm.fingerprint}|{v['base']}+{v['pipeline']}+{v['mode']}"
            f"|lang={output_language}|chunk={self.config.target_words}/{self.config.max_words}"
        )

    def collection_fingerprint(self, document_type: DocumentType) -> str:
        return f"{self.config_fingerprint(document_type)}|{COLLECTION_PROMPT_VERSION}"

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
        """Stage A for one video, reusing a compatible cached extraction when there is one (unless `refresh`)."""
        progress = on_progress or (lambda _data: None)
        usage = usage or AIUsage(model=self.llm.model)
        fingerprint = self.extraction_fingerprint(document_type, output_language)
        key = compute_extraction_key(transcript.video_id, transcript.content_hash(), fingerprint)
        store = self.evidence_store
        if store is not None and not refresh:
            cached = store.load(key)
            if cached:
                logger.info("Extraction cache hit for %s (%s items)", transcript.video_id, len(cached))
                progress({"stage": "extracting", "done": 1, "total": 1, "extraction_cached": True})
                return ExtractionOutcome(evidence=cached, cached=True, cache_key=key)
        evidence = DocumentPipeline(self.llm, self.config).extract(
            transcript, video, document_type, output_language, usage, progress
        )
        if store is not None and evidence:
            meta = ExtractionMeta(
                youtube_id=transcript.video_id,
                transcript_hash=transcript.content_hash(),
                document_type=document_type.value,
                output_language=output_language,
                prompt_version=prompt_version_string(document_type),
                model_config=fingerprint,
            )
            try:
                store.save(key, meta, evidence)
            except Exception:  # the cache is an optimisation: never fail a document because of it
                logger.exception("Could not store the extraction of %s", transcript.video_id)
        return ExtractionOutcome(evidence=evidence, cached=False, cache_key=key)

    def process(
        self,
        transcript: Transcript,
        video: VideoMetadata,
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        refresh_extraction: bool = False,
    ) -> AIResult:
        pipeline = DocumentPipeline(self.llm, self.config)
        if document_type == DocumentType.CLEAN_TRANSCRIPT:
            return pipeline.run(transcript, video, document_type, output_language, on_progress)
        usage = AIUsage(model=self.llm.model)
        outcome = self.extract(transcript, video, document_type, output_language, on_progress, usage, refresh_extraction)
        return pipeline.run(
            transcript, video, document_type, output_language, on_progress, evidence=outcome.evidence, usage=usage
        )

    def consolidate(
        self,
        sources: Sequence[VideoEvidence],
        document_type: DocumentType,
        output_language: str,
        on_progress: ProgressCallback | None = None,
        usage: AIUsage | None = None,
    ) -> CollectionAIResult:
        return CollectionPipeline(self.llm, self.config).run_collection(
            sources, document_type, output_language, on_progress, usage
        )


class OpenAIProcessor(PipelineAIProcessor):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._llm: StructuredLLM | None = None
        super().__init__(llm=_LazyLLM(self), config=pipeline_config_from_settings(settings), provider_name="openai")

    def build_llm(self) -> StructuredLLM:
        key = self._settings.openai_api_key.get_secret_value().strip() if self._settings.openai_api_key else ""
        if not key:
            raise AppError(ErrorCode.AI_NOT_CONFIGURED)
        if self._llm is None:
            self._llm = OpenAIStructuredLLM(
                api_key=key,
                model=self._settings.openai_model,
                timeout=self._settings.openai_timeout_seconds,
                max_retries=self._settings.openai_max_retries,
                reasoning=self._settings.openai_reasoning,
            )
        return self._llm

    @property
    def is_configured(self) -> bool:
        return bool(self._settings.openai_api_key and self._settings.openai_api_key.get_secret_value().strip())


class _LazyLLM(StructuredLLM):
    """Defers client creation until first use so the app starts without an API key."""

    def __init__(self, owner: OpenAIProcessor) -> None:
        self._owner = owner
        self.model = owner._settings.openai_model

    @property
    def fingerprint(self) -> str:
        # Same format as OpenAIStructuredLLM.fingerprint, without needing the API key.
        reasoning = self._owner._settings.openai_reasoning
        return f"{self.model}|" + ",".join(f"{k}={v}" for k, v in sorted(reasoning.items()))

    def parse(self, **kwargs):  # type: ignore[override]
        return self._owner.build_llm().parse(**kwargs)
