"""Repository for the `collections` and `collection_videos` tables."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from app.repositories.database import Database
from app.repositories.documents import ACTIVE_STATUSES, FILE_COLUMNS

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"


@dataclass
class CollectionVideoRecord:
    position: int
    video_db_id: int
    youtube_id: str
    video_url: str
    input_url: str
    video_title: str | None
    video_channel: str | None
    video_thumbnail: str | None
    transcript_language: str | None
    transcript_type: str | None
    document_id: int | None
    document_status: str | None
    status: str
    transcript_hash: str | None
    extraction_key: str | None
    extraction_cached: bool | None
    error_code: str | None
    error_message: str | None


@dataclass
class CollectionRecord:
    id: int
    document_type: str
    output_language: str
    output_mode: str
    status: str
    title: str | None
    markdown_path: str | None
    docx_path: str | None
    pdf_path: str | None
    txt_path: str | None
    processed_path: str | None
    cache_key: str | None
    prompt_version: str | None
    model: str | None
    usage: dict[str, Any] | None
    progress: dict[str, Any]
    error_code: str | None
    error_message: str | None
    reused_from_id: int | None
    created_at: str
    updated_at: str
    videos: list[CollectionVideoRecord]

    @property
    def wants_individual(self) -> bool:
        return self.output_mode in ("individual", "both")

    @property
    def wants_consolidated(self) -> bool:
        return self.output_mode in ("consolidated", "both")


_VIDEOS_SELECT = """
SELECT cv.*, v.youtube_id, v.url AS video_url, v.title AS video_title, v.channel AS video_channel,
       v.thumbnail AS video_thumbnail, v.transcript_language, v.transcript_type, d.status AS document_status
FROM collection_videos cv
JOIN videos v ON v.id = cv.video_id
LEFT JOIN documents d ON d.id = cv.document_id
WHERE cv.collection_id = ?
ORDER BY v.youtube_id
"""


def _video(row) -> CollectionVideoRecord:
    return CollectionVideoRecord(
        position=row["position"],
        video_db_id=row["video_id"],
        youtube_id=row["youtube_id"],
        video_url=row["video_url"],
        input_url=row["input_url"],
        video_title=row["video_title"],
        video_channel=row["video_channel"],
        video_thumbnail=row["video_thumbnail"],
        transcript_language=row["transcript_language"],
        transcript_type=row["transcript_type"],
        document_id=row["document_id"],
        document_status=row["document_status"],
        status=row["status"],
        transcript_hash=row["transcript_hash"],
        extraction_key=row["extraction_key"],
        extraction_cached=None if row["extraction_cached"] is None else bool(row["extraction_cached"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


def _record(row, videos: list[CollectionVideoRecord]) -> CollectionRecord:
    return CollectionRecord(
        id=row["id"],
        document_type=row["document_type"],
        output_language=row["output_language"],
        output_mode=row["output_mode"],
        status=row["status"],
        title=row["title"],
        markdown_path=row["markdown_path"],
        docx_path=row["docx_path"],
        pdf_path=row["pdf_path"],
        txt_path=row["txt_path"],
        processed_path=row["processed_path"],
        cache_key=row["cache_key"],
        prompt_version=row["prompt_version"],
        model=row["model"],
        usage=json.loads(row["usage_json"]) if row["usage_json"] else None,
        progress=json.loads(row["progress_json"]) if row["progress_json"] else {},
        error_code=row["error_code"],
        error_message=row["error_message"],
        reused_from_id=row["reused_from_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        videos=videos,
    )


class CollectionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._progress_lock = threading.Lock()

    def create(
        self,
        document_type: str,
        output_language: str,
        output_mode: str,
        videos: list[tuple[int, str, int | None]],
    ) -> int:
        """`videos`: (video_db_id, input_url, document_id); their order is kept only as `position` metadata."""
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO collections (document_type, output_language, output_mode, status, progress_json) "
                "VALUES (?, ?, ?, 'pending', '{}')",
                (document_type, output_language, output_mode),
            )
            collection_id = int(cur.lastrowid or 0)
            conn.executemany(
                "INSERT INTO collection_videos (collection_id, position, video_id, input_url, document_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [(collection_id, pos, vid, url, doc) for pos, (vid, url, doc) in enumerate(videos)],
            )
            return collection_id

    def get(self, collection_id: int) -> CollectionRecord | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
            if row is None:
                return None
            videos = [_video(r) for r in conn.execute(_VIDEOS_SELECT, (collection_id,)).fetchall()]
            return _record(row, videos)

    def list_recent(self, limit: int = 20) -> list[CollectionRecord]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM collections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [_record(r, [_video(v) for v in conn.execute(_VIDEOS_SELECT, (r["id"],)).fetchall()]) for r in rows]

    def find_completed_by_cache_key(self, cache_key: str, exclude_id: int | None = None) -> CollectionRecord | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM collections WHERE cache_key = ? AND status = 'completed' AND id != ? "
                "AND reused_from_id IS NULL AND processed_path IS NOT NULL ORDER BY id DESC LIMIT 1",
                (cache_key, exclude_id or -1),
            ).fetchone()
        return self.get(int(row["id"])) if row else None

    def update(self, collection_id: int, **fields: Any) -> None:
        allowed = {
            "status",
            "title",
            "cache_key",
            "prompt_version",
            "model",
            "error_code",
            "error_message",
            "reused_from_id",
            *FILE_COLUMNS,
        }
        values: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed}
        if "usage" in fields:
            values["usage_json"] = json.dumps(fields["usage"]) if fields["usage"] is not None else None
        unknown = set(fields) - allowed - {"usage"}
        if unknown:
            raise ValueError(f"Unknown collection fields: {unknown}")
        if not values:
            return
        assignments = ", ".join(f"{k} = ?" for k in values) + f", updated_at = {_NOW}"
        with self.db.connect() as conn:
            conn.execute(f"UPDATE collections SET {assignments} WHERE id = ?", (*values.values(), collection_id))

    def update_video(self, collection_id: int, position: int, **fields: Any) -> None:
        allowed = {"status", "transcript_hash", "extraction_key", "extraction_cached", "error_code", "error_message"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown collection video fields: {unknown}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE collection_videos SET {assignments} WHERE collection_id = ? AND position = ?",
                (*fields.values(), collection_id, position),
            )

    def merge_progress(self, collection_id: int, data: dict[str, Any]) -> None:
        with self._progress_lock, self.db.connect() as conn:
            row = conn.execute("SELECT progress_json FROM collections WHERE id = ?", (collection_id,)).fetchone()
            current = json.loads(row["progress_json"]) if row and row["progress_json"] else {}
            current.update(data)
            conn.execute(
                f"UPDATE collections SET progress_json = ?, updated_at = {_NOW} WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), collection_id),
            )

    def fail_interrupted(self) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                f"UPDATE collections SET status = 'failed', error_code = 'INTERNAL_ERROR', "
                f"error_message = 'El procesamiento se interrumpió (el servidor se reinició).', updated_at = {_NOW} "
                f"WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
                ACTIVE_STATUSES,
            )
            return cur.rowcount
