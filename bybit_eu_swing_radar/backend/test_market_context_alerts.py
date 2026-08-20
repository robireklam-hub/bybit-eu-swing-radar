from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.market_context_alerts as alerts


def _geo_snapshot(
    timestamp: datetime,
    *,
    event_count: int,
    event_share_pct: float,
    mentions: int,
    severe_count: int,
) -> dict:
    return {
        "captured_at": timestamp.isoformat(),
        "data_quality": "COMPLETE",
        "source_file": {"source_file_timestamp": timestamp.isoformat()},
        "event_context": {
            "material_conflict": {
                "event_count": event_count,
                "event_share_pct": event_share_pct,
                "sum_num_mentions": mentions,
                "goldstein_le_minus7_count": severe_count,
                "top_action_countries": [{"key": "US", "count": event_count}],
            }
        },
    }


def test_geopolitical_context_uses_prior_only_baseline_and_flags_extreme_tail():
    now = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
    history = [
        _geo_snapshot(
            now - timedelta(minutes=15 * (index + 1)),
            event_count=10 + (index % 3),
            event_share_pct=5.0 + (index % 2),
            mentions=100 + index,
            severe_count=1 + (index % 2),
        )
        for index in range(30)
    ]
    latest = _geo_snapshot(
        now,
        event_count=55,
        event_share_pct=22.0,
        mentions=1800,
        severe_count=25,
    )

    result = alerts.build_geopolitical_context(
        latest,
        history,
        now=now + timedelta(minutes=5),
    )

    assert result["state"] == "HIGH"
    assert result["mandatory_warning"] is True
    assert result["baseline_prior_snapshots"] == 30
    assert result["prior_baseline_percentiles"]["event_count"] == 100.0
    assert result["prior_baseline_percentiles"]["sum_num_mentions"] == 100.0
    assert result["causal_attribution"] == "UNCONFIRMED_CONTEXT_ONLY"


def test_geopolitical_context_fails_visible_when_snapshot_is_stale():
    now = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
    latest = _geo_snapshot(
        now - timedelta(hours=4),
        event_count=100,
        event_share_pct=30.0,
        mentions=3000,
        severe_count=40,
    )

    result = alerts.build_geopolitical_context(latest, [], now=now)

    assert result["state"] == "STALE"
    assert result["source_age_seconds"] == 4 * 60 * 60
    assert "attribution is incomplete" in result["note"]


def test_relative_volume_impulse_is_reported_but_not_called_macro_injection():
    payload = {
        "symbol": "BTCUSDC",
        "metrics": {
            "volume_ratio_5m": 3.1,
            "volume_ratio_15m": 2.2,
            "return_15m_pct": 1.25,
        },
    }

    result = alerts.build_market_impulse_context(payload)

    assert result["state"] == "HIGH"
    assert result["max_relative_volume_ratio_5m_15m"] == 3.1
    assert result["max_abs_return_15m_pct"] == 1.25
    assert "not proof" in result["interpretation"]


def test_macro_liquidity_context_preserves_frequency_limit():
    snapshot = {
        "captured_at": "2026-08-20T06:00:00+00:00",
        "macro": {
            "fed_total_assets": {"latest": 7000000, "change_5obs_pct": 0.4},
            "overnight_reverse_repo": {"latest": 120.0, "change_5obs_pct": -10.0},
        },
        "etf": {
            "latest_date": "2026-08-19",
            "latest_daily_flow_usd": 500000000,
            "flow_5d_usd": 1200000000,
        },
    }

    result = alerts.build_macro_liquidity_context(snapshot)

    assert result["state"] == "AVAILABLE"
    assert result["intraday_causality_supported"] is False
    assert "weekly" in result["note"]
    assert "daily" in result["note"]


def test_existing_action_route_gets_market_context_without_changing_trade_fields(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    alerts._external_cache = None
    alerts._external_cache_monotonic = 0.0

    app = FastAPI()

    @app.get("/v1/day-trade/setup/{symbol}")
    async def setup(symbol: str):
        return {
            "symbol": symbol,
            "category": "STRICT",
            "state": "TRIGGERED",
            "decision": "TRADE",
            "setup_score": 88,
            "metrics": {
                "volume_ratio_5m": 2.8,
                "volume_ratio_15m": 2.1,
                "return_15m_pct": 0.9,
            },
        }

    response = TestClient(app).get("/v1/day-trade/setup/BTCUSDC")
    assert response.status_code == 200
    body = response.json()

    assert body["symbol"] == "BTCUSDC"
    assert body["category"] == "STRICT"
    assert body["state"] == "TRIGGERED"
    assert body["decision"] == "TRADE"
    assert body["setup_score"] == 88

    context = body["market_context_alerts"]
    assert context["version"] == "market-context-alert-v1"
    assert context["context_only"] is True
    assert context["hard_gate"] is False
    assert context["score_mutation"] is False
    assert context["eligibility_mutation"] is False
    assert context["execution_mutation"] is False
    assert context["warning_level"] == "HIGH"
    assert context["mandatory_user_warning"] is True
    assert context["market_impulse"]["state"] == "HIGH"
    assert context["geopolitical"]["state"] == "UNAVAILABLE"
    assert "nem teljes" in context["headline"]
