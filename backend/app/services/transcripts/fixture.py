"""Offline transcript provider backed by JSON fixtures (development & tests)."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.errors import AppError, ErrorCode
from app.models.transcript import Transcript, TranscriptInfo
from app.services.transcripts.base import TranscriptProvider, clean_language_name, normalize_segments

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "transcript_divergencias.json"


class FixtureTranscriptProvider(TranscriptProvider):
    """Returns the same fixture transcript for any video id.

    Video ids starting with "NOTRANSCR" simulate a video without transcript, and
    ids starting with "DISABLED" simulate disabled transcripts.
    """

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE) -> None:
        self.fixture_path = fixture_path

    def _load(self) -> dict:
        return json.loads(self.fixture_path.read_text(encoding="utf-8"))

    def inspect(self, video_id: str) -> list[TranscriptInfo]:
        if video_id.startswith("NOTRANSCR"):
            return []
        if video_id.startswith("DISABLED"):
            raise AppError(ErrorCode.TRANSCRIPT_DISABLED)
        data = self._load()
        return [
            TranscriptInfo(
                language=clean_language_name(data["language"]),
                language_code=data["language_code"],
                is_generated=data["is_generated"],
            )
        ]

    def fetch(self, video_id: str, track: TranscriptInfo) -> Transcript:
        if not self.inspect(video_id):
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE)
        data = self._load()
        return Transcript(
            video_id=video_id,
            language=clean_language_name(data["language"]),
            language_code=data["language_code"],
            is_generated=data["is_generated"],
            segments=normalize_segments(data["segments"]),
        )
