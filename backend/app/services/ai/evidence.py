"""Evidence items (the structured per-video extraction) and the extraction cache seam.

An `EvidenceItem` is one idea extracted from a transcript with trusted timestamps. Single-video
documents use them as-is; consolidated documents namespace them per video (`V2.c0-i3`) and carry
the video id, so every idea keeps its provenance through every later stage.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from app.models.document import SourceRange


@dataclass
class EvidenceItem:
    id: str
    kind: str
    certainty: str
    text: str
    tags: list[str]
    start: float
    end: float
    chunk_index: int
    steps: list[str] = field(default_factory=list)
    # Provenance in multi-video documents ("" for single-video documents).
    video_id: str = ""
    source: str = ""  # short reference shown to the model, e.g. "V2"

    @property
    def range(self) -> SourceRange:
        return SourceRange(start=self.start, end=self.end, video_id=self.video_id or None)

    def to_prompt(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id}
        if self.source:
            data["source"] = self.source
        data |= {
            "kind": self.kind,
            "certainty": self.certainty,
            "text": self.text,
            "start": round(self.start, 1),
            "end": round(self.end, 1),
        }
        if self.tags:
            data["tags"] = self.tags
        if self.steps:
            data["steps"] = self.steps
        return data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def for_source(self, source: str, video_id: str) -> EvidenceItem:
        """Copy namespaced for a multi-video pool: `c0-i3` → `V2.c0-i3`."""
        return replace(
            self, id=f"{source}.{self.id}", source=source, video_id=video_id, tags=list(self.tags), steps=list(self.steps)
        )


def compute_extraction_key(youtube_id: str, transcript_hash: str, extraction_fingerprint: str) -> str:
    """SHA256(youtube_id + transcript_hash + extraction fingerprint (provider, model, reasoning, prompts, language, chunking))."""
    payload = "\x1f".join([youtube_id, transcript_hash, extraction_fingerprint])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExtractionMeta:
    youtube_id: str
    transcript_hash: str
    document_type: str
    output_language: str
    prompt_version: str
    model_config: str


class EvidenceStore(ABC):
    """Persistent cache of per-video extractions (stage A), shared by single and consolidated documents."""

    @abstractmethod
    def load(self, key: str) -> list[EvidenceItem] | None: ...

    @abstractmethod
    def save(self, key: str, meta: ExtractionMeta, evidence: list[EvidenceItem]) -> None: ...
