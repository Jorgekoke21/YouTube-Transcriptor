"""Repository for the `extractions` table (index of cached per-video extractions)."""

from __future__ import annotations

import sqlite3

from app.repositories.database import Database
from app.services.ai.evidence import ExtractionMeta


class ExtractionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def path_for(self, cache_key: str) -> str | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT path FROM extractions WHERE cache_key = ?", (cache_key,)).fetchone()
            return row["path"] if row else None

    def save(self, cache_key: str, meta: ExtractionMeta, item_count: int, path: str) -> None:
        with self.db.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO extractions (cache_key, youtube_id, transcript_hash, document_type, output_language, "
                    "prompt_version, model_config, item_count, path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cache_key,
                        meta.youtube_id,
                        meta.transcript_hash,
                        meta.document_type,
                        meta.output_language,
                        meta.prompt_version,
                        meta.model_config,
                        item_count,
                        path,
                    ),
                )
            except sqlite3.IntegrityError:  # same extraction stored concurrently / regenerated: keep the latest file
                conn.execute("UPDATE extractions SET item_count = ?, path = ? WHERE cache_key = ?", (item_count, path, cache_key))

    def count(self) -> int:
        with self.db.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM extractions").fetchone()["n"])
