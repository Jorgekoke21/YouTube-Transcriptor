"""Optional residential proxy for the YouTube transcript provider. No network: the client is always mocked."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from youtube_transcript_api import IpBlocked, RequestBlocked
from youtube_transcript_api.proxies import WebshareProxyConfig

from app.core.config import Settings
from app.core.container import build_service, build_transcript_provider
from app.core.errors import AppError, ErrorCode
from app.main import create_app
from app.services.metadata import StaticMetadataProvider
from app.services.transcripts import youtube
from app.services.transcripts.youtube import YouTubeTranscriptProvider, build_youtube_api
from tests.conftest import StubAIProcessor

USER = "proxy-user-sentinel-8f3a"
PASSWORD = "proxy-pass-sentinel-51c9"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {"data_dir": tmp_path / "data", "ai_provider": "fake", "transcript_provider": "youtube"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def webshare_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "youtube_proxy_provider": "webshare",
        "webshare_proxy_username": USER,
        "webshare_proxy_password": PASSWORD,
    }
    values.update(overrides)
    return make_settings(tmp_path, **values)


class RecordingApi:
    """Stands in for YouTubeTranscriptApi: records constructor arguments, never touches the network."""

    instances: list[RecordingApi] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        RecordingApi.instances.append(self)

    def list(self, video_id: str):
        raise AssertionError("no network calls in tests")


@pytest.fixture
def recording_api(monkeypatch: pytest.MonkeyPatch) -> type[RecordingApi]:
    RecordingApi.instances = []
    monkeypatch.setattr(youtube, "YouTubeTranscriptApi", RecordingApi)
    return RecordingApi


# ---------------------------------------------------------------- client construction


def test_none_creates_client_without_proxy(tmp_path: Path, recording_api: type[RecordingApi]) -> None:
    build_youtube_api(make_settings(tmp_path))
    assert len(recording_api.instances) == 1
    assert recording_api.instances[0].args == () and recording_api.instances[0].kwargs == {}


def test_proxy_provider_defaults_to_none(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    assert settings.youtube_proxy_provider == "none"
    assert make_settings(tmp_path, youtube_proxy_provider="  ").youtube_proxy_provider == "none"
    # Credentials are not required (nor used) without a proxy.
    assert settings.webshare_proxy_username is None and settings.webshare_proxy_password is None


def test_webshare_configures_proxy(tmp_path: Path, recording_api: type[RecordingApi]) -> None:
    build_youtube_api(webshare_settings(tmp_path, youtube_proxy_provider="Webshare"))
    (instance,) = recording_api.instances
    assert instance.args == ()
    config = instance.kwargs["proxy_config"]
    assert isinstance(config, WebshareProxyConfig)
    assert config.proxy_username == USER and config.proxy_password == PASSWORD
    assert config._filter_ip_locations == []


def test_container_wires_the_proxy_into_the_provider(tmp_path: Path, recording_api: type[RecordingApi]) -> None:
    provider = build_transcript_provider(webshare_settings(tmp_path))
    assert isinstance(provider, YouTubeTranscriptProvider)
    assert isinstance(recording_api.instances[0].kwargs["proxy_config"], WebshareProxyConfig)

    recording_api.instances = []
    build_transcript_provider(make_settings(tmp_path))
    assert recording_api.instances[0].kwargs == {}


def test_real_client_accepts_the_config(tmp_path: Path) -> None:
    # The real constructor only builds a requests Session: no request is sent.
    api = build_youtube_api(webshare_settings(tmp_path))
    assert isinstance(api, youtube.YouTubeTranscriptApi)


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("overrides", "missing"),
    [
        ({"webshare_proxy_username": None}, "WEBSHARE_PROXY_USERNAME is required"),
        ({"webshare_proxy_password": ""}, "WEBSHARE_PROXY_PASSWORD is required"),
        (
            {"webshare_proxy_username": " ", "webshare_proxy_password": None},
            "WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD",
        ),
    ],
)
def test_webshare_requires_credentials(tmp_path: Path, overrides: dict[str, Any], missing: str) -> None:
    with pytest.raises(ValidationError) as err:
        webshare_settings(tmp_path, **overrides)
    assert missing in str(err.value)
    assert "YOUTUBE_PROXY_PROVIDER=webshare" in str(err.value)


@pytest.mark.parametrize("present", ["webshare_proxy_username", "webshare_proxy_password"])
def test_validation_error_does_not_echo_credentials(tmp_path: Path, present: str) -> None:
    secret = "tiny-secret"  # short, so pydantic would not truncate it if it echoed the input
    with pytest.raises(ValidationError) as err:
        make_settings(tmp_path, youtube_proxy_provider="webshare", openai_api_key="sk-x", **{present: secret})
    assert secret not in str(err.value) and "sk-x" not in str(err.value)
    assert secret not in repr(err.value)


def test_unknown_proxy_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="YOUTUBE_PROXY_PROVIDER must be one of none, webshare"):
        make_settings(tmp_path, youtube_proxy_provider="brightdata")


def test_app_fails_to_start_with_incomplete_proxy_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.core import config

    monkeypatch.setenv("YOUTUBE_PROXY_PROVIDER", "webshare")
    monkeypatch.setenv("WEBSHARE_PROXY_USERNAME", USER)
    monkeypatch.delenv("WEBSHARE_PROXY_PASSWORD", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config.Settings, "model_config", {**config.Settings.model_config, "env_file": None})
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError, match="WEBSHARE_PROXY_PASSWORD is required") as err:
            create_app()
        assert USER not in str(err.value)
    finally:
        config.get_settings.cache_clear()


# ---------------------------------------------------------------- locations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("es", ["es"]),
        ("es,de,fr", ["es", "de", "fr"]),
        (" ES , de,, FR ,", ["es", "de", "fr"]),
    ],
)
def test_locations_are_parsed(tmp_path: Path, raw: str, expected: list[str]) -> None:
    assert webshare_settings(tmp_path, webshare_proxy_locations=raw).webshare_proxy_location_list == expected


def test_locations_are_passed_as_filter(tmp_path: Path, recording_api: type[RecordingApi]) -> None:
    build_youtube_api(webshare_settings(tmp_path, webshare_proxy_locations="es, de ,fr"))
    config = recording_api.instances[0].kwargs["proxy_config"]
    assert config._filter_ip_locations == ["es", "de", "fr"]


# ---------------------------------------------------------------- secrecy


def test_credentials_never_leak(tmp_path: Path, caplog: pytest.LogCaptureFixture, recording_api: type[RecordingApi]) -> None:
    caplog.set_level(logging.DEBUG)
    settings = webshare_settings(tmp_path, webshare_proxy_locations="es")
    build_youtube_api(settings)
    config = recording_api.instances[0].kwargs["proxy_config"]

    exposed = [repr(settings), str(settings), settings.model_dump_json(), repr(config), str(config), caplog.text]
    exposed += [str(v) for v in settings.model_dump().values()]
    for text in exposed:
        assert USER not in text and PASSWORD not in text
    assert "webshare" in caplog.text


def test_blocked_errors_keep_code_and_log_safely(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    class BlockedApi:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def list(self, video_id: str):
            raise self.exc

    caplog.set_level(logging.INFO)
    for exc in (RequestBlocked("dQw4w9WgXcQ"), IpBlocked("dQw4w9WgXcQ")):
        provider = YouTubeTranscriptProvider(api=BlockedApi(exc))  # type: ignore[arg-type]
        with pytest.raises(AppError) as err:
            provider.inspect("dQw4w9WgXcQ")
        assert err.value.code == ErrorCode.TRANSCRIPT_FETCH_FAILED
        assert USER not in str(err.value) and PASSWORD not in str(err.value)
    assert caplog.text.count("youtube_ip_blocked") == 2
    assert USER not in caplog.text and PASSWORD not in caplog.text


# ---------------------------------------------------------------- health


def test_health_reports_proxy_without_credentials(tmp_path: Path, recording_api: type[RecordingApi]) -> None:
    settings = webshare_settings(tmp_path)
    service = build_service(settings, metadata=StaticMetadataProvider(), ai=StubAIProcessor())
    client = TestClient(create_app(settings, service=service))
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["transcript_proxy"] == "webshare"
    assert USER not in response.text and PASSWORD not in response.text


def test_health_reports_no_proxy_by_default(client: TestClient) -> None:
    assert client.get("/api/health").json()["transcript_proxy"] == "none"
