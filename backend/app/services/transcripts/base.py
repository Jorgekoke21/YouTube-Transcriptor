"""Transcript provider abstraction.

The rest of the application depends only on this interface and on the internal
`Transcript` / `TranscriptSegment` models, never on a concrete library.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from app.models.transcript import Transcript, TranscriptInfo, TranscriptSegment


class TranscriptProvider(ABC):
    @abstractmethod
    def inspect(self, video_id: str) -> list[TranscriptInfo]:
        """List transcript tracks available for the video.

        Raises AppError(TRANSCRIPT_DISABLED | VIDEO_NOT_FOUND | TRANSCRIPT_FETCH_FAILED).
        Returns an empty list when the video simply has no transcript.
        """

    @abstractmethod
    def fetch(self, video_id: str, track: TranscriptInfo) -> Transcript:
        """Fetch a specific transcript track (with timestamps), normalised."""


def select_transcript(available: Sequence[TranscriptInfo], preferred_languages: Sequence[str]) -> TranscriptInfo | None:
    """Pick the best transcript according to the preference order.

    For each preferred language (matching by base code, so `es` matches `es-419`):
    manual first, then auto-generated. Falls back to any manual track, then any track.
    With the default preferences ["es", "en"] this yields:
    es manual > es auto > en manual > en auto > any other.
    """
    if not available:
        return None

    def base(code: str) -> str:
        return code.lower().replace("_", "-").split("-")[0]

    for lang in preferred_languages:
        wanted = base(lang)
        candidates = [t for t in available if base(t.language_code) == wanted]
        # Exact code match first (e.g. "es" before "es-419"), manual before generated.
        candidates.sort(key=lambda t: (t.is_generated, t.language_code.lower() != lang.lower()))
        if candidates:
            return candidates[0]

    manual = [t for t in available if not t.is_generated]
    return manual[0] if manual else available[0]


def clean_language_name(name: str) -> str:
    """`"Spanish (auto-generated)"` → `"Spanish"`; the manual/auto flag is stored separately."""
    cleaned = re.sub(
        r"\s*\((auto-generated|generado automáticamente|automatique|automatisch[^)]*)\)\s*$", "", name or "", flags=re.I
    )
    return cleaned.strip() or (name or "").strip()


def normalize_segments(raw: Iterable[dict | object]) -> list[TranscriptSegment]:
    """Normalise raw provider snippets into ordered, clean `TranscriptSegment`s.

    Accepts dicts (`{"text","start","duration"}`) or objects with those attributes.
    Collapses whitespace, drops empty snippets and pure sound tags such as
    `[Música]`, and sorts by start time. Timestamps are preserved untouched.
    """
    segments: list[TranscriptSegment] = []
    for item in raw:
        if isinstance(item, dict):
            text, start, duration = item.get("text"), item.get("start"), item.get("duration")
        else:
            text = getattr(item, "text", None)
            start = getattr(item, "start", None)
            duration = getattr(item, "duration", None)
        if text is None or start is None:
            continue
        clean = " ".join(str(text).replace("\n", " ").split())
        if not clean or _is_sound_tag(clean):
            continue
        try:
            start_f = max(0.0, float(start))
            duration_f = max(0.0, float(duration or 0.0))
        except (TypeError, ValueError):
            continue
        segments.append(TranscriptSegment(text=clean, start=round(start_f, 3), duration=round(duration_f, 3)))
    segments.sort(key=lambda s: s.start)
    return segments


def _is_sound_tag(text: str) -> bool:
    return (text.startswith("[") and text.endswith("]")) or (text.startswith("(") and text.endswith(")") and len(text) < 30)
