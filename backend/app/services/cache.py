"""Document cache fingerprint (idempotency)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def compute_cache_key(
    transcript_hash: str,
    document_type: str,
    output_language: str,
    prompt_version: str,
    model_config: str,
) -> str:
    """SHA256(transcript_hash + document_type + output_language + prompt_version + model_config)."""
    payload = "\x1f".join([transcript_hash, document_type, output_language, prompt_version, model_config])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_video_ids(video_ids: Iterable[str]) -> list[str]:
    """A collection is an unordered SET of videos: unique youtube ids in canonical (sorted) order."""
    return sorted(set(video_ids))


def compute_collection_identity(video_ids: Iterable[str]) -> str:
    """Which videos form the collection, whatever the order (or repetitions) in which they were given."""
    return hashlib.sha256(",".join(canonical_video_ids(video_ids)).encode("utf-8")).hexdigest()


def compute_collection_key(
    videos: Iterable[tuple[str, str]],
    document_type: str,
    output_language: str,
    prompt_version: str,
    model_config: str,
) -> str:
    """Fingerprint of a consolidated document.

    SHA256(sorted(unique((youtube_id, transcript_hash))) + document_type + output_language + prompt versions
    + model config (provider, model, reasoning, flags)). The input order never takes part: A,B,C / C,A,B /
    B,C,A (or A,A,B,C) are the same collection and share the same consolidated document.
    """
    pairs = ",".join(f"{vid}:{thash}" for vid, thash in sorted(set(videos)))
    payload = "\x1f".join([pairs, document_type, output_language, prompt_version, model_config])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
