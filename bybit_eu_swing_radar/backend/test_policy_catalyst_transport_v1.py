from types import SimpleNamespace

import pytest

from research.policy_catalyst_transport_v1 import (
    MAX_SOURCE_RESPONSE_BYTES,
    bounded_response_text,
    contract,
    validate_response_origin,
)


SOURCE = {
    "allowed_host": "www.sec.gov",
}


def _response(*, url: str, content: bytes = b"ok", headers=None):
    return SimpleNamespace(
        url=url,
        content=content,
        text=content.decode("utf-8", errors="replace"),
        headers=headers or {},
    )


def test_transport_contract_is_research_only_and_non_mutating():
    value = contract()
    assert value["research_only"] is True
    assert value["primary_source_only"] is True
    assert value["https_required"] is True
    assert value["cross_host_redirect_allowed"] is False
    assert value["context_only"] is True
    assert value["hard_gate"] is False
    assert value["score_mutation"] is False
    assert value["ranking_mutation"] is False
    assert value["eligibility_mutation"] is False
    assert value["execution_mutation"] is False


def test_same_official_https_host_is_accepted_after_redirect():
    response = _response(url="https://www.sec.gov/news/pressreleases.rss")
    validate_response_origin(
        response,
        source=SOURCE,
        requested_url="https://www.sec.gov/news/pressreleases.rss",
    )


@pytest.mark.parametrize(
    "final_url",
    [
        "https://example.com/news/pressreleases.rss",
        "http://www.sec.gov/news/pressreleases.rss",
        "https://sec.gov.evil.example/news/pressreleases.rss",
    ],
)
def test_cross_host_or_https_downgrade_redirect_fails_closed(final_url):
    with pytest.raises(ValueError, match="redirected outside"):
        validate_response_origin(
            _response(url=final_url),
            source=SOURCE,
            requested_url="https://www.sec.gov/news/pressreleases.rss",
        )


def test_unregistered_request_host_fails_closed_even_if_response_looks_official():
    with pytest.raises(ValueError, match="request URL"):
        validate_response_origin(
            _response(url="https://www.sec.gov/news/pressreleases.rss"),
            source=SOURCE,
            requested_url="https://example.com/feed.xml",
        )


def test_announced_oversized_payload_fails_before_parser_use():
    response = _response(
        url="https://www.sec.gov/news/pressreleases.rss",
        headers={"content-length": str(MAX_SOURCE_RESPONSE_BYTES + 1)},
    )
    with pytest.raises(ValueError, match="exceeds frozen byte bound"):
        bounded_response_text(response, max_bytes=MAX_SOURCE_RESPONSE_BYTES)


def test_actual_oversized_payload_fails_when_content_length_is_missing():
    response = _response(
        url="https://www.sec.gov/news/pressreleases.rss",
        content=b"x" * (MAX_SOURCE_RESPONSE_BYTES + 1),
    )
    with pytest.raises(ValueError, match="exceeds frozen byte bound"):
        bounded_response_text(response, max_bytes=MAX_SOURCE_RESPONSE_BYTES)


def test_invalid_content_length_fails_closed():
    response = _response(
        url="https://www.sec.gov/news/pressreleases.rss",
        headers={"content-length": "not-an-integer"},
    )
    with pytest.raises(ValueError, match="invalid Content-Length"):
        bounded_response_text(response, max_bytes=MAX_SOURCE_RESPONSE_BYTES)
