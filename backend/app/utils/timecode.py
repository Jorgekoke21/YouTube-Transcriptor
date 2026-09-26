"""Timestamp formatting helpers."""

from __future__ import annotations


def format_timestamp(seconds: float) -> str:
    """Format seconds as `M:SS` / `MM:SS` or `H:MM:SS` for long videos."""
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_range(start: float, end: float) -> str:
    if int(end) <= int(start):
        return format_timestamp(start)
    return f"{format_timestamp(start)} – {format_timestamp(end)}"
