"""`youtube-transcript-api` implementation of `TranscriptProvider`.

This is the ONLY module that imports `youtube_transcript_api`. If YouTube changes
its internals, replace or update this file only.
"""

from __future__ import annotations

import logging

from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)

from app.core.errors import AppError, ErrorCode
from app.models.transcript import Transcript, TranscriptInfo
from app.services.transcripts.base import TranscriptProvider, clean_language_name, normalize_segments

logger = logging.getLogger(__name__)


def _map_error(exc: Exception) -> AppError:
    if isinstance(exc, TranscriptsDisabled):
        return AppError(ErrorCode.TRANSCRIPT_DISABLED)
    if isinstance(exc, NoTranscriptFound):
        return AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE)
    if isinstance(exc, (VideoUnavailable, InvalidVideoId)):
        return AppError(ErrorCode.VIDEO_NOT_FOUND)
    if isinstance(exc, (VideoUnplayable, AgeRestricted)):
        return AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE, "El vídeo no es reproducible públicamente (restringido o privado).")
    if isinstance(exc, (RequestBlocked, IpBlocked)):
        return AppError(ErrorCode.TRANSCRIPT_FETCH_FAILED, "YouTube ha bloqueado temporalmente las peticiones desde esta IP.")
    return AppError(ErrorCode.TRANSCRIPT_FETCH_FAILED)


class YouTubeTranscriptProvider(TranscriptProvider):
    def __init__(self, api: YouTubeTranscriptApi | None = None) -> None:
        self._api = api or YouTubeTranscriptApi()

    def _list(self, video_id: str):
        try:
            return self._api.list(video_id)
        except CouldNotRetrieveTranscript as exc:
            logger.info("Transcript list failed for %s: %s", video_id, type(exc).__name__)
            raise _map_error(exc) from exc
        except Exception as exc:  # network errors, parsing changes...
            logger.warning("Unexpected transcript list error for %s: %s", video_id, type(exc).__name__)
            raise AppError(ErrorCode.TRANSCRIPT_FETCH_FAILED) from exc

    def inspect(self, video_id: str) -> list[TranscriptInfo]:
        return [
            TranscriptInfo(language=clean_language_name(t.language), language_code=t.language_code, is_generated=t.is_generated)
            for t in self._list(video_id)
        ]

    def fetch(self, video_id: str, track: TranscriptInfo) -> Transcript:
        transcript_list = self._list(video_id)
        match = next(
            (t for t in transcript_list if t.language_code == track.language_code and t.is_generated == track.is_generated),
            None,
        )
        if match is None:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE)
        try:
            fetched = match.fetch()
        except CouldNotRetrieveTranscript as exc:
            raise _map_error(exc) from exc
        except Exception as exc:
            logger.warning("Unexpected transcript fetch error for %s: %s", video_id, type(exc).__name__)
            raise AppError(ErrorCode.TRANSCRIPT_FETCH_FAILED) from exc

        segments = normalize_segments(fetched)
        if not segments:
            raise AppError(ErrorCode.TRANSCRIPT_NOT_AVAILABLE, "La transcripción del vídeo está vacía.")
        return Transcript(
            video_id=video_id,
            language=clean_language_name(match.language),
            language_code=match.language_code,
            is_generated=match.is_generated,
            segments=segments,
        )
