"""Endpoint tests with a mocked TranscriptProvider (fixture) and a mocked AIProcessor (stub)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.errors import AppError, ErrorCode
from app.main import create_app
from app.services.transcripts.fixture import FixtureTranscriptProvider
from tests.conftest import VIDEO_URL, StubAIProcessor


def create(client: TestClient, **body) -> dict:
    response = client.post("/api/documents", json={"url": VIDEO_URL, **body})
    assert response.status_code == 202, response.text
    # TestClient runs background tasks before returning, so the document is already processed.
    return client.get(f"/api/documents/{response.json()['id']}").json()


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "api_key" not in str(body).lower()


def test_inspect_video_with_transcript(client: TestClient) -> None:
    body = client.post("/api/videos/inspect", json={"url": "https://youtu.be/dQw4w9WgXcQ"}).json()
    assert body["valid_url"] is True
    assert body["video"]["video_id"] == "dQw4w9WgXcQ"
    assert body["video"]["title"]
    assert body["transcript_available"] is True
    assert body["selected_transcript"] == {
        "language": "Español",
        "language_code": "es",
        "is_generated": True,
        "is_manual": False,
        "kind": "generated",
    }


def test_inspect_video_without_transcript(client: TestClient) -> None:
    body = client.post("/api/videos/inspect", json={"url": "https://youtu.be/NOTRANSCR12"}).json()
    assert body["transcript_available"] is False
    assert body["transcript_error"]["code"] == "TRANSCRIPT_NOT_AVAILABLE"
    disabled = client.post("/api/videos/inspect", json={"url": "https://youtu.be/DISABLED123"}).json()
    assert disabled["transcript_error"]["code"] == "TRANSCRIPT_DISABLED"


def test_invalid_url_and_invalid_body(client: TestClient) -> None:
    r = client.post("/api/videos/inspect", json={"url": "https://vimeo.com/1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_YOUTUBE_URL"
    r = client.post("/api/documents", json={"url": "https://vimeo.com/1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_YOUTUBE_URL"
    r = client.post("/api/documents", json={"url": VIDEO_URL, "document_type": "poem"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = client.post("/api/documents", json={"url": VIDEO_URL, "output_language": "fr"})
    assert r.status_code == 400


def test_generate_document_end_to_end(client: TestClient, stub_ai: StubAIProcessor, settings) -> None:
    doc = create(client, document_type="full_notes", output_language="es")
    assert doc["status"] == "completed", doc
    assert doc["document_type_label"] == "Apuntes completos"
    assert doc["progress"]["word_count"] > 100
    assert doc["progress"]["transcript_type"] == "generated"
    assert doc["prompt_version"] == "base-v2+pipeline-v6+full-notes-v1"
    assert doc["usage"]["calls"] == 1
    assert "## Confirmación de entrada" in doc["markdown"]
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=49s" in doc["markdown"]
    assert doc["document"]["source_video"]["transcript_language_code"] == "es"
    assert stub_ai.calls == [("full_notes", "es")]

    video_dir = settings.videos_dir / "dQw4w9WgXcQ"
    assert (video_dir / "metadata.json").exists() and (video_dir / "transcript.json").exists()

    expected = {
        "markdown": ("text/markdown", b"# Divergencias RSI"),
        "docx": ("wordprocessingml", b"PK"),
        "pdf": ("application/pdf", b"%PDF"),
        "txt": ("text/plain", b"DIVERGENCIAS RSI"),
    }
    for fmt, (media, magic) in expected.items():
        r = client.get(doc["downloads"][fmt])
        assert r.status_code == 200, fmt
        assert media in r.headers["content-type"]
        assert r.content.startswith(magic)
        assert "Apuntes" not in r.headers["content-disposition"]  # ascii-safe english label
        assert 'filename="Divergencias_RSI' in r.headers["content-disposition"]


def test_cache_reuse_and_force_regenerate(client: TestClient, stub_ai: StubAIProcessor) -> None:
    first = create(client, document_type="trading")
    second = create(client, document_type="trading")
    assert second["status"] == "completed"
    assert second["reused_from_id"] == first["id"]
    assert second["progress"]["cache_hit"] is True
    assert second["markdown"] == first["markdown"]
    assert len(stub_ai.calls) == 1  # the AI was not paid twice

    other_lang = create(client, document_type="trading", output_language="en")
    assert other_lang["reused_from_id"] is None
    forced = create(client, document_type="trading", force_regenerate=True)
    assert forced["reused_from_id"] is None
    assert len(stub_ai.calls) == 3

    history = client.get("/api/documents").json()
    ids = [d["id"] for d in history]
    assert second["id"] not in ids  # reused entries don't duplicate the history
    assert ids == sorted(ids, reverse=True)


def test_video_without_transcript_fails_without_ai(client: TestClient, stub_ai: StubAIProcessor) -> None:
    r = client.post("/api/documents", json={"url": "https://www.youtube.com/watch?v=NOTRANSCR12"})
    doc = client.get(f"/api/documents/{r.json()['id']}").json()
    assert doc["status"] == "failed"
    assert doc["error"]["code"] == "TRANSCRIPT_NOT_AVAILABLE"
    assert doc["markdown"] is None and doc["downloads"] == {}
    assert stub_ai.calls == []
    r = client.get(f"/api/documents/{doc['id']}/download/pdf")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DOCUMENT_NOT_READY"


def test_ai_failure_is_reported_without_internals(settings, service) -> None:
    service.ai = StubAIProcessor(fail_with=AppError(ErrorCode.AI_PROCESSING_FAILED))
    client = TestClient(create_app(settings, service=service))
    doc = create(client)
    assert doc["status"] == "failed" and doc["error"]["code"] == "AI_PROCESSING_FAILED"

    service.ai = StubAIProcessor(fail_with=RuntimeError("secret internal detail"))
    doc = create(client)
    assert doc["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in doc["error"]["message"] and "Traceback" not in str(doc)


def test_transcript_endpoint(client: TestClient) -> None:
    create(client)
    body = client.get("/api/videos/dQw4w9WgXcQ/transcript").json()
    assert body["language_code"] == "es" and body["is_generated"] is True
    first = body["segments"][0]
    assert set(first) == {"text", "start", "duration", "end"}
    assert client.get("/api/videos/bad..id/transcript").status_code == 400


def test_not_found_and_bad_format(client: TestClient) -> None:
    r = client.get("/api/documents/999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    doc = create(client)
    assert client.get(f"/api/documents/{doc['id']}/download/exe").status_code == 400


def test_interrupted_documents_are_failed_on_startup(settings, service) -> None:
    record = service.create(VIDEO_URL, __import__("app.models.document", fromlist=["DocumentType"]).DocumentType.SUMMARY, "es")
    service.repo.update(record.id, status="processing")
    client = TestClient(create_app(settings, service=service))
    doc = client.get(f"/api/documents/{record.id}").json()
    assert doc["status"] == "failed"


def test_cors_is_not_wildcard(client: TestClient) -> None:
    ok = client.options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert bad.headers.get("access-control-allow-origin") is None


def test_error_detail_only_when_more_specific(client: TestClient) -> None:
    generic = client.post("/api/videos/inspect", json={"url": "https://vimeo.com/1"}).json()["error"]
    assert generic["code"] == "INVALID_YOUTUBE_URL" and generic["detail"] is None
    specific = client.post("/api/documents", json={"url": VIDEO_URL, "document_type": "poem"}).json()["error"]
    assert specific["code"] == "INVALID_REQUEST" and "document_type" in specific["detail"]


def test_progress_never_claims_a_missing_video_was_found(settings, service) -> None:
    class MissingVideoProvider(FixtureTranscriptProvider):
        def inspect(self, video_id: str):
            raise AppError(ErrorCode.VIDEO_NOT_FOUND)

    service.transcripts = MissingVideoProvider()
    client = TestClient(create_app(settings, service=service))
    doc = create(client)
    assert doc["error"]["code"] == "VIDEO_NOT_FOUND"
    assert not doc["progress"].get("video_found")

    service.transcripts = FixtureTranscriptProvider()
    created = client.post("/api/documents", json={"url": "https://youtu.be/NOTRANSCR12"})
    doc = client.get(f"/api/documents/{created.json()['id']}").json()
    assert doc["error"]["code"] == "TRANSCRIPT_NOT_AVAILABLE"
    assert doc["progress"]["video_found"] is True  # the video exists, it just has no transcript
