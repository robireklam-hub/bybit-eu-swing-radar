from datetime import datetime, timezone

from research.liquidation_context_shadow import (
    build_snapshot,
    build_symbol_context,
    normalize_exchange_names,
    select_market_candidates,
    spec,
)


def _market(symbol: str, exchange: str, quote: str, base: str = "BTC") -> dict:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "symbol_on_exchange": symbol.split(".")[0],
        "base_asset": base,
        "quote_asset": quote,
        "is_perpetual": True,
    }


def test_spec_is_research_only_and_never_execution_proof() -> None:
    payload = spec()
    assert payload["research_only"] is True
    assert payload["context_only"] is True
    assert payload["label_free"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["execution_proof"] is False
    assert payload["promotion_allowed"] is False
    assert payload["max_liquidation_symbol_calls"] == 16


def test_exchange_codes_are_resolved_and_bybit_usdt_is_primary() -> None:
    names = normalize_exchange_names(
        [{"code": "A", "name": "Bybit"}, {"code": "B", "name": "Binance"}]
    )
    markets = [
        _market("BTCUSDC_PERP.A", "A", "USDC"),
        _market("BTCUSDT_PERP.B", "B", "USDT"),
        _market("BTCUSDT_PERP.A", "A", "USDT"),
    ]
    candidates = select_market_candidates(markets, names, ["BTC"])["BTC"]
    assert candidates[0]["symbol"] == "BTCUSDT_PERP.A"
    assert candidates[0]["resolved_exchange_name"] == "Bybit"
    assert candidates[1]["symbol"] == "BTCUSDC_PERP.A"
    assert candidates[2]["symbol"] == "BTCUSDT_PERP.B"


def test_unknown_exchange_is_lower_priority_than_known_exchange() -> None:
    names = {"A": "Bybit", "C": "Hyperliquid"}
    markets = [
        _market("BTCUSDC_PERP.C", "C", "USDC"),
        _market("BTCUSDT_PERP.A", "A", "USDT"),
    ]
    candidates = select_market_candidates(markets, names, ["BTC"])["BTC"]
    assert candidates[0]["exchange_code"] == "A"


def test_zero_activity_is_covered_not_missing() -> None:
    market = {
        "symbol": "BTCUSDT_PERP.A",
        "exchange": "A",
        "exchange_code": "A",
        "resolved_exchange_name": "Bybit",
        "quote_asset": "USDT",
    }
    row = build_symbol_context(
        "BTCUSDC",
        market,
        [{"t": 100, "l": 0, "s": 0}, {"t": 200, "l": 0, "s": 0}],
        fallback_used=False,
        attempted_markets=["BTCUSDT_PERP.A"],
    )
    assert row["coverage"] is True
    assert row["state"] == "AVAILABLE_ZERO_ACTIVITY"
    assert row["total_liquidations_24h_usd"] == 0
    assert row["liquidation_skew"] is None


def test_activity_and_snapshot_coverage_are_descriptive_only() -> None:
    market = {
        "symbol": "BTCUSDT_PERP.A",
        "exchange": "A",
        "exchange_code": "A",
        "resolved_exchange_name": "Bybit",
        "quote_asset": "USDT",
    }
    covered = build_symbol_context(
        "BTCUSDC",
        market,
        [{"t": 200, "l": 150.0, "s": 50.0}],
        fallback_used=True,
        attempted_markets=["BTCUSDC_PERP.C", "BTCUSDT_PERP.A"],
    )
    missing = build_symbol_context(
        "ETHUSDC", None, [], fallback_used=False, attempted_markets=[]
    )
    snapshot = build_snapshot(
        [covered, missing],
        captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        source_commit_sha="abc",
        metadata={"liquidation_symbol_calls": 2},
    )
    assert snapshot["coverage"] == {
        "total": 2,
        "available": 1,
        "unavailable": 1,
        "activity": 1,
        "zero_activity": 0,
        "fallback_used": 1,
    }
    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["promotion_allowed"] is False
