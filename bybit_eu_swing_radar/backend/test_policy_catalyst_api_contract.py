from datetime import datetime, timedelta, timezone

import pytest

import app.market_context_alerts as alerts
import app.research_policy_catalyst_api as policy_api
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


@pytest.mark.asyncio
async def test_status_lookback_uses_database_clock_without_untyped_interval_parameter(monkeypatch):
    class FakeConnection:
        def __init__(self):
            self.fetch_calls = []

        async def execute(self, *_args):
            return None

        async def fetchrow(self, *_args):
            return None

        async def fetch(self, query, *args):
            self.fetch_calls.append((query, args))
            return []

        async def fetchval(self, *_args):
            return 0

        async def close(self):
            return None

    connection = FakeConnection()

    async def fake_connect(*_args, **_kwargs):
        return connection

    monkeypatch.setattr(policy_api.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(policy_api, "_database_url", lambda: "postgresql://unused")

    result = await policy_api.status_payload()

    assert result["freshness"] == "UNAVAILABLE"
    assert len(connection.fetch_calls) == 1
    query, args = connection.fetch_calls[0]
    assert "first_seen_at >= NOW() - INTERVAL '24 hours'" in query
    assert args == (policy_api.SPEC_VERSION,)
    assert "$2 - INTERVAL" not in query


@pytest.mark.asyncio
async def test_capture_dual_writes_timestamped_event_into_immutable_event_store_v1():
    class FakeConnection:
        def __init__(self):
            self.execute_calls = []

        async def execute(self, query, *args):
            self.execute_calls.append((query, args))
            return None

    captured_at = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    event = {
        "provider_code": "TREASURY",
        "headline": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9",
        "url": "https://home.treasury.gov/news/press-releases/sb0607",
        "primary_event_class": "TREASURY_DEBT_MANAGEMENT",
        "published_at": "2026-08-19T15:00:00+00:00",
    }
    connection = FakeConnection()

    record = await policy_api._persist_event_store_v1(connection, event, captured_at)

    assert record is not None
    assert record["spec_version"] == "policy-catalyst-event-store-v1"
    assert record["context_only"] is True
    assert record["hard_gate"] is False
    assert record["score_mutation"] is False
    assert record["ranking_mutation"] is False
    assert record["eligibility_mutation"] is False
    assert record["execution_mutation"] is False
    assert record["trade_direction"] is None
    assert record["first_seen_at"] == captured_at.isoformat()
    assert len(connection.execute_calls) == 1
    query, args = connection.execute_calls[0]
    assert "INSERT INTO policy_catalyst_event_v1" in query
    assert "ON CONFLICT (event_id) DO UPDATE SET" in query
    assert "last_seen_at=EXCLUDED.last_seen_at" in query
    assert "first_seen_at=EXCLUDED.first_seen_at" not in query
    assert args[0] == record["event_id"]


@pytest.mark.asyncio
async def test_capture_does_not_invent_event_store_identity_without_source_publish_time():
    class FakeConnection:
        def __init__(self):
            self.execute_calls = []

        async def execute(self, query, *args):
            self.execute_calls.append((query, args))
            return None

    captured_at = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    event = {
        "provider_code": "SEC",
        "headline": "SEC Proposes New Regulation Crypto Assets",
        "url": "https://www.sec.gov/newsroom/press-releases/example",
        "primary_event_class": "US_CRYPTO_REGULATION",
        "published_at": None,
    }
    connection = FakeConnection()

    record = await policy_api._persist_event_store_v1(connection, event, captured_at)

    assert record is None
    assert connection.execute_calls == []


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
