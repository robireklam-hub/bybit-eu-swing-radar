from datetime import datetime, timezone

import pytest

from research.microstructure.data_access import (
    build_bucket_payload,
    normalize_symbol,
    summarize_bucket_rows,
    validate_bounds,
)


SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")


def _row(second: int, **overrides):
    row = {
        "symbol": "BTCUSDC",
        "bucket_start": datetime(2026, 8, 18, 5, 0, second, tzinfo=timezone.utc),
        "bucket_seconds": 5,
        "source": "bybit-eu-spot-ws",
        "trade_count": 1,
        "block_trade_count": 0,
        "rpi_trade_count": 0,
        "taker_buy_base": 0.01,
        "taker_sell_base": 0.005,
        "taker_buy_quote": 1200.0,
        "taker_sell_quote": 600.0,
        "signed_quote_flow": 600.0,
        "total_quote_volume": 1800.0,
        "trade_vwap": 120000.0,
        "best_bid": 119999.0,
        "best_ask": 120001.0,
        "mid": 120000.0,
        "spread_bps": 0.1666666667,
        "microprice": 120000.5,
        "bid_depth_5_quote": 100000.0,
        "ask_depth_5_quote": 90000.0,
        "bid_depth_10_quote": 180000.0,
        "ask_depth_10_quote": 160000.0,
        "bid_depth_50_quote": 800000.0,
        "ask_depth_50_quote": 750000.0,
        "imbalance_5": 0.0526315789,
        "imbalance_10": 0.0588235294,
        "imbalance_50": 0.0322580645,
        "bid_added_quote": 10000.0,
        "bid_removed_quote": 4000.0,
        "ask_added_quote": 3000.0,
        "ask_removed_quote": 8000.0,
        "book_message_count": 12,
        "last_trade_at": datetime(2026, 8, 18, 5, 0, second, tzinfo=timezone.utc),
        "last_book_at": datetime(2026, 8, 18, 5, 0, second, tzinfo=timezone.utc),
        "book_update_id": 123,
        "cross_seq": 456,
        "book_ready": True,
    }
    row.update(overrides)
    return row


def test_symbol_access_is_usdc_only_and_recorder_bounded():
    assert normalize_symbol(" btcusdc ", SYMBOLS) == "BTCUSDC"
    with pytest.raises(ValueError, match="USDC-only"):
        normalize_symbol("BTCUSDT", SYMBOLS)
    with pytest.raises(ValueError, match="not enabled"):
        normalize_symbol("XRPUSDC", SYMBOLS)


def test_query_bounds_are_hard_capped():
    assert validate_bounds(15, 240) == (15, 240)
    with pytest.raises(ValueError):
        validate_bounds(361, 240)
    with pytest.raises(ValueError):
        validate_bounds(15, 1001)


def test_payload_is_label_blind_read_only_and_chronological():
    rows = [_row(10), _row(0, trade_count=0, signed_quote_flow=-100.0)]
    payload = build_bucket_payload(
        "BTCUSDC",
        rows,
        lookback_minutes=15,
        limit=240,
        checked_at=datetime(2026, 8, 18, 5, 15, tzinfo=timezone.utc),
    )
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_blind"] is True
    assert payload["outcome_fields_read"] is False
    assert payload["promotion_allowed"] is False
    assert payload["source_table"] == "microstructure_buckets"
    assert payload["row_count"] == 2
    assert payload["rows"][0]["bucket_start"] < payload["rows"][1]["bucket_start"]
    assert "net_r" not in payload["rows"][0]
    assert "outcome" not in payload["rows"][0]


def test_summary_reports_descriptive_market_metrics_only():
    rows = [
        _row(0),
        _row(
            5,
            trade_count=0,
            signed_quote_flow=-200.0,
            total_quote_volume=200.0,
            spread_bps=0.25,
            book_ready=False,
            imbalance_10=-0.1,
            imbalance_50=-0.2,
        ),
    ]
    summary = summarize_bucket_rows(rows)
    assert summary["row_count"] == 2
    assert summary["book_ready_ratio"] == 0.5
    assert summary["trade_bucket_ratio"] == 0.5
    assert summary["trade_count"] == 1
    assert summary["signed_quote_flow"] == pytest.approx(400.0)
    assert summary["total_quote_volume"] == pytest.approx(2000.0)
    assert summary["mean_spread_bps"] is not None
    assert summary["p95_spread_bps"] == pytest.approx(0.25)
    assert summary["mean_imbalance_10"] is not None
    assert summary["mean_microprice_displacement_bps"] is not None
    assert summary["mean_book_pressure_ratio"] is not None
