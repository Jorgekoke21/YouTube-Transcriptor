"""Repository for the `videos` and `documents` tables."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any

from app.repositories.database import Database

ACTIVE_STATUSES = ("pending", "fetching_transcript", "processing", "generating_files")
FILE_COLUMNS = ("markdown_path", "docx_path", "pdf_path", "txt_path", "processed_path")
_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"


@dataclass
class DocumentRecord:
    id: int
    video_db_id: int
    youtube_id: str
    video_url: str
    video_title: str | None
    video_channel: str | None
    video_thumbnail: str | None
    transcript_language: str | None
    transcript_type: str | None
    document_type: str
    output_language: str
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


_SELECT = """
SELECT d.*, v.youtube_id, v.url AS video_url, v.title AS video_title, v.channel AS video_channel,
       v.thumbnail AS video_thumbnail, v.transcript_language, v.transcript_type
FROM documents d JOIN videos v ON v.id = d.video_id
"""


def _to_record(row) -> DocumentRecord:
    return DocumentRecord(
        id=row["id"],
        video_db_id=row["video_id"],
        youtube_id=row["youtube_id"],
        video_url=row["video_url"],
        video_title=row["video_title"],
        video_channel=row["video_channel"],
        video_thumbnail=row["video_thumbnail"],
        transcript_language=row["transcript_language"],
        transcript_type=row["transcript_type"],
        document_type=row["document_type"],
        output_language=row["output_language"],
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
    )


class DocumentRepository:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._progress_lock = threading.Lock()

    # ------------------------------------------------------------------ videos

    def upsert_video(
        self,
        youtube_id: str,
        url: str,
        title: str | None = None,
        channel: str | None = None,
        thumbnail: str | None = None,
        transcript_language: str | None = None,
        transcript_type: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos (youtube_id, url, title, channel, thumbnail, transcript_language, transcript_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(youtube_id) DO UPDATE SET
                    url = excluded.url,
                    title = COALESCE(excluded.title, videos.title),
                    channel = COALESCE(excluded.channel, videos.channel),
                    thumbnail = COALESCE(excluded.thumbnail, videos.thumbnail),
                    transcript_language = COALESCE(excluded.transcript_language, videos.transcript_language),
                    transcript_type = COALESCE(excluded.transcript_type, videos.transcript_type)
                """,
                (youtube_id, url, title, channel, thumbnail, transcript_language, transcript_type),
            )
            row = conn.execute("SELECT id FROM videos WHERE youtube_id = ?", (youtube_id,)).fetchone()
            return int(row["id"])

    # ------------------------------------------------------------------ documents

    def create_document(self, video_db_id: int, document_type: str, output_language: str) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO documents (video_id, document_type, output_language, status, progress_json) "
                "VALUES (?, ?, ?, 'pending', '{}')",
                (video_db_id, document_type, output_language),
            )
            return int(cur.lastrowid or 0)

    def get(self, document_id: int) -> DocumentRecord | None:
        with self.db.connect() as conn:
            row = conn.execute(_SELECT + " WHERE d.id = ?", (document_id,)).fetchone()
            return _to_record(row) if row else None

    def list_recent(self, limit: int = 20) -> list[DocumentRecord]:
        with self.db.connect() as conn:
            rows = conn.execute(_SELECT + " WHERE d.reused_from_id IS NULL ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall()
            return [_to_record(r) for r in rows]

    def find_completed_by_cache_key(self, cache_key: str, exclude_id: int | None = None) -> DocumentRecord | None:
        with self.db.connect() as conn:
            row = conn.execute(
                _SELECT + " WHERE d.cache_key = ? AND d.status = 'completed' AND d.id != ? AND d.reused_from_id IS NULL"
                " ORDER BY d.id DESC LIMIT 1",
                (cache_key, exclude_id or -1),
            ).fetchone()
            return _to_record(row) if row else None

    def update(self, document_id: int, **fields: Any) -> None:
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
            raise ValueError(f"Unknown document fields: {unknown}")
        if not values:
            return
        assignments = ", ".join(f"{k} = ?" for k in values) + f", updated_at = {_NOW}"
        with self.db.connect() as conn:
            conn.execute(f"UPDATE documents SET {assignments} WHERE id = ?", (*values.values(), document_id))

    def merge_progress(self, document_id: int, data: dict[str, Any]) -> None:
        with self._progress_lock, self.db.connect() as conn:
            row = conn.execute("SELECT progress_json FROM documents WHERE id = ?", (document_id,)).fetchone()
            current = json.loads(row["progress_json"]) if row and row["progress_json"] else {}
            current.update(data)
            conn.execute(
                f"UPDATE documents SET progress_json = ?, updated_at = {_NOW} WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), document_id),
            )

    def fail_interrupted(self) -> int:
        """Documents left in an active state by a previous server run can never finish."""
        with self.db.connect() as conn:
            cur = conn.execute(
                f"UPDATE documents SET status = 'failed', error_code = 'INTERNAL_ERROR', "
                f"error_message = 'El procesamiento se interrumpió (el servidor se reinició).', updated_at = {_NOW} "
                f"WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
                ACTIVE_STATUSES,
            )
            return cur.rowcount
