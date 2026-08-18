"""Deterministic contract tests for the frozen market-regime shadow v1 classifier."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.research_market_regime_api import _select_universe
from research.market_regime_shadow import (
    Bar,
    REGIMES,
    SPEC_VERSION,
    build_market_snapshot,
    classify_symbol,
    spec,
)


def _bars_4h(mode: str) -> list[Bar]:
    rows: list[Bar] = []
    base = 100.0
    for i in range(140):
        if mode == "trend":
            close = base + i * 0.45
            width = 1.0
            turnover = 1_000_000.0
        elif mode == "range":
            close = base + math.sin(i / 2.0) * 1.8
            width = 1.2
            turnover = 1_000_000.0
        elif mode == "compression":
            if i < 105:
                close = base + math.sin(i / 2.0) * 4.5
                width = 4.0
            else:
                close = base + math.sin(i / 3.0) * 0.15
                width = 0.25
            turnover = 1_000_000.0
        elif mode == "high_vol":
            close = base + math.sin(i / 2.5) * 2.0
            width = 1.0 if i < 122 else 2.2
            turnover = 1_000_000.0
            if i == 139:
                close += 4.0
                width = 4.0
        else:
            raise AssertionError(mode)
        open_ = rows[-1].close if rows else close - 0.1
        high = max(open_, close) + width / 2.0
        low = min(open_, close) - width / 2.0
        rows.append(
            Bar(
                start_ms=i * 4 * 60 * 60 * 1000,
                open=open_, high=high, low=low, close=close,
                volume=1000.0, turnover=turnover,
            )
        )
    return rows


def _bars_1d(direction: str) -> list[Bar]:
    rows: list[Bar] = []
    for i in range(90):
        if direction == "up":
            close = 80.0 + i * 0.8
        elif direction == "down":
            close = 180.0 - i * 0.8
        else:
            close = 120.0 + math.sin(i / 3.0)
        open_ = rows[-1].close if rows else close
        rows.append(Bar(i * 86_400_000, open_, max(open_, close) + 0.5, min(open_, close) - 0.5, close, 1000.0, 1_000_000.0))
    return rows


def test_spec_is_frozen_research_only() -> None:
    payload = spec()
    assert payload["version"] == SPEC_VERSION
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["promotion_allowed"] is False
    assert set(payload["regimes"]) == set(REGIMES)


def test_clear_trend_classifies_as_trend() -> None:
    result = classify_symbol("BTCUSDC", _bars_4h("trend"), _bars_1d("up"))
    assert result["regime"] == "TREND"
    assert result["direction"] == "BULL"
    assert result["flags"]["trend"] is True


def test_range_classifies_as_range() -> None:
    result = classify_symbol("BTCUSDC", _bars_4h("range"), _bars_1d("flat"))
    assert result["regime"] == "RANGE"
    assert result["flags"]["high_vol_stress"] is False


def test_compression_classifies_before_range() -> None:
    result = classify_symbol("BTCUSDC", _bars_4h("compression"), _bars_1d("flat"))
    assert result["regime"] == "COMPRESSION"
    assert result["flags"]["compression"] is True


def test_high_vol_stress_has_priority() -> None:
    result = classify_symbol("BTCUSDC", _bars_4h("high_vol"), _bars_1d("flat"))
    assert result["regime"] == "HIGH_VOL_STRESS"
    assert result["flags"]["high_vol_stress"] is True


def test_market_snapshot_uses_btc_anchor_and_breadth() -> None:
    rows = [
        {"symbol": "BTCUSDC", "regime": "TREND", "direction": "BULL"},
        {"symbol": "ETHUSDC", "regime": "TREND", "direction": "BULL"},
        {"symbol": "SOLUSDC", "regime": "TREND", "direction": "BULL"},
        {"symbol": "XRPUSDC", "regime": "RANGE", "direction": "BULL"},
        {"symbol": "DOGEUSDC", "regime": "RANGE", "direction": "BULL"},
    ]
    snapshot = build_market_snapshot(rows, captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc))
    assert snapshot["global_regime"] == "TREND"
    assert snapshot["dominant_direction"] == "BULL"
    assert snapshot["btc_anchor"] == {"regime": "TREND", "direction": "BULL"}
    assert snapshot["promotion_allowed"] is False


def test_universe_is_usdc_nonstable_and_btc_is_forced() -> None:
    tickers = [
        {"symbol": "ETHUSDC", "turnover24h": "1000"},
        {"symbol": "SOLUSDC", "turnover24h": "900"},
        {"symbol": "XRPUSDC", "turnover24h": "800"},
        {"symbol": "USDTUSDC", "turnover24h": "5000"},
        {"symbol": "ETHUSDT", "turnover24h": "9000"},
        {"symbol": "BTCUSDC", "turnover24h": "100"},
    ]
    selected = _select_universe(tickers, limit=3)
    assert "BTCUSDC" in selected
    assert "USDTUSDC" not in selected
    assert all(symbol.endswith("USDC") for symbol in selected)
