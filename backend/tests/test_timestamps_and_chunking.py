import pytest

from app.models.document import SourceRange
from app.models.transcript import Transcript, TranscriptSegment
from app.services.ai.chunking import chunk_transcript
from app.services.ai.timestamps import TimestampIndex, merge_ranges, sanitize_ranges
from app.utils.timecode import format_range, format_timestamp


def seg(text: str, start: float, duration: float = 2.0) -> TranscriptSegment:
    return TranscriptSegment(text=text, start=start, duration=duration)


def make_transcript(n: int, words_per_segment: int = 10, punctuate_every: int = 0, gap_every: int = 0) -> Transcript:
    segments, t = [], 0.0
    for i in range(n):
        text = " ".join(["palabra"] * words_per_segment)
        if punctuate_every and (i + 1) % punctuate_every == 0:
            text += "."
        segments.append(seg(text, t, 3.0))
        t += 3.0 + (2.0 if gap_every and (i + 1) % gap_every == 0 else 0.0)
    return Transcript(video_id="dQw4w9WgXcQ", language="es", language_code="es", is_generated=True, segments=segments)


# ---------------------------------------------------------------- formatting


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (5.9, "00:05"), (65, "01:05"), (1122.4, "18:42"), (1270, "21:10"), (3600, "1:00:00"), (3725, "1:02:05")],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    assert format_timestamp(seconds) == expected


def test_format_range() -> None:
    assert format_range(1122.4, 1270) == "18:42 – 21:10"
    assert format_range(10, 10.5) == "00:10"


# ---------------------------------------------------------------- timestamp safety net


def test_snap_to_segment_boundaries_and_clamp() -> None:
    index = TimestampIndex([seg("a", 0, 5), seg("b", 5, 5), seg("c", 10, 5)])
    assert index.duration == 15
    snapped = index.snap(SourceRange(start=6.2, end=11))
    assert (snapped.start, snapped.end) == (5, 15)
    reversed_range = index.snap(SourceRange(start=11, end=6.2))
    assert (reversed_range.start, reversed_range.end) == (5, 15)
    clamped = index.snap(SourceRange(start=2, end=999))
    assert (clamped.start, clamped.end) == (0, 15)
    assert index.snap(SourceRange(start=500, end=600)) is None  # invented timestamp beyond the video
    assert index.snap(SourceRange(start=-5, end=3)) is None


def test_sanitize_drops_ranges_not_supported_by_evidence() -> None:
    index = TimestampIndex([seg(str(i), i * 10, 10) for i in range(10)])
    evidence = [SourceRange(start=20, end=30)]
    kept = sanitize_ranges([SourceRange(start=22, end=28), SourceRange(start=80, end=85)], index, evidence)
    assert kept == [SourceRange(start=20, end=30)]


def test_excerpt_returns_original_lines_around_ranges() -> None:
    index = TimestampIndex([seg(f"s{i}", i * 10, 10) for i in range(10)])
    text = index.excerpt([SourceRange(start=22, end=28), SourceRange(start=71, end=72)], margin=1)
    assert text.splitlines() == ["[20.0] s2", "[70.0] s7"]


def test_merge_ranges() -> None:
    merged = merge_ranges([SourceRange(start=50, end=60), SourceRange(start=0, end=10), SourceRange(start=12, end=20)], max_gap=5)
    assert merged == [SourceRange(start=0, end=20), SourceRange(start=50, end=60)]


# ---------------------------------------------------------------- chunking


def test_chunks_preserve_all_segments_in_order() -> None:
    transcript = make_transcript(200, punctuate_every=7)
    chunks = chunk_transcript(transcript, target_words=300, max_words=450)
    flat = [s for c in chunks for s in c.segments]
    assert flat == transcript.segments
    assert len(chunks) > 3
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.end_time <= nxt.start_time
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunks_respect_max_words_and_cut_at_boundaries() -> None:
    transcript = make_transcript(200, punctuate_every=7)
    chunks = chunk_transcript(transcript, target_words=300, max_words=450)
    for chunk in chunks[:-1]:
        assert chunk.word_count <= 450
        assert chunk.segments[-1].text.endswith(".")  # closed at a sentence end


def test_chunks_use_pauses_when_no_punctuation() -> None:
    transcript = make_transcript(100, gap_every=9)
    chunks = chunk_transcript(transcript, target_words=200, max_words=400)
    for chunk in chunks[:-1]:
        last = chunk.segments[-1]
        idx = transcript.segments.index(last)
        assert transcript.segments[idx + 1].start - last.end >= 1.5


def test_force_cut_without_boundaries() -> None:
    chunks = chunk_transcript(make_transcript(100), target_words=100, max_words=150)
    assert all(c.word_count <= 150 for c in chunks)


def test_small_tail_is_merged_and_single_chunk_for_short_videos(fixture_transcript: Transcript) -> None:
    chunks = chunk_transcript(fixture_transcript)
    assert len(chunks) == 1
    assert chunks[0].start_time == 0.0 and chunks[0].end_time == pytest.approx(104.5)
    tail = chunk_transcript(make_transcript(33, punctuate_every=1), target_words=100, max_words=200)
    assert tail[-1].word_count >= 25


def test_chunk_prompt_text_keeps_timestamps(fixture_transcript: Transcript) -> None:
    text = chunk_transcript(fixture_transcript)[0].to_prompt_text()
    assert "[7.3] Hoy vamos a explicar qué es una divergencia." in text


def test_invalid_chunk_sizes() -> None:
    with pytest.raises(ValueError):
        chunk_transcript(make_transcript(3), target_words=100, max_words=50)
