from __future__ import annotations

import math

import pytest

from research.microstructure.collector import (
    MicrostructureConfig,
    OrderBookState,
    ResearchBucket,
    depth_metrics,
    subscription_topics,
)


def _config(symbols=("BTCUSDC", "ETHUSDC", "SOLUSDC")) -> MicrostructureConfig:
    return MicrostructureConfig(
        enabled=True,
        database_url="postgresql://test",
        ws_url="wss://stream.bybit.eu/v5/public/spot",
        symbols=tuple(symbols),
        bucket_seconds=5,
        depth=50,
    )


def test_config_rejects_non_usdc_symbols() -> None:
    with pytest.raises(ValueError, match="USDC-only"):
        _config(("BTCUSDT",)).validate()


def test_subscription_batches_respect_spot_limit() -> None:
    config = _config(tuple(f"S{i}USDC" for i in range(12)))
    config.validate()
    batches = subscription_topics(config)
    assert all(len(batch) <= 10 for batch in batches)
    assert sum(len(batch) for batch in batches) == 24
    flattened = [topic for batch in batches for topic in batch]
    assert "orderbook.50.S0USDC" in flattened
    assert "publicTrade.S0USDC" in flattened


def test_snapshot_and_delta_reconstruct_book_and_visible_flow() -> None:
    book = OrderBookState("BTCUSDC")
    changes = book.apply(
        "snapshot",
        {
            "u": 100,
            "seq": 200,
            "b": [["100", "2"], ["99", "3"]],
            "a": [["101", "4"], ["102", "5"]],
        },
    )
    assert book.ready
    assert changes == {"bid_added": 0.0, "bid_removed": 0.0, "ask_added": 0.0, "ask_removed": 0.0}

    changes = book.apply(
        "delta",
        {
            "u": 101,
            "seq": 201,
            "b": [["100", "3"], ["99", "0"], ["98", "1"]],
            "a": [["101", "2"], ["103", "2"]],
        },
    )
    assert book.bids == {100.0: 3.0, 98.0: 1.0}
    assert book.asks == {101.0: 2.0, 102.0: 5.0, 103.0: 2.0}
    assert changes["bid_added"] == pytest.approx(100.0 + 98.0)
    assert changes["bid_removed"] == pytest.approx(99.0 * 3.0)
    assert changes["ask_removed"] == pytest.approx(101.0 * 2.0)
    assert changes["ask_added"] == pytest.approx(103.0 * 2.0)


def test_depth_metrics_are_quote_weighted() -> None:
    book = OrderBookState("BTCUSDC", ready=True)
    book.bids = {100.0: 2.0, 99.0: 1.0}
    book.asks = {101.0: 1.0, 102.0: 2.0}
    metrics = depth_metrics(book)
    assert metrics["best_bid"] == 100.0
    assert metrics["best_ask"] == 101.0
    assert metrics["mid"] == 100.5
    assert metrics["spread_bps"] == pytest.approx(1.0 / 100.5 * 10_000)
    assert metrics["microprice"] == pytest.approx((101.0 * 2.0 + 100.0 * 1.0) / 3.0)
    assert metrics["bid_depth_5_quote"] == pytest.approx(299.0)
    assert metrics["ask_depth_5_quote"] == pytest.approx(305.0)
    assert -1.0 <= metrics["imbalance_5"] <= 1.0


def test_trade_bucket_uses_taker_side_and_standard_vwap() -> None:
    bucket = ResearchBucket("BTCUSDC", start_ms=1_700_000_000_000, bucket_seconds=5)
    bucket.add_trade({"T": 1_700_000_000_100, "S": "Buy", "p": "100", "v": "2", "BT": False})
    bucket.add_trade({"T": 1_700_000_000_200, "S": "Sell", "p": "101", "v": "1", "BT": True, "RPI": True})
    values = bucket.db_values("TEST")
    assert bucket.trade_count == 2
    assert bucket.taker_buy_quote == pytest.approx(200.0)
    assert bucket.taker_sell_quote == pytest.approx(101.0)
    assert bucket.block_trade_count == 1
    assert bucket.rpi_trade_count == 1
    assert values[11] == pytest.approx(99.0)  # signed_quote_flow
    assert values[13] == pytest.approx(301.0 / 3.0)  # standard base-volume VWAP
    assert math.isfinite(values[13])


def test_book_bucket_persists_last_state_and_flow() -> None:
    bucket = ResearchBucket("ETHUSDC", start_ms=1_700_000_000_000, bucket_seconds=5)
    bucket.add_book(
        {"best_bid": 10.0, "best_ask": 10.1, "mid": 10.05, "spread_bps": 99.5},
        {"bid_added": 100.0, "bid_removed": 20.0, "ask_added": 30.0, "ask_removed": 40.0},
        cts_ms=1_700_000_000_300,
        update_id=12,
        cross_seq=13,
        ready=True,
    )
    assert bucket.book_message_count == 1
    assert bucket.bid_added_quote == 100.0
    assert bucket.ask_removed_quote == 40.0
    assert bucket.book_update_id == 12
    assert bucket.cross_seq == 13
    assert bucket.book_ready
