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
from youtube_transcript_api.proxies import WebshareProxyConfig

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.transcript import Transcript, TranscriptInfo
from app.services.transcripts.base import TranscriptProvider, clean_language_name, normalize_segments

logger = logging.getLogger(__name__)


def build_youtube_api(settings: Settings) -> YouTubeTranscriptApi:
    """Creates the client, routed through a residential proxy only when configured.

    The proxy only affects requests made by this client to YouTube; nothing else
    (OpenAI, oEmbed metadata...) goes through it. Credentials are never logged.
    """
    if settings.youtube_proxy_provider == "webshare":
        assert settings.webshare_proxy_username and settings.webshare_proxy_password  # enforced by Settings
        locations = settings.webshare_proxy_location_list
        logger.info("YouTube transcript proxy: webshare (locations=%s)", ",".join(locations) or "any")
        return YouTubeTranscriptApi(
            proxy_config=WebshareProxyConfig(
                proxy_username=settings.webshare_proxy_username.get_secret_value().strip(),
                proxy_password=settings.webshare_proxy_password.get_secret_value().strip(),
                filter_ip_locations=locations or None,
            )
        )
    return YouTubeTranscriptApi()


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


def _log_retrieve_error(step: str, video_id: str, exc: Exception) -> None:
    # Only the exception type is logged: messages may describe the proxy setup.
    if isinstance(exc, (RequestBlocked, IpBlocked)):
        logger.warning("youtube_ip_blocked: transcript %s blocked for %s (%s)", step, video_id, type(exc).__name__)
    else:
        logger.info("Transcript %s failed for %s: %s", step, video_id, type(exc).__name__)


class YouTubeTranscriptProvider(TranscriptProvider):
    def __init__(self, api: YouTubeTranscriptApi | None = None) -> None:
        self._api = api or YouTubeTranscriptApi()

    def _list(self, video_id: str):
        try:
            return self._api.list(video_id)
        except CouldNotRetrieveTranscript as exc:
            _log_retrieve_error("list", video_id, exc)
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
            _log_retrieve_error("fetch", video_id, exc)
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
