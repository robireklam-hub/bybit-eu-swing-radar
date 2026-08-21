from datetime import datetime, timedelta, timezone

import app.market_context_alerts as alerts
from research.policy_catalyst_feed_v1 import (
    build_snapshot,
    classify_headline,
    extract_published_at_from_html,
    normalize_event,
    parse_official_html_index,
    parse_rss_or_atom,
    spec,
)
from research.policy_catalyst_sources_v1 import regression_fixtures, source_registry

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)


def test_frozen_primary_source_taxonomy_and_unavailable_congress_are_explicit():
    contract = spec()
    assert contract["context_only"] is True
    assert contract["hard_gate"] is False
    assert contract["trade_direction"] is None
    assert "SEC" in contract["enabled_providers"]
    assert "FED" in contract["enabled_providers"]
    assert "TREASURY" in contract["enabled_providers"]
    assert "CFTC" in contract["enabled_providers"]
    assert "WHITE_HOUSE" in contract["enabled_providers"]
    unavailable = {row["provider_code"]: row["reason"] for row in contract["unavailable_providers"]}
    assert "CONGRESS" in unavailable
    assert "not yet configured" in unavailable["CONGRESS"]


def test_primary_source_registry_never_allows_trade_mutation():
    for source in source_registry():
        assert source["context_only"] is True
        assert source["hard_gate"] is False
        assert source["score_mutation"] is False
        assert source["ranking_mutation"] is False
        assert source["eligibility_mutation"] is False
        assert source["execution_mutation"] is False


def test_sec_and_treasury_regression_fixtures_are_exact_and_direction_free():
    fixtures = {row["fixture_id"]: row for row in regression_fixtures()}
    sec = fixtures["sec-2026-76-regulation-crypto-assets"]
    treasury = fixtures["treasury-2026-08-19-long-end-liquidity-support-buybacks"]
    assert sec["url"].endswith("2026-76-sec-proposes-new-regulation-crypto-assets")
    assert sec["trade_direction"] is None
    assert treasury["url"] == "https://home.treasury.gov/news/press-releases/sb0607"
    assert treasury["event_class"] == "TREASURY_DEBT_MANAGEMENT"
    assert treasury["trade_direction"] is None


def test_label_free_headline_classifier_covers_core_policy_classes():
    assert classify_headline("SEC", "SEC Proposes New Regulation Crypto Assets") == [
        "US_CRYPTO_REGULATION"
    ]
    assert classify_headline(
        "TREASURY",
        "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
    ) == ["TREASURY_DEBT_MANAGEMENT"]
    assert "FED_LIQUIDITY_MARKET_OPERATION" in classify_headline(
        "FED", "Federal Reserve announces standing repo operation changes"
    )
    assert "US_MONETARY_POLICY" in classify_headline(
        "FED", "Federal Reserve issues FOMC statement on monetary policy"
    )
    assert classify_headline("SEC", "SEC Announces Annual Small Business Forum") == []


def test_normalize_event_rejects_spoofed_irrelevant_and_future_rows():
    assert normalize_event(
        provider_code="SEC",
        headline="SEC Proposes New Regulation Crypto Assets",
        url="https://example.com/newsroom/press-releases/fake",
        published_at=NOW,
        captured_at=NOW,
    ) is None
    assert normalize_event(
        provider_code="SEC",
        headline="SEC Announces Annual Small Business Forum",
        url="https://www.sec.gov/newsroom/press-releases/example",
        published_at=NOW,
        captured_at=NOW,
    ) is None
    assert normalize_event(
        provider_code="SEC",
        headline="SEC Proposes New Regulation Crypto Assets",
        url="https://www.sec.gov/newsroom/press-releases/example",
        published_at=NOW + timedelta(hours=1),
        captured_at=NOW,
    ) is None


def test_normalized_event_is_context_only_and_has_deterministic_identity():
    kwargs = dict(
        provider_code="SEC",
        headline="SEC Proposes New Regulation Crypto Assets",
        url="https://www.sec.gov/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets",
        published_at="2026-08-18T14:00:00+00:00",
        captured_at=NOW,
    )
    first = normalize_event(**kwargs)
    second = normalize_event(**kwargs)
    assert first is not None and second is not None
    assert first["event_id"] == second["event_id"]
    assert first["primary_event_class"] == "US_CRYPTO_REGULATION"
    assert first["trade_direction"] is None
    assert first["context_only"] is True
    assert first["hard_gate"] is False
    assert first["execution_mutation"] is False


