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
SEC_PRESS_RELEASE_RSS = "https://www.sec.gov/news/pressreleases.rss"
SEC_2026_76_FIXTURE_URL = (
    "https://www.sec.gov/newsroom/press-releases/"
    "2026-76-sec-proposes-new-regulation-crypto-assets"
)
FED_PRESS_RELEASE_RSS = "https://www.federalreserve.gov/feeds/press_all.xml"
TREASURY_PRESS_RELEASE_INDEX = "https://home.treasury.gov/news/press-releases"
CFTC_PRESS_RELEASE_RSS = "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"
WHITE_HOUSE_BRIEFINGS_INDEX = "https://www.whitehouse.gov/briefings-statements/"
CONGRESS_ALERTS_INFO = "https://www.congress.gov/get-alerts"


def _source(
    *,
    provider: str,
    provider_code: str,
    authority_tier: str,
    source_family: str,
    monitor_url: str,
    fetch_url: str,
    parser_mode: str,
    allowed_host: str,
    allowed_path_prefixes: list[str],
    event_classes: list[str],
    enabled: bool = True,
    coverage_note: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "provider_code": provider_code,
        "authority_tier": authority_tier,
        "source_family": source_family,
        "monitor_url": monitor_url,
        "fetch_url": fetch_url,
        "parser_mode": parser_mode,
        "allowed_host": allowed_host,
        "allowed_path_prefixes": allowed_path_prefixes,
        # Backward-compatible singular field used by the original SEC test.
        "allowed_path_prefix": allowed_path_prefixes[0],
        "event_classes": event_classes,
        "enabled": enabled,
        "coverage_note": coverage_note,
        "context_only": True,
        "hard_gate": False,
        "score_mutation": False,
        "ranking_mutation": False,
        "eligibility_mutation": False,
        "execution_mutation": False,
    }


_PRIMARY_SOURCES: tuple[dict[str, Any], ...] = (
    _source(
        provider="U.S. Securities and Exchange Commission",
        provider_code="SEC",
        authority_tier="PRIMARY_REGULATOR",
        source_family="OFFICIAL_PRESS_RELEASES",
        monitor_url=SEC_PRESS_RELEASE_INDEX,
        fetch_url=SEC_PRESS_RELEASE_RSS,
        parser_mode="RSS",
        allowed_host="www.sec.gov",
        allowed_path_prefixes=["/newsroom/press-releases"],
        event_classes=["US_CRYPTO_REGULATION"],
    ),
    _source(
        provider="Board of Governors of the Federal Reserve System",
        provider_code="FED",
        authority_tier="PRIMARY_CENTRAL_BANK",
        source_family="OFFICIAL_PRESS_RELEASES",
        monitor_url="https://www.federalreserve.gov/newsevents/pressreleases.htm",
        fetch_url=FED_PRESS_RELEASE_RSS,
        parser_mode="RSS",
        allowed_host="www.federalreserve.gov",
        allowed_path_prefixes=["/newsevents/pressreleases"],
        event_classes=["FED_LIQUIDITY_MARKET_OPERATION", "US_MONETARY_POLICY"],
    ),
    _source(
        provider="U.S. Department of the Treasury",
        provider_code="TREASURY",
        authority_tier="PRIMARY_TREASURY",
        source_family="OFFICIAL_PRESS_RELEASES",
        monitor_url=TREASURY_PRESS_RELEASE_INDEX,
        fetch_url=TREASURY_PRESS_RELEASE_INDEX,
        parser_mode="HTML_INDEX",
        allowed_host="home.treasury.gov",
        allowed_path_prefixes=["/news/press-releases"],
        event_classes=[
            "TREASURY_DEBT_MANAGEMENT",
            "US_CRYPTO_REGULATION",
            "SANCTIONS_FINANCIAL_GEOPOLITICS",
        ],
    ),
    _source(
        provider="Commodity Futures Trading Commission",
        provider_code="CFTC",
        authority_tier="PRIMARY_REGULATOR",
        source_family="OFFICIAL_PRESS_RELEASES",
        monitor_url="https://www.cftc.gov/PressRoom/PressReleases/index.htm",
        fetch_url=CFTC_PRESS_RELEASE_RSS,
        parser_mode="RSS",
        allowed_host="www.cftc.gov",
        allowed_path_prefixes=["/PressRoom/PressReleases", "/pressroom/pressreleases"],
        event_classes=["US_CRYPTO_REGULATION", "DERIVATIVES_REGULATION"],
    ),
    _source(
        provider="The White House",
        provider_code="WHITE_HOUSE",
        authority_tier="PRIMARY_EXECUTIVE",
        source_family="OFFICIAL_BRIEFINGS_STATEMENTS",
        monitor_url=WHITE_HOUSE_BRIEFINGS_INDEX,
        fetch_url=WHITE_HOUSE_BRIEFINGS_INDEX,
        parser_mode="HTML_INDEX",
        allowed_host="www.whitehouse.gov",
        allowed_path_prefixes=["/briefings-statements", "/presidential-actions"],
        event_classes=["US_CRYPTO_REGULATION", "TRADE_POLICY", "SANCTIONS_FINANCIAL_GEOPOLITICS"],
    ),
    _source(
        provider="Congress.gov / Library of Congress",
        provider_code="CONGRESS",
        authority_tier="PRIMARY_LEGISLATIVE",
        source_family="TARGETED_SAVED_SEARCH_RSS",
        monitor_url=CONGRESS_ALERTS_INFO,
        fetch_url=CONGRESS_ALERTS_INFO,
        parser_mode="TARGETED_RSS_NOT_CONFIGURED",
        allowed_host="www.congress.gov",
        allowed_path_prefixes=["/"],
        event_classes=["US_CRYPTO_REGULATION", "TRADE_POLICY"],
        enabled=False,
        coverage_note=(
            "Congress.gov provides RSS for targeted/saved activity, but a bounded crypto-policy "
            "saved-search feed is not yet configured. Coverage must remain visibly unavailable."
        ),
    ),
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
    {
        "fixture_id": "treasury-2026-08-19-long-end-liquidity-support-buybacks",
        "provider_code": "TREASURY",
        "published_date": "2026-08-19",
        "headline": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
        "url": "https://home.treasury.gov/news/press-releases/sb0607",
        "event_class": "TREASURY_DEBT_MANAGEMENT",
        "source_role": "PRIMARY_SOURCE_REGRESSION_FIXTURE",
        "trade_direction": None,
        "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
    },
)


def source_registry() -> list[dict[str, Any]]:
    """Return an isolated copy of the frozen primary-source registry."""
    return deepcopy(list(_PRIMARY_SOURCES))


def enabled_source_registry() -> list[dict[str, Any]]:
    return [item for item in source_registry() if item.get("enabled")]


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
        prefixes = source.get("allowed_path_prefixes") or [source.get("allowed_path_prefix")]
        if not any(
            path == str(prefix).rstrip("/")
            or path.startswith(str(prefix).rstrip("/") + "/")
            for prefix in prefixes
            if prefix
        ):
            continue
        event_classes = list(source.get("event_classes") or [])
        return {
            "provider": source["provider"],
            "provider_code": source["provider_code"],
            "authority_tier": source["authority_tier"],
            "source_family": source["source_family"],
            "event_class": event_classes[0] if event_classes else None,
            "event_classes": event_classes,
            "context_only": True,
            "hard_gate": False,
            "score_mutation": False,
            "ranking_mutation": False,
            "eligibility_mutation": False,
            "execution_mutation": False,
        }
    return None
