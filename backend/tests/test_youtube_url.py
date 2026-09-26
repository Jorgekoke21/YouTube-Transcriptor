import pytest

from app.core.errors import AppError, ErrorCode
from app.utils.youtube_url import canonical_url, extract_video_id, is_valid_video_id, require_video_id, timestamp_url

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}",
        f"http://m.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/watch?feature=share&v={VID}&t=42s",
        f"https://www.youtube.com/watch?v={VID}&list=PL123&index=2",
        f"www.youtube.com/watch?v={VID}",
        f"youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}",
        f"https://youtu.be/{VID}?t=10",
        f"youtu.be/{VID}",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://youtube.com/shorts/{VID}?feature=share",
        f"https://www.youtube.com/live/{VID}",
        f"https://www.youtube.com/live/{VID}?si=abc",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube-nocookie.com/embed/{VID}",
        f"https://music.youtube.com/watch?v={VID}",
        f"  https://youtu.be/{VID}  ",
    ],
)
def test_extracts_id_from_supported_urls(url: str) -> None:
    assert extract_video_id(url) == VID


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://vimeo.com/123456",
        "https://www.youtube.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQextra",
        "https://www.youtube.com/watch?v=dQw4w9WgX$Q",
        "https://youtu.be/",
        "https://www.youtube.com/channel/UC1234567890",
        "https://www.youtube.com/@somechannel",
        "https://evil.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "javascript:alert(1)",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ " + "x" * 3000,
    ],
)
def test_rejects_invalid_urls(url: str) -> None:
    assert extract_video_id(url) is None


def test_require_video_id_raises_app_error() -> None:
    with pytest.raises(AppError) as exc:
        require_video_id("https://vimeo.com/1")
    assert exc.value.code == ErrorCode.INVALID_YOUTUBE_URL


def test_id_validation() -> None:
    assert is_valid_video_id(VID)
    assert not is_valid_video_id("../../etc/pa")
    assert not is_valid_video_id(None)


def test_url_builders() -> None:
    assert canonical_url(VID) == f"https://www.youtube.com/watch?v={VID}"
    assert timestamp_url(VID, 1122.4) == f"https://www.youtube.com/watch?v={VID}&t=1122s"
    assert timestamp_url(VID, -3) == f"https://www.youtube.com/watch?v={VID}&t=0s"
