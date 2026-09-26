"""Multi-URL batches and document collections through the API.

Transcripts come from fixtures, metadata is static and the AI is the deterministic fake LLM running the real
pipelines (single and consolidated): no YouTube, no OpenAI.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.container import build_service
from app.core.errors import AppError, ErrorCode
from app.main import create_app
from app.models.transcript import Transcript, TranscriptInfo, VideoMetadata
from app.services.ai.fake import build_fake_processor
from app.services.ai.openai_processor import PipelineAIProcessor
from app.services.ai.pipeline import PipelineConfig
from app.services.metadata import MetadataProvider
from app.services.transcripts.fixture import FixtureTranscriptProvider
from app.utils.youtube_url import canonical_url

A, B, C = "AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"
TITLES = {A: "Divergencias con RSI", B: "RSI y MACD", C: "Gestión del stop"}
LINK_RE = re.compile(r"\]\(https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})&t=(\d+)s\)")


def url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


class TitledMetadata(MetadataProvider):
    def get(self, video_id: str) -> VideoMetadata:
        return VideoMetadata(
            video_id=video_id,
            url=canonical_url(video_id),
            title=TITLES.get(video_id, f"Vídeo {video_id}"),
            channel=f"Canal {video_id[0]}",
        )


class VariantTranscripts(FixtureTranscriptProvider):
    """A: the fixture. B: same first half (→ agreements with A), different second half. C: all different.

    Ids starting with MISSING simulate a video that does not exist.
    """

    def inspect(self, video_id: str) -> list[TranscriptInfo]:
        if video_id.startswith("MISSING"):
            raise AppError(ErrorCode.VIDEO_NOT_FOUND)
        return super().inspect(video_id)

    def fetch(self, video_id: str, track: TranscriptInfo) -> Transcript:
        transcript = super().fetch(video_id, track)
        half = len(transcript.segments) // 2
        for i, seg in enumerate(transcript.segments):
            if (video_id.startswith("B") and i >= half) or video_id.startswith("C"):
                seg.text = f"{seg.text} (según el vídeo {video_id[0]})"
        return transcript


@pytest.fixture
def fake_ai() -> PipelineAIProcessor:
    return build_fake_processor(config=PipelineConfig(max_workers=1))  # type: ignore[return-value]


def make_client(settings: Settings, ai) -> TestClient:
    service = build_service(settings, transcripts=VariantTranscripts(), metadata=TitledMetadata(), ai=ai)
    return TestClient(create_app(settings, service=service))


@pytest.fixture
def api(settings: Settings, fake_ai) -> TestClient:
    return make_client(settings, fake_ai)


def stage_calls(ai: PipelineAIProcessor, stage: str) -> int:
    return ai.llm.calls.count(stage)  # type: ignore[attr-defined]


def create_collection(client: TestClient, urls: list[str], **body) -> dict:
    response = client.post("/api/document-collections", json={"urls": urls, **body})
    assert response.status_code == 202, response.text
    # TestClient runs background tasks before returning, so the collection is already processed.
    return client.get(f"/api/document-collections/{response.json()['id']}").json()


# ---------------------------------------------------------------- batch inspection


def test_inspect_batch_multiple_urls_dedup_invalid_and_errors(api: TestClient) -> None:
    lines = [
        "",
        "https://youtube.com/watch?v=AAAAAAAAAAA",
        "https://youtu.be/BBBBBBBBBBB?si=abc",
        "esto no es una url",
        "https://www.youtube.com/shorts/AAAAAAAAAAA",
        "https://youtu.be/NOTRANSCR12",
        "youtube.com/watch?v=DISABLED123",
        "   ",
        "https://youtu.be/MISSING0001",
    ]
    r = api.post("/api/videos/inspect-batch", json={"urls": lines})
    assert r.status_code == 200, r.text
    body = r.json()

    videos = body["videos"]
    assert [v["video"]["video_id"] for v in videos] == [A, B, "NOTRANSCR12", "DISABLED123", "MISSING0001"]  # paste order
    assert [v["position"] for v in videos] == [0, 1, 2, 3, 4]
    assert body["valid_count"] == 5 and body["available_count"] == 2 and body["max_videos"] == 20
    assert [i["input"] for i in body["invalid"]] == ["esto no es una url"]
    assert body["invalid"][0]["error"]["code"] == "INVALID_YOUTUBE_URL"
    assert [(d["video_id"], d["duplicate_of"]) for d in body["duplicates"]] == [(A, 0)]

    a = videos[0]
    assert a["transcript_available"] is True and a["error"] is None
    assert a["video"]["title"] == "Divergencias con RSI" and a["video"]["channel"] == "Canal A"
    assert a["selected_transcript"]["language_code"] == "es" and a["selected_transcript"]["kind"] == "generated"
    assert a["duration_seconds"] is None  # unknown until the transcript is stored locally
    # one bad video never prevents inspecting the others: each carries its own error
    assert [v["error"]["code"] if v["error"] else None for v in videos[2:]] == [
        "TRANSCRIPT_NOT_AVAILABLE",
        "TRANSCRIPT_DISABLED",
        "VIDEO_NOT_FOUND",
    ]
    assert all(not v["transcript_available"] for v in videos[2:])


def test_inspect_batch_reports_stored_duration(api: TestClient) -> None:
    api.post("/api/documents", json={"url": url(A)})
    video = api.post("/api/videos/inspect-batch", json={"urls": [url(A), url(B)]}).json()["videos"]
    assert video[0]["duration_seconds"] > 0 and video[1]["duration_seconds"] is None


def test_batch_limit(settings: Settings, fake_ai) -> None:
    assert Settings(_env_file=None, ai_provider="fake").max_videos_per_batch == 20  # type: ignore[call-arg]
    client = make_client(settings.model_copy(update={"max_videos_per_batch": 2}), fake_ai)
    ok = client.post("/api/videos/inspect-batch", json={"urls": [url(A), url(A), url(B)]})
    assert ok.status_code == 200  # duplicates do not count
    for path, body in (
        ("/api/videos/inspect-batch", {"urls": [url(A), url(B), url(C)]}),
        ("/api/document-collections", {"urls": [url(A), url(B), url(C)], "output_mode": "individual"}),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "BATCH_TOO_LARGE"
        assert "máximo por lote es 2" in r.json()["error"]["message"]


# ---------------------------------------------------------------- validation


def test_create_collection_validation(api: TestClient) -> None:
    cases = [
        ({"urls": [url(A)], "output_mode": "consolidated"}, 422, "NOT_ENOUGH_VIDEOS"),
        ({"urls": [url(A), url(A)], "output_mode": "both"}, 422, "NOT_ENOUGH_VIDEOS"),  # one video after dedup
        ({"urls": [url(A), url(B)], "document_type": "clean_transcript"}, 400, "INVALID_REQUEST"),
        ({"urls": [url(A), "https://vimeo.com/1"]}, 400, "INVALID_YOUTUBE_URL"),
        ({"urls": [url(A), url(B)], "output_mode": "everything"}, 400, "INVALID_REQUEST"),
        ({"urls": []}, 400, "INVALID_REQUEST"),
    ]
    for body, status, code in cases:
        r = api.post("/api/document-collections", json=body)
        assert (r.status_code, r.json()["error"]["code"]) == (status, code), body
    assert api.get("/api/document-collections/999").json()["error"]["code"] == "COLLECTION_NOT_FOUND"


# ---------------------------------------------------------------- individual mode


def test_individual_mode_reuses_the_single_video_pipeline(api: TestClient, fake_ai) -> None:
    collection = create_collection(api, [url(A), url(B)], output_mode="individual", document_type="summary")
    assert collection["status"] == "completed", collection
    assert collection["downloads"] == {} and collection["markdown"] is None
    assert [v["video"]["video_id"] for v in collection["videos"]] == [A, B]

    docs = [api.get(f"/api/documents/{v['document_id']}").json() for v in collection["videos"]]
    assert all(d["status"] == "completed" and d["document_type"] == "summary" for d in docs)
    assert all(d["prompt_version"] == "base-v2+pipeline-v6+summary-v1" for d in docs)
    assert all(v["document_status"] == "completed" for v in collection["videos"])

    # a later single-URL request for the same video is the very same document (same cache key)
    single = api.post("/api/documents", json={"url": url(A), "document_type": "summary"}).json()
    assert api.get(f"/api/documents/{single['id']}").json()["reused_from_id"] == docs[0]["id"]


def test_single_url_document_has_no_multi_video_fields(api: TestClient) -> None:
    created = api.post("/api/documents", json={"url": url(A), "document_type": "trading"}).json()
    doc = api.get(f"/api/documents/{created['id']}").json()
    assert doc["status"] == "completed" and doc["prompt_version"] == "base-v2+pipeline-v6+trading-v3"
    processed = doc["document"]
    assert "citations" not in processed and "sources" not in processed and processed["source_video"]["video_id"] == A
    ranges = [r for s in processed["sections"] for r in s["source_ranges"]]
    ranges += [r for s in processed["sections"] for b in s["blocks"] for r in b["source_ranges"]]
    assert ranges and all(set(r) == {"start", "end"} for r in ranges)
    assert "·" not in "".join(t for t, _ in LINK_RE.findall(doc["markdown"]))
    assert "Fuentes utilizadas" not in doc["markdown"] and f"watch?v={A}&t=" in doc["markdown"]


# ---------------------------------------------------------------- consolidated mode


def test_consolidated_end_to_end_attribution_and_downloads(api: TestClient) -> None:
    pasted = [url(C), "", url(A), f"https://youtu.be/{A}", url(B)]
    collection = create_collection(api, pasted, output_mode="consolidated", document_type="full_notes")
    assert collection["status"] == "completed", collection
    assert collection["video_count"] == 3  # deduplicated by youtube_id
    assert all(v["status"] == "included" and v["document_id"] is None for v in collection["videos"])

    doc = collection["document"]
    assert doc["kind"] == "collection"
    source_ids = {C, A, B}
    # visible sources: first appearance in the document (A and B open "Parte 1" with a shared idea), not paste order
    assert [s["video_id"] for s in doc["sources"]] == [A, B, C]
    assert [v["video"]["video_id"] for v in collection["videos"]] == [A, B, C]  # the result page uses the same order
    assert [v["position"] for v in collection["videos"]] == [1, 2, 0]  # the input order survives only as metadata

    # every range of the document knows its video
    ranges = [r for s in doc["sections"] for r in s["source_ranges"]]
    ranges += [r for s in doc["sections"] for b in s["blocks"] for r in b["source_ranges"]]
    assert ranges and all(r["video_id"] in source_ids for r in ranges)
    # every citation: youtube_id + title + start/end + a link that opens that video at that second
    refs = {"V1": A, "V2": B, "V3": C}  # canonical: sorted youtube_id, whatever the paste order
    for c in doc["citations"]:
        assert c["youtube_id"] == refs[c["evidence_id"].split(".")[0]]
        assert c["video_title"] == TITLES[c["youtube_id"]]
        assert c["source_url"] == f"https://www.youtube.com/watch?v={c['youtube_id']}&t={int(c['source_start'])}s"
        assert c["source_end"] >= c["source_start"]
    assert {c["youtube_id"] for c in doc["citations"]} == source_ids

    md = collection["markdown"]
    assert "**Fuentes utilizadas:**" in md
    assert md.index("1. **Divergencias con RSI**") < md.index("2. **RSI y MACD**") < md.index("3. **Gestión del stop**")
    assert "## Coincidencias entre fuentes" in md  # A and B share half of their content
    assert {vid for vid, _ in LINK_RE.findall(md)} == source_ids
    assert "[Divergencias con RSI · " in md and "[RSI y MACD · " in md

    for fmt, magic in {"markdown": b"# ", "docx": b"PK", "pdf": b"%PDF", "txt": b"DOCUMENTO CONSOLIDADO"}.items():
        r = api.get(collection["downloads"][fmt])
        assert r.status_code == 200, fmt
        assert r.content.startswith(magic), fmt

    history = api.get("/api/document-collections").json()
    assert history[0]["id"] == collection["id"] and history[0]["kind"] == "collection"
    assert history[0]["video_count"] == 3 and history[0]["title"] == doc["title"]


def test_collection_is_an_unordered_set(api: TestClient, fake_ai) -> None:  # TEST 5, 7, 8, 10
    first = create_collection(api, [url(A), url(B), url(C)], output_mode="consolidated")
    assert first["status"] == "completed" and first["reused_from_id"] is None
    calls = len(fake_ai.llm.calls)  # type: ignore[attr-defined]

    for order in ([url(C), url(A), url(B)], [url(B), url(C), url(A)], [url(A), url(A), f"https://youtu.be/{B}", url(C)]):
        again = create_collection(api, order, output_mode="consolidated")
        assert again["identity"] == first["identity"]  # same collection identity
        assert again["reused_from_id"] == first["id"] and again["progress"]["cache_hit"] is True  # same consolidated cache
        assert again["document"] == first["document"] and again["markdown"] == first["markdown"]  # same document
        assert [v["video"]["video_id"] for v in again["videos"]] == [v["video"]["video_id"] for v in first["videos"]]
    assert len(fake_ai.llm.calls) == calls  # nothing regenerated

    service = api.app.state.collections  # type: ignore[attr-defined]
    keys = {service.get_record(c["id"]).cache_key for c in api.get("/api/document-collections").json()}
    assert len(keys) == 1  # one fingerprint for the four inputs

    other = create_collection(api, [url(A), url(B)], output_mode="consolidated")
    assert other["identity"] != first["identity"] and other["reused_from_id"] is None


def test_partial_selection_only_processes_selected_videos(api: TestClient) -> None:
    inspected = api.post("/api/videos/inspect-batch", json={"urls": [url(A), url(B), url(C)]}).json()
    selected = [v["input"] for v in inspected["videos"] if v["video"]["video_id"] != B]
    collection = create_collection(api, selected, output_mode="consolidated")
    assert [v["video"]["video_id"] for v in collection["videos"]] == [A, C]
    assert {s["video_id"] for s in collection["document"]["sources"]} == {A, C}
    assert B not in collection["markdown"]


def test_video_without_transcript_inside_batch(api: TestClient) -> None:
    collection = create_collection(api, [url(A), "https://youtu.be/NOTRANSCR12", url(B)], output_mode="consolidated")
    assert collection["status"] == "completed", collection
    failed = next(v for v in collection["videos"] if v["video"]["video_id"] == "NOTRANSCR12")
    assert failed["status"] == "failed" and failed["error"]["code"] == "TRANSCRIPT_NOT_AVAILABLE"
    assert {s["video_id"] for s in collection["document"]["sources"]} == {A, B}

    not_enough = create_collection(api, [url(A), "https://youtu.be/NOTRANSCR12"], output_mode="both")
    assert not_enough["status"] == "failed" and not_enough["error"]["code"] == "NOT_ENOUGH_VIDEOS"
    assert [v["document_status"] for v in not_enough["videos"]] == ["completed", "failed"]  # individual docs still made


# ---------------------------------------------------------------- caches


def test_extraction_cache_is_shared_by_individual_and_consolidated(api: TestClient, fake_ai) -> None:
    single = api.post("/api/documents", json={"url": url(A), "document_type": "trading"}).json()
    assert api.get(f"/api/documents/{single['id']}").json()["status"] == "completed"
    per_video = stage_calls(fake_ai, "extraction")
    assert per_video >= 1

    collection = create_collection(api, [url(A), url(B)], output_mode="consolidated", document_type="trading")
    assert collection["status"] == "completed"
    assert [v["extraction_cached"] for v in collection["videos"]] == [True, False]
    assert collection["progress"]["extraction_cache_hits"] == 1
    assert stage_calls(fake_ai, "extraction") == 2 * per_video  # only B was extracted

    # "both": the individual documents extract (or reuse) first; the consolidated document reuses every extraction
    both = create_collection(api, [url(C), url(A)], output_mode="both", document_type="trading")
    assert both["status"] == "completed"
    assert [v["extraction_cached"] for v in both["videos"]] == [True, True]
    assert stage_calls(fake_ai, "extraction") == 3 * per_video  # only C, once

    # a different document type needs its own extraction
    create_collection(api, [url(A), url(B)], output_mode="consolidated", document_type="summary")
    assert stage_calls(fake_ai, "extraction") == 5 * per_video


def test_single_document_force_regenerate_refreshes_extraction(api: TestClient, fake_ai) -> None:
    api.post("/api/documents", json={"url": url(A)})
    before = stage_calls(fake_ai, "extraction")
    api.post("/api/documents", json={"url": url(A), "force_regenerate": True})
    assert stage_calls(fake_ai, "extraction") == 2 * before  # "regenerar sin caché" really starts from scratch


def test_consolidated_cache(api: TestClient, fake_ai) -> None:
    first = create_collection(api, [url(A), url(B)], output_mode="consolidated")
    calls = len(fake_ai.llm.calls)  # type: ignore[attr-defined]

    same_set = create_collection(api, [url(B), url(A)], output_mode="consolidated")  # order does not change the key
    assert same_set["status"] == "completed"
    assert same_set["reused_from_id"] == first["id"] and same_set["progress"]["cache_hit"] is True
    assert same_set["markdown"] == first["markdown"]
    assert len(fake_ai.llm.calls) == calls  # the AI was not paid twice  # type: ignore[attr-defined]

    other_lang = create_collection(api, [url(A), url(B)], output_mode="consolidated", output_language="en")
    assert other_lang["reused_from_id"] is None and "## Points of agreement between sources" in other_lang["markdown"]

    calls = len(fake_ai.llm.calls)  # type: ignore[attr-defined]
    forced = create_collection(api, [url(A), url(B)], output_mode="consolidated", force_regenerate=True)
    assert forced["reused_from_id"] is None
    new_stages = fake_ai.llm.calls[calls:]  # type: ignore[attr-defined]
    assert "consolidation" in new_stages and "extraction" in new_stages


# ---------------------------------------------------------------- robustness


def test_processor_without_collection_support_fails_clearly(client: TestClient) -> None:
    # the default test service uses a stub AIProcessor that only implements the single-video contract
    collection = create_collection(client, [url(A), url(B)], output_mode="consolidated")
    assert collection["status"] == "failed" and collection["error"]["code"] == "AI_PROCESSING_FAILED"
    assert "consolidados" in collection["error"]["message"]


def test_interrupted_collections_are_failed_on_startup(settings: Settings, fake_ai) -> None:
    client = make_client(settings, fake_ai)
    service = client.app.state.collections  # type: ignore[attr-defined]
    record = service.create(
        [url(A), url(B)], __import__("app.models.document", fromlist=["x"]).DocumentType.SUMMARY, "es", "consolidated"
    )
    service.repo.update(record.id, status="processing")
    restarted = TestClient(create_app(settings, service=service.documents))
    assert restarted.get(f"/api/document-collections/{record.id}").json()["status"] == "failed"