def test_rss_parser_keeps_only_relevant_official_rows():
    xml = """<?xml version='1.0'?>
    <rss><channel>
      <item><title>SEC Proposes New Regulation Crypto Assets</title>
        <link>https://www.sec.gov/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets</link>
        <pubDate>Tue, 18 Aug 2026 14:00:00 GMT</pubDate></item>
      <item><title>SEC Announces Annual Small Business Forum</title>
        <link>https://www.sec.gov/newsroom/press-releases/other</link>
        <pubDate>Tue, 18 Aug 2026 15:00:00 GMT</pubDate></item>
    </channel></rss>"""
    rows = parse_rss_or_atom(xml, provider_code="SEC", captured_at=NOW)
    assert len(rows) == 1
    assert rows[0]["primary_event_class"] == "US_CRYPTO_REGULATION"


def test_html_index_parser_finds_relevant_treasury_release_only():
    page = """
    <html><body>
      <a href='/news/press-releases/sb0607'>Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9</a>
      <a href='/news/press-releases/other'>Treasury Announces Employer Contributions to Trump Accounts</a>
    </body></html>
    """
    rows = parse_official_html_index(
        page,
        provider_code="TREASURY",
        base_url="https://home.treasury.gov/news/press-releases",
        allowed_prefixes=["/news/press-releases"],
    )
    assert rows == [
        {
            "headline": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
            "url": "https://home.treasury.gov/news/press-releases/sb0607",
        }
    ]


def test_detail_publication_timestamp_extraction_is_provider_time_not_first_seen():
    html = '<script type="application/ld+json">{"datePublished":"2026-08-19T09:30:00-04:00"}</script>'
    published = extract_published_at_from_html(html)
    assert published == datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)


def test_snapshot_exposes_provider_failures_instead_of_turning_them_into_zero_risk():
    event = normalize_event(
        provider_code="SEC",
        headline="SEC Proposes New Regulation Crypto Assets",
        url="https://www.sec.gov/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets",
        published_at="2026-08-18T14:00:00+00:00",
        captured_at=NOW,
    )
    snapshot = build_snapshot(
        [event],
        source_results=[
            {"provider_code": "SEC", "status": "OK", "event_count": 1},
            {"provider_code": "FED", "status": "ERROR", "event_count": 0},
        ],
        captured_at=NOW,
    )
    assert snapshot["data_quality"] == "PARTIAL"
    assert snapshot["coverage"]["failed_sources"] == ["FED"]
    assert snapshot["causal_attribution"] == "UNCONFIRMED_CONTEXT_ONLY"


def test_live_policy_context_uses_first_seen_and_freshness_not_market_outcome():
    capture = {"captured_at": NOW.isoformat(), "data_quality": "COMPLETE"}
    event = {
        "provider": "U.S. Department of the Treasury",
        "provider_code": "TREASURY",
        "authority_tier": "PRIMARY_TREASURY",
        "headline": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
        "url": "https://home.treasury.gov/news/press-releases/sb0607",
        "primary_event_class": "TREASURY_DEBT_MANAGEMENT",
        "event_classes": ["TREASURY_DEBT_MANAGEMENT"],
        "published_at": (NOW - timedelta(hours=3)).isoformat(),
        "first_seen_at": (NOW - timedelta(minutes=5)).isoformat(),
    }
    result = alerts.build_policy_catalyst_context(capture, [event], now=NOW)
    assert result["state"] == "ACTIVE"
    assert result["mandatory_warning"] is True
    assert result["hard_gate"] is False
    assert result["score_mutation"] is False
    assert result["execution_mutation"] is False
    assert result["recent_events"][0]["first_seen_at"] == event["first_seen_at"]


def test_live_policy_context_stale_capture_is_visible_even_with_recent_event():
    capture = {
        "captured_at": (NOW - timedelta(hours=1)).isoformat(),
        "data_quality": "PARTIAL",
    }
    event = {
        "first_seen_at": (NOW - timedelta(minutes=5)).isoformat(),
        "provider_code": "SEC",
        "headline": "SEC Proposes New Regulation Crypto Assets",
    }
    result = alerts.build_policy_catalyst_context(capture, [event], now=NOW)
    assert result["state"] == "STALE"
    assert result["source_age_seconds"] == 3600
    assert "incomplete" in result["note"]
