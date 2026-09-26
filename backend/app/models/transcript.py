"""Internal transcript model. Every provider normalises its output to these classes."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field, computed_field


class TranscriptSegment(BaseModel):
    text: str
    start: float
    duration: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def end(self) -> float:
        return round(self.start + self.duration, 3)


class TranscriptInfo(BaseModel):
    """Describes one transcript track available for a video (without its content)."""

    language: str
    language_code: str
    is_generated: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_manual(self) -> bool:
        return not self.is_generated

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> str:
        return "generated" if self.is_generated else "manual"


class Transcript(BaseModel):
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: list[TranscriptSegment] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_manual(self) -> bool:
        return not self.is_generated

    @property
    def info(self) -> TranscriptInfo:
        return TranscriptInfo(language=self.language, language_code=self.language_code, is_generated=self.is_generated)

    @property
    def duration(self) -> float:
        return max((s.end for s in self.segments), default=0.0)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    def content_hash(self) -> str:
        """Stable hash of the transcript content (used by the document cache)."""
        payload = json.dumps(
            {
                "language_code": self.language_code,
                "is_generated": self.is_generated,
                "segments": [[s.text, round(s.start, 3), round(s.duration, 3)] for s in self.segments],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VideoMetadata(BaseModel):
    video_id: str
    url: str
    title: str | None = None
    channel: str | None = None
    thumbnail: str | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str
    detail: str | None = None


class InspectionResult(BaseModel):
    valid_url: bool = True
    video: VideoMetadata
    transcript_available: bool
    selected_transcript: TranscriptInfo | None = None
    available_transcripts: list[TranscriptInfo] = Field(default_factory=list)
    transcript_error: ErrorInfo | None = None


class BatchVideoInspection(InspectionResult):
    """Inspection of one video of a multi-URL batch. A failure here never affects the other videos."""

    position: int  # among the unique videos, in paste order
    input: str
    duration_seconds: float | None = None  # only known when the transcript is already stored locally
    error: ErrorInfo | None = None


class BatchLine(BaseModel):
    position: int  # among the non-empty pasted lines
    input: str
    video_id: str | None = None
    duplicate_of: int | None = None
    error: ErrorInfo | None = None


class BatchInspectionResult(BaseModel):
    videos: list[BatchVideoInspection]
    invalid: list[BatchLine]
    duplicates: list[BatchLine]
    valid_count: int
    available_count: int
    max_videos: int
