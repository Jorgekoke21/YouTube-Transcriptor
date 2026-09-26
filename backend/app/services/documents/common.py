"""Shared helpers for all document renderers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.document import DOCUMENT_TYPE_LABELS, AnyDocument, ConsolidatedDocument, SourceRange, SourceVideo
from app.utils.timecode import format_range
from app.utils.youtube_url import timestamp_url

MAX_RANGES_SHOWN = 4
MAX_COLLECTION_RANGES_SHOWN = 6
SHORT_TITLE_LENGTH = 42

LABELS: dict[str, dict[str, str]] = {
    "es": {
        "video": "Vídeo",
        "channel": "Canal",
        "url": "URL original",
        "language": "Idioma de la transcripción",
        "type": "Tipo de documento",
        "generated": "Fecha de generación",
        "summary": "Resumen",
        "key_points": "Ideas clave",
        "note": "Nota",
        "manual": "manual",
        "generated_tr": "automática",
        "sources": "Fuentes",
        "sources_used": "Fuentes utilizadas",
        "video_count": "Número de vídeos",
    },
    "en": {
        "video": "Video",
        "channel": "Channel",
        "url": "Original URL",
        "language": "Transcript language",
        "type": "Document type",
        "generated": "Generated on",
        "summary": "Summary",
        "key_points": "Key points",
        "note": "Note",
        "manual": "manual",
        "generated_tr": "auto-generated",
        "sources": "Sources",
        "sources_used": "Sources used",
        "video_count": "Number of videos",
    },
}


def labels(lang: str) -> dict[str, str]:
    return LABELS.get(lang, LABELS["es"])


@dataclass(frozen=True)
class TimeLink:
    label: str
    url: str
    start: float
    end: float


def is_collection(doc: AnyDocument) -> bool:
    return isinstance(doc, ConsolidatedDocument)


def document_sources(doc: AnyDocument) -> list[SourceVideo]:
    return list(doc.sources) if isinstance(doc, ConsolidatedDocument) else [doc.source_video]


def range_label(doc: AnyDocument) -> str:
    """Prefix of a heading's time links: "Vídeo" (one video) or "Fuentes" (consolidated)."""
    return labels(doc.output_language)["sources" if is_collection(doc) else "video"]


def short_title(video: SourceVideo, number: int, lang: str) -> str:
    title = " ".join((video.title or "").split())
    if not title:
        return f"{labels(lang)['video']} {number}"
    return title if len(title) <= SHORT_TITLE_LENGTH else title[: SHORT_TITLE_LENGTH - 1].rstrip() + "…"


def time_links(doc: AnyDocument, ranges: Sequence[SourceRange]) -> list[TimeLink]:
    """Clickable time references. Each range opens ITS video (`range.video_id`, or the document's own video).

    In consolidated documents the label also names the video: `Divergencias con RSI · 04:32`.
    """
    sources = document_sources(doc)
    default_id = sources[0].video_id if sources else ""
    if not is_collection(doc):
        return [
            TimeLink(
                label=format_range(r.start, r.end), url=timestamp_url(r.video_id or default_id, r.start), start=r.start, end=r.end
            )
            for r in list(ranges)[:MAX_RANGES_SHOWN]
        ]
    names = {v.video_id: short_title(v, n, doc.output_language) for n, v in enumerate(sources, 1)}
    links = []
    for r in list(ranges)[:MAX_COLLECTION_RANGES_SHOWN]:
        video_id = r.video_id or default_id
        label = f"{names.get(video_id, video_id)} · {format_range(r.start, r.end)}"
        links.append(TimeLink(label=label, url=timestamp_url(video_id, r.start), start=r.start, end=r.end))
    return links


@dataclass(frozen=True)
class MetaLine:
    label: str
    value: str
    url: str | None = None


@dataclass(frozen=True)
class SourceLine:
    number: int
    title: str
    channel: str | None
    url: str


def source_lines(doc: AnyDocument) -> list[SourceLine]:
    """The "Fuentes utilizadas" header of consolidated documents (empty for single-video documents)."""
    if not is_collection(doc):
        return []
    return [SourceLine(n, v.title or v.video_id, v.channel, v.url) for n, v in enumerate(document_sources(doc), 1)]


def metadata_lines(doc: AnyDocument) -> list[MetaLine]:
    lab = labels(doc.output_language)
    type_labels = DOCUMENT_TYPE_LABELS.get(doc.output_language, DOCUMENT_TYPE_LABELS["es"])
    if isinstance(doc, ConsolidatedDocument):
        return [
            MetaLine(lab["type"], type_labels[doc.document_type]),
            MetaLine(lab["video_count"], str(len(doc.sources))),
            MetaLine(lab["generated"], doc.generated_at[:16].replace("T", " ")),
        ]
    v = doc.source_video
    lines = [MetaLine(lab["video"], v.title or v.video_id, v.url)]
    if v.channel:
        lines.append(MetaLine(lab["channel"], v.channel))
    lines.append(MetaLine(lab["url"], v.url, v.url))
    if v.transcript_language:
        kind = lab["generated_tr"] if v.transcript_is_generated else lab["manual"]
        lines.append(MetaLine(lab["language"], f"{v.transcript_language} · {kind}"))
    lines.append(MetaLine(lab["type"], type_labels[doc.document_type]))
    lines.append(MetaLine(lab["generated"], doc.generated_at[:16].replace("T", " ")))
    return lines


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def split_bold(text: str) -> list[tuple[str, bool]]:
    """Split `a **b** c` into [("a ", False), ("b", True), (" c", False)]."""
    parts: list[tuple[str, bool]] = []
    pos = 0
    for m in _BOLD_RE.finditer(text):
        if m.start() > pos:
            parts.append((text[pos : m.start()], False))
        parts.append((m.group(1), True))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts or [("", False)]


def strip_markdown(text: str) -> str:
    return _BOLD_RE.sub(r"\1", text)
