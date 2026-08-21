from datetime import datetime, timedelta, timezone

import app.market_context_alerts as alerts
from app.main import app as radar_app
from app.research_policy_catalyst_api import SCHEMA_SQL


def test_policy_catalyst_hidden_routes_are_registered_without_public_action_surface():
    paths = {route.path: route for route in radar_app.routes}
    expected = {
        "/v1/research/policy-catalyst/spec",
        "/v1/research/policy-catalyst/capture",
        "/v1/research/policy-catalyst/status",
    }
    assert expected.issubset(paths)
    assert all(paths[path].include_in_schema is False for path in expected)


def test_policy_persistence_contract_has_first_seen_and_separate_captures():
    assert "research_policy_catalyst_events" in SCHEMA_SQL
    assert "research_policy_catalyst_captures" in SCHEMA_SQL
    assert "first_seen_at TIMESTAMPTZ NOT NULL" in SCHEMA_SQL
    assert "last_seen_at TIMESTAMPTZ NOT NULL" in SCHEMA_SQL
    assert "PRIMARY KEY (spec_version, event_id)" in SCHEMA_SQL


def test_old_provider_content_does_not_become_active_on_first_bootstrap_observation():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    capture = {"captured_at": now.isoformat(), "data_quality": "COMPLETE"}
    event = {
        "provider_code": "TREASURY",
        "authority_tier": "PRIMARY_TREASURY",
        "headline": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
        "url": "https://home.treasury.gov/news/press-releases/sb0607",
        "primary_event_class": "TREASURY_DEBT_MANAGEMENT",
        "event_classes": ["TREASURY_DEBT_MANAGEMENT"],
        "published_at": (now - timedelta(days=2)).isoformat(),
        "first_seen_at": (now - timedelta(minutes=1)).isoformat(),
    }
    result = alerts.build_policy_catalyst_context(capture, [event], now=now)
    assert result["state"] == "NORMAL"
    assert result["recent_events"] == []
    assert result["mandatory_warning"] is False


def test_recent_provider_content_can_be_active_but_never_mutates_trade_contract():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    capture = {"captured_at": now.isoformat(), "data_quality": "COMPLETE"}
    event = {
        "provider_code": "SEC",
        "authority_tier": "PRIMARY_REGULATOR",
        "headline": "SEC Proposes New Regulation Crypto Assets",
        "url": "https://www.sec.gov/newsroom/press-releases/example",
        "primary_event_class": "US_CRYPTO_REGULATION",
        "event_classes": ["US_CRYPTO_REGULATION"],
        "published_at": (now - timedelta(minutes=20)).isoformat(),
        "first_seen_at": (now - timedelta(minutes=10)).isoformat(),
    }
    result = alerts.build_policy_catalyst_context(capture, [event], now=now)
    assert result["state"] == "ACTIVE"
    assert result["context_only"] is True
    assert result["hard_gate"] is False
    assert result["score_mutation"] is False
    assert result["ranking_mutation"] is False
    assert result["eligibility_mutation"] is False
    assert result["execution_mutation"] is False
