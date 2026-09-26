from types import SimpleNamespace

import pytest
from youtube_transcript_api import NoTranscriptFound, RequestBlocked, TranscriptsDisabled, VideoUnavailable

from app.core.errors import AppError, ErrorCode
from app.models.transcript import Transcript, TranscriptInfo
from app.services.transcripts.base import clean_language_name, normalize_segments, select_transcript
from app.services.transcripts.fixture import FixtureTranscriptProvider
from app.services.transcripts.youtube import YouTubeTranscriptProvider


def info(code: str, generated: bool) -> TranscriptInfo:
    return TranscriptInfo(language=code, language_code=code, is_generated=generated)


# ---------------------------------------------------------------- normalizer


def test_normalizer_keeps_timestamps_and_computes_end() -> None:
    segs = normalize_segments([{"text": "Aquí esperamos a que rompa este máximo", "start": 1122.4, "duration": 8.3}])
    assert segs[0].text == "Aquí esperamos a que rompa este máximo"
    assert segs[0].start == 1122.4
    assert segs[0].duration == 8.3
    assert segs[0].end == pytest.approx(1130.7)


def test_normalizer_cleans_and_sorts() -> None:
    raw = [
        {"text": "segundo\n  texto", "start": 5, "duration": 2},
        {"text": "[Música]", "start": 3, "duration": 2},
        {"text": "   ", "start": 4, "duration": 1},
        {"text": "primero", "start": 0, "duration": 5},
        {"text": "sin start"},
        {"text": "negativo", "start": -1, "duration": None},
    ]
    segs = normalize_segments(raw)
    # Stable sort: "primero" and the clamped "negativo" both start at 0 and keep their input order.
    assert [s.text for s in segs] == ["primero", "negativo", "segundo texto"]
    assert segs[1].start == 0.0 and segs[1].duration == 0.0


def test_normalizer_accepts_objects() -> None:
    obj = SimpleNamespace(text="hola", start=1.0, duration=2.0)
    assert normalize_segments([obj])[0].end == 3.0


def test_transcript_properties_and_hash(fixture_transcript: Transcript) -> None:
    assert fixture_transcript.word_count > 100
    assert fixture_transcript.duration == pytest.approx(104.5)
    assert fixture_transcript.is_manual is False
    h1 = fixture_transcript.content_hash()
    modified = fixture_transcript.model_copy(deep=True)
    modified.segments[0].text += "!"
    assert h1 == fixture_transcript.content_hash()
    assert h1 != modified.content_hash()


# ---------------------------------------------------------------- language selection


def test_selection_default_preference_order() -> None:
    prefs = ["es", "en"]
    all_tracks = [info("de", False), info("en", True), info("en", False), info("es", True), info("es", False)]
    assert select_transcript(all_tracks, prefs) == info("es", False)  # 1. es manual
    assert select_transcript([t for t in all_tracks if t != info("es", False)], prefs) == info("es", True)  # 2. es auto
    assert select_transcript([info("de", False), info("en", True), info("en", False)], prefs) == info("en", False)  # 3.
    assert select_transcript([info("de", False), info("en", True)], prefs) == info("en", True)  # 4. en auto
    assert select_transcript([info("fr", True), info("de", False)], prefs) == info("de", False)  # 5. any (manual first)
    assert select_transcript([info("fr", True)], prefs) == info("fr", True)
    assert select_transcript([], prefs) is None


def test_selection_matches_regional_variants_and_is_configurable() -> None:
    tracks = [info("es-419", True), info("en-US", False)]
    assert select_transcript(tracks, ["es", "en"]).language_code == "es-419"
    assert select_transcript(tracks, ["en", "es"]).language_code == "en-US"
    assert select_transcript([info("es-419", False), info("es", False)], ["es"]).language_code == "es"


def test_clean_language_name() -> None:
    assert clean_language_name("Spanish (auto-generated)") == "Spanish"
    assert clean_language_name("Español (generado automáticamente)") == "Español"
    assert clean_language_name("English") == "English"


