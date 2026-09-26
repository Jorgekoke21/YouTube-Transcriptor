"""Robust extraction and validation of YouTube video IDs."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from app.core.errors import AppError, ErrorCode

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
# Path prefixes where the video id is the next path component.
_PATH_PREFIXES = {"shorts", "live", "embed", "v", "e"}


def _query_param(query: str, name: str) -> str | None:
    values = parse_qs(query).get(name)
    return values[0] if values else None


def is_valid_video_id(video_id: str | None) -> bool:
    return bool(video_id) and VIDEO_ID_RE.fullmatch(video_id or "") is not None


def extract_video_id(url: str) -> str | None:
    """Return the 11-char video id contained in a YouTube URL, or None.

    Supported forms: watch?v=, youtu.be/, /shorts/, /live/, /embed/, /v/,
    with or without scheme, `www.`/`m.` subdomains and extra query params.
    """
    if not url or not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw or len(raw) > 2048 or any(c.isspace() for c in raw):
        return None
    if "://" not in raw:
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None

    host = (parsed.hostname or "").lower()
    parts = [p for p in parsed.path.split("/") if p]
    candidate: str | None = None

    if host in _SHORT_HOSTS:
        candidate = parts[0] if parts else None
    elif host in _YOUTUBE_HOSTS:
        if parts and parts[0] == "watch":
            candidate = _query_param(parsed.query, "v")
        elif len(parts) >= 2 and parts[0] in _PATH_PREFIXES:
            candidate = parts[1]
        elif not parts:
            # e.g. youtube.com/?v=ID (rare but seen in the wild)
            candidate = _query_param(parsed.query, "v")
    else:
        return None

    return candidate if is_valid_video_id(candidate) else None


def require_video_id(url: str) -> str:
    video_id = extract_video_id(url)
    if video_id is None:
        raise AppError(ErrorCode.INVALID_YOUTUBE_URL)
    return video_id


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def timestamp_url(video_id: str, seconds: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={max(0, int(seconds))}s"


@dataclass(frozen=True)
class BatchEntry:
    """One non-empty input line of a multi-URL batch."""

    position: int  # 0-based position among the non-empty lines, in the order the user pasted them
    input: str
    video_id: str | None  # None when the line is not a valid YouTube video URL
    duplicate_of: int | None = None  # position of the first line with the same video id


@dataclass(frozen=True)
class ParsedBatch:
    entries: list[BatchEntry]

    @property
    def unique(self) -> list[BatchEntry]:
        """Valid, first-seen entries in paste order (one per video)."""
        return [e for e in self.entries if e.video_id and e.duplicate_of is None]

    @property
    def invalid(self) -> list[BatchEntry]:
        return [e for e in self.entries if e.video_id is None]

    @property
    def duplicates(self) -> list[BatchEntry]:
        return [e for e in self.entries if e.duplicate_of is not None]


def split_url_lines(values: str | Iterable[str]) -> list[str]:
    """Accept a pasted block or a list of lines; return trimmed, non-empty lines."""
    raw = values.splitlines() if isinstance(values, str) else [line for v in values for line in str(v).splitlines()]
    return [line.strip() for line in raw if line and line.strip()]


def parse_url_batch(values: str | Iterable[str]) -> ParsedBatch:
    """Normalise a multi-URL input: drop empty lines, extract ids, dedupe by video id keeping paste order."""
    entries: list[BatchEntry] = []
    first_seen: dict[str, int] = {}
    for position, line in enumerate(split_url_lines(values)):
        video_id = extract_video_id(line)
        duplicate_of = first_seen.get(video_id) if video_id else None
        if video_id and duplicate_of is None:
            first_seen[video_id] = position
        entries.append(BatchEntry(position=position, input=line, video_id=video_id, duplicate_of=duplicate_of))
    return ParsedBatch(entries=entries)
