"""File + SQLite implementation of the extraction cache (`EvidenceStore`).

Extractions are stored next to the video they belong to:
`data/videos/VIDEO_ID/extractions/<cache_key>.json`, indexed in the `extractions` table.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.repositories.extractions import ExtractionRepository
from app.services.ai.evidence import EvidenceItem, EvidenceStore, ExtractionMeta
from app.utils.youtube_url import is_valid_video_id

logger = logging.getLogger(__name__)


class FileEvidenceStore(EvidenceStore):
    def __init__(self, repo: ExtractionRepository, videos_dir: Path) -> None:
        self.repo = repo
        self.videos_dir = videos_dir

    def load(self, key: str) -> list[EvidenceItem] | None:
        raw = self.repo.path_for(key)
        path = Path(raw) if raw else None
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [EvidenceItem.from_dict(d) for d in data["evidence"]]
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Ignoring unreadable cached extraction %s", path)
            return None

    def save(self, key: str, meta: ExtractionMeta, evidence: list[EvidenceItem]) -> None:
        if not is_valid_video_id(meta.youtube_id):  # ids become directory names
            return
        path = self.videos_dir / meta.youtube_id / "extractions" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cache_key": key,
            "youtube_id": meta.youtube_id,
            "transcript_hash": meta.transcript_hash,
            "document_type": meta.document_type,
            "output_language": meta.output_language,
            "prompt_version": meta.prompt_version,
            "model_config": meta.model_config,
            "evidence": [e.to_dict() for e in evidence],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        self.repo.save(key, meta, len(evidence), str(path))
