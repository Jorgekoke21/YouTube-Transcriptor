"""Temporal/semantic chunking of a transcript that preserves the original segments."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.transcript import Transcript, TranscriptSegment

SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")
PAUSE_SECONDS = 1.5


@dataclass
class TranscriptChunk:
    index: int
    segments: list[TranscriptSegment] = field(default_factory=list)

    @property
    def start_time(self) -> float:
        return self.segments[0].start if self.segments else 0.0

    @property
    def end_time(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def word_count(self) -> int:
        return sum(len(s.text.split()) for s in self.segments)

    def to_prompt_text(self) -> str:
        """One line per segment prefixed with its start time in seconds: `[123.4] text`."""
        return "\n".join(f"[{s.start:.1f}] {s.text}" for s in self.segments)


def _is_boundary(current: TranscriptSegment, nxt: TranscriptSegment | None) -> bool:
    """A good place to cut: end of sentence or a noticeable pause before the next segment."""
    if nxt is None:
        return True
    if current.text.rstrip().endswith(SENTENCE_END):
        return True
    return (nxt.start - current.end) >= PAUSE_SECONDS


def chunk_transcript(transcript: Transcript, target_words: int = 1400, max_words: int = 1900) -> list[TranscriptChunk]:
    """Split the transcript into chunks of consecutive segments.

    - Segments are never split and always stay in temporal order.
    - Once a chunk reaches `target_words`, it is closed at the next natural
      boundary (sentence end or pause); it is force-closed at `max_words`.
    - A very small trailing chunk is merged into the previous one.
    """
    if target_words <= 0 or max_words < target_words:
        raise ValueError("invalid chunk sizes")

    segments = transcript.segments
    chunks: list[TranscriptChunk] = []
    current = TranscriptChunk(index=0)
    words = 0

    for i, seg in enumerate(segments):
        current.segments.append(seg)
        words += len(seg.text.split())
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if nxt is None:
            break
        if (words >= target_words and _is_boundary(seg, nxt)) or words >= max_words:
            chunks.append(current)
            current = TranscriptChunk(index=len(chunks))
            words = 0

    if current.segments:
        if chunks and words < target_words * 0.25 and chunks[-1].word_count + words <= max_words * 1.2:
            chunks[-1].segments.extend(current.segments)
        else:
            chunks.append(current)

    for idx, chunk in enumerate(chunks):
        chunk.index = idx
    return chunks
