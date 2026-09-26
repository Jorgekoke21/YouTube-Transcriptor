"""Filename sanitisation. Untrusted strings (e.g. YouTube titles) never become paths as-is."""

from __future__ import annotations

import re
import unicodedata

_RESERVED_WINDOWS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str | None, fallback: str = "document", max_length: int = 80) -> str:
    """Return a safe, ASCII-only file stem (no extension, no separators)."""
    text = unicodedata.normalize("NFKD", name or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._ -]+", " ", text)
    text = re.sub(r"[\s_]+", "_", text).strip("._- ")
    text = re.sub(r"\.{2,}", ".", text)
    text = text[:max_length].rstrip("._- ")
    if not text or text.upper() in _RESERVED_WINDOWS:
        return fallback
    return text
