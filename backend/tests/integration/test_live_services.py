"""Optional integration tests against real services.

Skipped by default. Run with:
    RUN_INTEGRATION=1 pytest tests/integration            (YouTube only)
    RUN_INTEGRATION=1 OPENAI_API_KEY=... pytest tests/integration   (also OpenAI)
Set INTEGRATION_VIDEO_ID to a public video that has captions.
"""

from __future__ import annotations

import os

import pytest

from app.core.config import Settings
from app.models.document import DocumentType
from app.models.transcript import VideoMetadata
from app.services.ai.openai_processor import OpenAIProcessor
from app.services.metadata import OEmbedMetadataProvider
from app.services.transcripts.base import select_transcript
from app.services.transcripts.youtube import YouTubeTranscriptProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1 to call real services"),
]

VIDEO_ID = os.getenv("INTEGRATION_VIDEO_ID", "dQw4w9WgXcQ")


def test_youtube_transcript_provider_live() -> None:
    provider = YouTubeTranscriptProvider()
    tracks = provider.inspect(VIDEO_ID)
    assert tracks
    transcript = provider.fetch(VIDEO_ID, select_transcript(tracks, ["es", "en"]))
    assert transcript.segments and transcript.segments[0].end >= transcript.segments[0].start


def test_oembed_metadata_live() -> None:
    meta = OEmbedMetadataProvider().get(VIDEO_ID)
    assert meta.title


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_openai_pipeline_live() -> None:
    provider = YouTubeTranscriptProvider()
    transcript = provider.fetch(VIDEO_ID, select_transcript(provider.inspect(VIDEO_ID), ["es", "en"]))
    transcript.segments = transcript.segments[:60]  # keep the call cheap
    result = OpenAIProcessor(Settings()).process(
        transcript,
        VideoMetadata(video_id=VIDEO_ID, url=f"https://www.youtube.com/watch?v={VIDEO_ID}"),
        DocumentType.SUMMARY,
        "es",
    )
    assert result.sections and result.usage.calls >= 3
