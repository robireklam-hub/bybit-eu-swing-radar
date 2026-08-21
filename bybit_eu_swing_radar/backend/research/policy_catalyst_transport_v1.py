"""Fail-closed transport guards for policy catalyst primary-source collection.

The collector accepts only frozen official HTTPS hosts and streams response bodies
through explicit byte caps before parser use. It is research-only and cannot
mutate live strategy, score, ranking, eligibility, shortability, or execution.
"""
from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

SPEC_VERSION = "policy-catalyst-transport-v1"
MAX_SOURCE_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DETAIL_RESPONSE_BYTES = 2 * 1024 * 1024


def _official_https_host(url: str, *, allowed_host: str) -> bool:
    try:
        parsed = urlparse(str(url))
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() == "https" and (parsed.hostname or "").lower() == allowed_host.lower()


def validate_response_origin(
    response: Any,
    *,
    source: Mapping[str, Any],
    requested_url: str,
) -> None:
    """Reject cross-host/downgrade redirects before any response body is trusted."""
    allowed_host = str(source.get("allowed_host") or "").strip().lower()
    if not allowed_host:
        raise ValueError("policy source allowed_host is required")
    if not _official_https_host(requested_url, allowed_host=allowed_host):
        raise ValueError("policy source request URL is outside the frozen official host")

    final_url = str(getattr(response, "url", "") or "")
    if not _official_https_host(final_url, allowed_host=allowed_host):
        raise ValueError("policy source response redirected outside the frozen official HTTPS host")


def _validate_announced_length(response: Any, *, max_bytes: int) -> None:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    headers = getattr(response, "headers", {}) or {}
    content_length = headers.get("content-length") or headers.get("Content-Length")
    if content_length in (None, ""):
        return
    try:
        announced = int(str(content_length).strip())
    except ValueError as exc:
        raise ValueError("invalid Content-Length from policy source") from exc
    if announced < 0 or announced > max_bytes:
        raise ValueError("policy source response exceeds frozen byte bound")


def bounded_response_text(response: Any, *, max_bytes: int) -> str:
    """Validate an already-buffered response; retained for deterministic unit contracts."""
    _validate_announced_length(response, max_bytes=max_bytes)
    content = getattr(response, "content", b"")
    if isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = bytes(content)
    if len(raw) > max_bytes:
        raise ValueError("policy source response exceeds frozen byte bound")
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    return raw.decode("utf-8", errors="replace")


async def fetch_bounded_official_text(
    client: Any,
    *,
    source: Mapping[str, Any],
    requested_url: str,
    max_bytes: int,
) -> tuple[str, int, str, int]:
    """Stream one official response and abort as soon as the frozen byte cap is exceeded."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    async with client.stream("GET", requested_url) as response:
        response.raise_for_status()
        validate_response_origin(response, source=source, requested_url=requested_url)
        _validate_announced_length(response, max_bytes=max_bytes)

        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("policy source response exceeds frozen byte bound")
            chunks.append(bytes(chunk))

        raw = b"".join(chunks)
        encoding = str(getattr(response, "encoding", None) or "utf-8")
        try:
            text = raw.decode(encoding, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return text, int(response.status_code), str(response.url), total


def contract() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "primary_source_only": True,
        "https_required": True,
        "cross_host_redirect_allowed": False,
        "streaming_byte_cap_enforced": True,
        "source_response_max_bytes": MAX_SOURCE_RESPONSE_BYTES,
        "detail_response_max_bytes": MAX_DETAIL_RESPONSE_BYTES,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
    }
