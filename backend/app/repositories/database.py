"""SQLite access (stdlib `sqlite3`, one short-lived connection per operation)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    title TEXT,
    channel TEXT,
    thumbnail TEXT,
    transcript_language TEXT,
    transcript_type TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    document_type TEXT NOT NULL,
    output_language TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    markdown_path TEXT,
    docx_path TEXT,
    pdf_path TEXT,
    txt_path TEXT,
    processed_path TEXT,
    cache_key TEXT,
    prompt_version TEXT,
    model TEXT,
    usage_json TEXT,
    progress_json TEXT,
    error_code TEXT,
    error_message TEXT,
    reused_from_id INTEGER REFERENCES documents(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_documents_cache ON documents(cache_key, status);
CREATE INDEX IF NOT EXISTS idx_documents_created ON documents(created_at);

-- Multi-video batches. output_mode: individual | consolidated | both.
-- The consolidated document (if any) lives in the *_path columns of the collection itself.
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type TEXT NOT NULL,
    output_language TEXT NOT NULL,
    output_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT,
    markdown_path TEXT,
    docx_path TEXT,
    pdf_path TEXT,
    txt_path TEXT,
    processed_path TEXT,
    cache_key TEXT,
    prompt_version TEXT,
    model TEXT,
    usage_json TEXT,
    progress_json TEXT,
    error_code TEXT,
    error_message TEXT,
    reused_from_id INTEGER REFERENCES collections(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Collection ↔ video. A collection is an unordered SET of videos (UNIQUE collection_id, video_id).
-- position = order in which the URLs were given: presentation/debug metadata only, never part of the
-- collection identity, cache keys or evidence ids.
-- document_id: the individual document of this video (output_mode individual | both).
CREATE TABLE IF NOT EXISTS collection_videos (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    input_url TEXT NOT NULL,
    document_id INTEGER REFERENCES documents(id),
    status TEXT NOT NULL DEFAULT 'pending',
    transcript_hash TEXT,
    extraction_key TEXT,
    extraction_cached INTEGER,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY (collection_id, position),
    UNIQUE (collection_id, video_id)
);

CREATE INDEX IF NOT EXISTS idx_collections_cache ON collections(cache_key, status);

-- Cache of per-video structured extractions (stage A), shared by single and consolidated documents.
CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL UNIQUE,
    youtube_id TEXT NOT NULL,
    transcript_hash TEXT NOT NULL,
    document_type TEXT NOT NULL,
    output_language TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_config TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
