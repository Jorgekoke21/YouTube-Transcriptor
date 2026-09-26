"""Deterministic timestamp safety net.

The model is told to use only timestamps present in the source, but we never
trust it blindly: every range is clamped to the video, snapped to real segment
boundaries and (when evidence is known) checked against the evidence ranges.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence

from app.models.document import SourceRange
from app.models.transcript import TranscriptSegment


class TimestampIndex:
    def __init__(self, segments: Sequence[TranscriptSegment]) -> None:
        self._segments = list(segments)
        self._starts = [s.start for s in self._segments]
        self.duration = max((s.end for s in self._segments), default=0.0)

    def _segment_at(self, t: float) -> TranscriptSegment | None:
        if not self._segments:
            return None
        pos = bisect_right(self._starts, t) - 1
        return self._segments[max(0, pos)]

    def excerpt(self, ranges: Sequence[SourceRange], margin: float = 5.0, max_segments: int = 800) -> str:
        """Original transcript lines (`[seconds] text`) overlapping the given ranges (plus a margin)."""
        windows = merge_ranges((SourceRange(start=max(0.0, r.start - margin), end=r.end + margin) for r in ranges), max_gap=0.0)
        lines = [f"[{s.start:.1f}] {s.text}" for s in self._segments if any(s.start < w.end and s.end > w.start for w in windows)]
        return "\n".join(lines[:max_segments])

    def snap(self, rng: SourceRange) -> SourceRange | None:
        """Clamp and snap a range to segment boundaries. Returns None if unusable."""
        try:
            start, end = float(rng.start), float(rng.end)
        except (TypeError, ValueError):
            return None
        if start != start or end != end:  # NaN
            return None
        if end < start:
            start, end = end, start
        if start < 0 or start > self.duration + 1:
            return None
        end = min(max(end, start), self.duration)
        first = self._segment_at(start)
        last = self._segment_at(end)
        if first is None or last is None:
            return None
        return SourceRange(start=first.start, end=max(last.end, first.end))


def merge_ranges(ranges: Iterable[SourceRange], max_gap: float = 45.0) -> list[SourceRange]:
    ordered = sorted(ranges, key=lambda r: r.start)
    merged: list[SourceRange] = []
    for r in ordered:
        if merged and r.start - merged[-1].end <= max_gap:
            merged[-1] = SourceRange(start=merged[-1].start, end=max(merged[-1].end, r.end))
        else:
            merged.append(SourceRange(start=r.start, end=r.end))
    return merged


def overlaps(a: SourceRange, b: SourceRange, tolerance: float = 0.0) -> bool:
    return a.start <= b.end + tolerance and b.start <= a.end + tolerance


def sanitize_ranges(
    ranges: Iterable[SourceRange],
    index: TimestampIndex,
    evidence: Sequence[SourceRange] | None = None,
    tolerance: float = 10.0,
) -> list[SourceRange]:
    """Snap ranges to the transcript; if `evidence` is given, drop ranges not supported by it."""
    result: list[SourceRange] = []
    for rng in ranges:
        snapped = index.snap(rng)
        if snapped is None:
            continue
        if evidence is not None and not any(overlaps(snapped, ev, tolerance) for ev in evidence):
            continue
        result.append(snapped)
    return merge_ranges(result, max_gap=5.0)
