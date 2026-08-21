from types import SimpleNamespace

import pytest

from research.policy_catalyst_transport_v1 import fetch_bounded_official_text


SOURCE = {"allowed_host": "www.sec.gov"}


class FakeResponse:
    def __init__(self, *, url, chunks, headers=None, status_code=200, encoding="utf-8"):
        self.url = url
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.status_code = status_code
        self.encoding = encoding
        self.yielded = 0

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def stream(self, method, url):
        self.calls.append((method, url))
        return FakeStreamContext(self.response)


@pytest.mark.asyncio
async def test_streaming_transport_returns_only_official_bounded_body():
    response = FakeResponse(
        url="https://www.sec.gov/news/pressreleases.rss",
        chunks=[b"abc", b"def"],
        headers={"content-length": "6"},
    )
    client = FakeClient(response)

    text, status, final_url, size = await fetch_bounded_official_text(
        client,
        source=SOURCE,
        requested_url="https://www.sec.gov/news/pressreleases.rss",
        max_bytes=8,
    )

    assert text == "abcdef"
    assert status == 200
    assert final_url == "https://www.sec.gov/news/pressreleases.rss"
    assert size == 6
    assert response.yielded == 2
    assert client.calls == [("GET", "https://www.sec.gov/news/pressreleases.rss")]


@pytest.mark.asyncio
async def test_streaming_transport_aborts_on_first_chunk_crossing_cap():
    response = FakeResponse(
        url="https://www.sec.gov/news/pressreleases.rss",
        chunks=[b"1234", b"5678", b"never-read"],
    )
    with pytest.raises(ValueError, match="exceeds frozen byte bound"):
        await fetch_bounded_official_text(
            FakeClient(response),
            source=SOURCE,
            requested_url="https://www.sec.gov/news/pressreleases.rss",
            max_bytes=6,
        )
    assert response.yielded == 2


@pytest.mark.asyncio
async def test_streaming_transport_rejects_cross_host_redirect_before_body_read():
    response = FakeResponse(
        url="https://example.com/redirected-feed.xml",
        chunks=[b"must-not-be-read"],
    )
    with pytest.raises(ValueError, match="redirected outside"):
        await fetch_bounded_official_text(
            FakeClient(response),
            source=SOURCE,
            requested_url="https://www.sec.gov/news/pressreleases.rss",
            max_bytes=1024,
        )
    assert response.yielded == 0


@pytest.mark.asyncio
async def test_streaming_transport_rejects_announced_oversize_before_body_read():
    response = FakeResponse(
        url="https://www.sec.gov/news/pressreleases.rss",
        chunks=[b"must-not-be-read"],
        headers={"content-length": "2049"},
    )
    with pytest.raises(ValueError, match="exceeds frozen byte bound"):
        await fetch_bounded_official_text(
            FakeClient(response),
            source=SOURCE,
            requested_url="https://www.sec.gov/news/pressreleases.rss",
            max_bytes=2048,
        )
    assert response.yielded == 0