# ---------------------------------------------------------------- providers


class FakeTrack:
    def __init__(self, code: str, generated: bool, snippets=None, error: Exception | None = None) -> None:
        self.language = f"{code} name"
        self.language_code = code
        self.is_generated = generated
        self._snippets = snippets or []
        self._error = error

    def fetch(self):
        if self._error:
            raise self._error
        return self._snippets


class FakeApi:
    def __init__(self, tracks=None, error: Exception | None = None) -> None:
        self.tracks = tracks or []
        self.error = error

    def list(self, video_id: str):
        if self.error:
            raise self.error
        return iter(self.tracks)


def test_youtube_provider_inspect_and_fetch() -> None:
    snippets = [SimpleNamespace(text="hola", start=0.0, duration=1.5), SimpleNamespace(text="mundo", start=1.5, duration=1.0)]
    api = FakeApi([FakeTrack("en", True), FakeTrack("es", False, snippets)])
    provider = YouTubeTranscriptProvider(api=api)  # type: ignore[arg-type]
    tracks = provider.inspect("dQw4w9WgXcQ")
    assert [(t.language_code, t.is_generated) for t in tracks] == [("en", True), ("es", False)]
    transcript = provider.fetch("dQw4w9WgXcQ", tracks[1])
    assert transcript.language_code == "es" and transcript.is_manual
    assert [s.end for s in transcript.segments] == [1.5, 2.5]


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (TranscriptsDisabled("dQw4w9WgXcQ"), ErrorCode.TRANSCRIPT_DISABLED),
        (VideoUnavailable("dQw4w9WgXcQ"), ErrorCode.VIDEO_NOT_FOUND),
        (RequestBlocked("dQw4w9WgXcQ"), ErrorCode.TRANSCRIPT_FETCH_FAILED),
        (ConnectionError("boom"), ErrorCode.TRANSCRIPT_FETCH_FAILED),
    ],
)
def test_youtube_provider_maps_errors(exc: Exception, code: ErrorCode) -> None:
    provider = YouTubeTranscriptProvider(api=FakeApi(error=exc))  # type: ignore[arg-type]
    with pytest.raises(AppError) as err:
        provider.inspect("dQw4w9WgXcQ")
    assert err.value.code == code


def test_youtube_provider_fetch_errors() -> None:
    track = FakeTrack("es", False, error=NoTranscriptFound("dQw4w9WgXcQ", ["es"], None))
    provider = YouTubeTranscriptProvider(api=FakeApi([track]))  # type: ignore[arg-type]
    with pytest.raises(AppError) as err:
        provider.fetch("dQw4w9WgXcQ", info("es", False))
    assert err.value.code == ErrorCode.TRANSCRIPT_NOT_AVAILABLE

    empty = YouTubeTranscriptProvider(api=FakeApi([FakeTrack("es", False, [])]))  # type: ignore[arg-type]
    with pytest.raises(AppError) as err:
        empty.fetch("dQw4w9WgXcQ", info("es", False))
    assert err.value.code == ErrorCode.TRANSCRIPT_NOT_AVAILABLE

    missing = YouTubeTranscriptProvider(api=FakeApi([FakeTrack("en", False)]))  # type: ignore[arg-type]
    with pytest.raises(AppError) as err:
        missing.fetch("dQw4w9WgXcQ", info("es", False))
    assert err.value.code == ErrorCode.TRANSCRIPT_NOT_AVAILABLE


def test_fixture_provider_simulations() -> None:
    provider = FixtureTranscriptProvider()
    assert provider.inspect("NOTRANSCR12") == []
    with pytest.raises(AppError) as err:
        provider.inspect("DISABLED123")
    assert err.value.code == ErrorCode.TRANSCRIPT_DISABLED
    tracks = provider.inspect("dQw4w9WgXcQ")
    assert provider.fetch("dQw4w9WgXcQ", tracks[0]).segments
