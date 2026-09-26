"""Best-effort video metadata (title, channel, thumbnail) via YouTube oEmbed.

Metadata is secondary: failures never block document generation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.models.transcript import VideoMetadata
from app.utils.youtube_url import canonical_url

logger = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"


class MetadataProvider(ABC):
    @abstractmethod
    def get(self, video_id: str) -> VideoMetadata: ...


class OEmbedMetadataProvider(MetadataProvider):
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def get(self, video_id: str) -> VideoMetadata:
        url = canonical_url(video_id)
        meta = VideoMetadata(video_id=video_id, url=url, thumbnail=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
        try:
            resp = httpx.get(OEMBED_URL, params={"url": url, "format": "json"}, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                meta.title = data.get("title") or None
                meta.channel = data.get("author_name") or None
                meta.thumbnail = data.get("thumbnail_url") or meta.thumbnail
            else:
                logger.info("oEmbed returned %s for %s", resp.status_code, video_id)
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("oEmbed failed for %s: %s", video_id, type(exc).__name__)
        return meta


class StaticMetadataProvider(MetadataProvider):
    """Offline provider used in development/test mode."""

    def get(self, video_id: str) -> VideoMetadata:
        return VideoMetadata(
            video_id=video_id,
            url=canonical_url(video_id),
            title="Divergencias RSI: cómo operarlas (vídeo de ejemplo)",
            channel="Canal de ejemplo",
            thumbnail=None,
        )
