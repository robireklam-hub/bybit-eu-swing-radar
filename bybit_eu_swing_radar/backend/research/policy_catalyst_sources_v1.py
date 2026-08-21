"""Frozen primary-source registry for policy/liquidity catalyst research.

This module contains source metadata and deterministic URL classification only.
It performs no network I/O, produces no trading signal, and cannot mutate live
strategy score, eligibility, ranking, or execution.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

SPEC_VERSION = "policy-catalyst-sources-v1"

SEC_PRESS_RELEASE_INDEX = "https://www.sec.gov/newsroom/press-releases"
SEC_2026_76_FIXTURE_URL = (
    "https://www.sec.gov/newsroom/press-releases/"
    "2026-76-sec-proposes-new-regulation-crypto-assets"
)

_PRIMARY_SOURCES: tuple[dict[str, Any], ...] = (
    {
        "provider": "U.S. Securities and Exchange Commission",
        "provider_code": "SEC",
        "authority_tier": "PRIMARY_REGULATOR",
        "source_family": "OFFICIAL_PRESS_RELEASES",
        "monitor_url": SEC_PRESS_RELEASE_INDEX,
        "allowed_host": "www.sec.gov",
        "allowed_path_prefix": "/newsroom/press-releases",
        "event_classes": ["US_CRYPTO_REGULATION"],
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
    },
)

_REGRESSION_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "fixture_id": "sec-2026-76-regulation-crypto-assets",
        "provider_code": "SEC",
        "release_no": "2026-76",
        "published_date": "2026-08-18",
        "headline": "SEC Proposes New Regulation Crypto Assets",
        "url": SEC_2026_76_FIXTURE_URL,
        "event_class": "US_CRYPTO_REGULATION",
        "source_role": "PRIMARY_SOURCE_REGRESSION_FIXTURE",
        "trade_direction": None,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
    },
)


def source_registry() -> list[dict[str, Any]]:
    """Return an isolated copy of the frozen primary-source registry."""
    return deepcopy(list(_PRIMARY_SOURCES))


def regression_fixtures() -> list[dict[str, Any]]:
    """Return immutable-by-caller regression fixtures for source classification."""
    return deepcopy(list(_REGRESSION_FIXTURES))


def classify_primary_policy_url(url: str) -> dict[str, Any] | None:
    """Classify supported official policy URLs without reading page contents."""
    try:
        parsed = urlparse(url)
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() != "https":
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    for source in _PRIMARY_SOURCES:
        if host != source["allowed_host"]:
            continue
        prefix = str(source["allowed_path_prefix"]).rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return {
                "provider": source["provider"],
                "provider_code": source["provider_code"],
                "authority_tier": source["authority_tier"],
                "source_family": source["source_family"],
                "event_class": "US_CRYPTO_REGULATION",
                "context_only": True,
                "hard_gate": False,
                "score_mutation": False,
                "ranking_mutation": False,
                "eligibility_mutation": False,
                "execution_mutation": False,
            }
    return None
