from datetime import datetime, timezone

from research.btc_macro_cycle_etf_shadow import (
    build_snapshot,
    summarize_btc_price,
    summarize_cycle,
    summarize_etf_rows,
    summarize_series,
)


def _bybit_daily_payload(count: int = 300) -> dict:
    base = 1_700_000_000_000
    rows = []
    for index in range(count):
        close = 50_000.0 + index * 100.0
        rows.append([str(base + index * 86_400_000), str(close), str(close), str(close), str(close), "1", "1"])
    rows.reverse()  # Bybit returns newest first.
    return {"retCode": 0, "result": {"list": rows}}


def test_cycle_is_block_progress_descriptive() -> None:
    result = summarize_cycle(945_000, now=datetime(2026, 8, 18, tzinfo=timezone.utc))
    assert result["cycle_progress_pct"] == 50.0
    assert result["cycle_quartile"] == "Q3"
    assert result["blocks_to_next_halving"] == 105_000
    assert result["estimated_next_halving_assumption_seconds_per_block"] == 600


def test_btc_price_summary_uses_usdc_daily_series() -> None:
    result = summarize_btc_price(_bybit_daily_payload())
    assert result["symbol"] == "BTCUSDC"
    assert result["data_points"] == 300
    assert result["sma_200d"] > 0
    assert result["return_30d_pct"] is not None
    assert result["return_90d_pct"] is not None
    assert result["rolling_300d_high_drawdown_pct"] <= 0


def test_macro_series_summary_is_observation_based() -> None:
    points = [(f"2026-07-{index + 1:02d}", 100.0 + index) for index in range(25)]
    result = summarize_series(points)
    assert result["latest"] == 124.0
    assert result["change_5obs_pct"] > 0
    assert result["change_20obs_pct"] > 0


def test_etf_summary_preserves_usd_sign() -> None:
    rows = [
        {"date": "2026-08-14", "total_usd": -100_000_000.0, "funds": {"IBIT": -50_000_000.0}},
        {"date": "2026-08-17", "total_usd": 200_000_000.0, "funds": {"IBIT": 150_000_000.0}},
    ]
    result = summarize_etf_rows(rows)
    assert result["latest_daily_flow_usd"] == 200_000_000.0
    assert result["flow_5d_usd"] == 100_000_000.0
    assert result["positive_days_20d"] == 1
    assert result["negative_days_20d"] == 1


def test_snapshot_contract_is_research_only() -> None:
    result = build_snapshot(
        cycle={"tip_height": 1},
        btc_price={"symbol": "BTCUSDC"},
        macro={},
        etf=None,
        source_status={"cycle": {"status": "LIVE"}},
        captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        source_commit_sha="abc",
    )
    assert result["research_only"] is True
    assert result["label_free"] is True
    assert result["context_only"] is True
    assert result["live_strategy_mutated"] is False
    assert result["promotion_allowed"] is False
