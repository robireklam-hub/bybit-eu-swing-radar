from datetime import datetime, timezone

from research.relative_strength_shadow import (
    build_snapshot,
    compute_symbol_metrics,
    parse_closed_daily_klines,
)


def test_parse_closed_daily_klines_excludes_open_candle() -> None:
    day_ms = 86_400_000
    now_ms = int(datetime(2026, 8, 18, 12, tzinfo=timezone.utc).timestamp() * 1000)
    today_start = int(datetime(2026, 8, 18, tzinfo=timezone.utc).timestamp() * 1000)
    yesterday_start = today_start - day_ms
    rows = [
        [today_start, "1", "1", "1", "101", "0", "0"],
        [yesterday_start, "1", "1", "1", "100", "0", "0"],
    ]
    bars = parse_closed_daily_klines(rows, now_ms=now_ms)
    assert bars == [{"start_ms": yesterday_start, "close": 100.0}]


def test_compute_symbol_metrics_uses_completed_daily_history() -> None:
    bars = [
        {"start_ms": index * 86_400_000, "close": 100.0 + index}
        for index in range(100)
    ]
    metrics = compute_symbol_metrics("BTCUSDC", bars)
    assert metrics["symbol"] == "BTCUSDC"
    assert metrics["data_points"] == 100
    assert metrics["return_7d_pct"] > 0
    assert metrics["return_30d_pct"] > metrics["return_7d_pct"]
    assert metrics["return_90d_pct"] > metrics["return_30d_pct"]
    assert metrics["max_drawdown_90d_pct"] == 0.0


def _analysis(symbol: str, r7: float, r30: float, r90: float) -> dict:
    return {
        "symbol": symbol,
        "data_points": 100,
        "data_as_of_ms": 1,
        "close": 100.0,
        "return_7d_pct": r7,
        "return_30d_pct": r30,
        "return_90d_pct": r90,
        "volatility_30d_pct": 2.0,
        "drawdown_from_90d_high_pct": -1.0,
        "max_drawdown_90d_pct": -5.0,
    }


def test_build_snapshot_ranks_cross_section_without_sector_labels() -> None:
    analyses = [
        _analysis("BTCUSDC", 1, 5, 10),
        _analysis("AAAUSDC", 8, 20, 30),
        _analysis("BBBUSDC", 4, 10, 15),
        _analysis("CCCUSDC", -2, -5, -10),
        _analysis("DDDUSDC", -8, -20, -30),
    ]
    snapshot = build_snapshot(
        analyses, captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc)
    )
    rows = {item["symbol"]: item for item in snapshot["symbols"]}
    assert rows["AAAUSDC"]["rank"] == 1
    assert rows["AAAUSDC"]["state"] == "LEADER"
    assert rows["DDDUSDC"]["rank"] == 5
    assert rows["DDDUSDC"]["state"] == "LAGGARD"
    assert rows["AAAUSDC"]["relative_to_btc_30d_pct"] == 15.0
    assert snapshot["sector_rotation_available"] is False
    assert snapshot["sector_metadata_status"] == "NOT_INCLUDED_UNSOURCED"
    assert snapshot["promotion_allowed"] is False
