from datetime import datetime, timezone

import pytest

import day_worker
from day_worker import DayAnalysis, build_day_candidate
from journal_core import build_signal_record
from worker import Bar, Instrument


def _bar(i: int, *, close: float = 99.5, high: float = 100.0, low: float = 99.0) -> Bar:
    return Bar(i * 300_000, 99.5, high, low, close, 10.0, 1000.0)


def _analysis(*, follow_close: float, tradeable: bool = True, shortable: bool = True) -> DayAnalysis:
    prior = [_bar(i) for i in range(12)]
    prior[-1] = _bar(11, close=99.8)
    bars = prior + [_bar(12, close=100.2, high=100.4, low=99.5)]
    bars.append(_bar(13, close=follow_close, high=max(follow_close + 0.2, 100.4), low=100.05))
    instrument = Instrument(
        symbol="BTCUSDC", base="BTC", quote="USDC", margin_trading="both", tick_size=0.1,
        turnover_24h=1e9, volume_24h=1e4, last_price=follow_close,
        bid=follow_close - 0.1, ask=follow_close, spread_bps=1.0,
        price_change_24h_pct=1.0, tradeable=tradeable,
        liquidity_reasons=[] if tradeable else ["blocked"], discovery_source="mandatory",
    )
    bars15 = [Bar(i * 900_000, 99.0, 100.0, 98.8, 99.5, 20.0, 2000.0) for i in range(8)]
    return DayAnalysis(
        instrument=instrument, bars_5m=bars, bars_15m=bars15, bars_1h=[], bars_4h=[],
        atr_5m=1.0, atr_15m=2.0, rolling_vwap_24h=99.0,
        ema20_15m=99.0, ema50_15m=98.5, ema20_1h=99.0, ema50_1h=98.0,
        ema20_4h=99.0, ema50_4h=98.0, return_15m_pct=1.0, return_1h_pct=1.0,
        return_4h_pct=1.0, relative_strength_1h=0.0, relative_strength_4h=0.0,
        volume_ratio_5m=1.84, volume_ratio_15m=3.83, atr_ratio_15m=1.2,
        structure_15m="bullish", structure_1h="bullish", structure_4h="bullish",
        expansion_score=62.05, direction_score=57.6, quality_score=99.99,
        derivatives={}, missing_data=[], shortable=shortable, max_borrowing_amount=10.0,
    )


def _no_barrier(*args, **kwargs):
    return None


def _candidate(monkeypatch, *, follow_close=100.35, strategy_version="0.7.7", tradeable=True):
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", _no_barrier)
    return build_day_candidate(
        _analysis(follow_close=follow_close, tradeable=tradeable),
        "long", datetime(2026, 8, 22, tzinfo=timezone.utc), strategy_version=strategy_version,
    )


def test_next_closed_5m_bar_cannot_revoke_valid_confirmed_breakout(monkeypatch):
    candidate = _candidate(monkeypatch)
    assert candidate is not None
    assert candidate["strategy_version"] == "0.7.7"
    assert candidate["trigger"]["age_bars"] == 1
    assert candidate["trigger"]["boundary_held"] is True
    assert candidate["trigger"]["triggered"] is True
    assert candidate["trigger"]["route"] == "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"
    assert candidate["setup_state"] == "VALID"
    assert candidate["rr_valid"] is True
    assert candidate["execution_valid"] is True
    assert candidate["entry_state"] == "ENTRY_CONFIRMED"
    assert candidate["category"] == "STRICT"
    assert candidate["state"] == "TRIGGERED"
    assert candidate["decision"] == "TRADE"


def test_v076_historical_behavior_remains_provisional_on_followthrough(monkeypatch):
    candidate = _candidate(monkeypatch, strategy_version="0.7.6")
    assert candidate is not None
    assert candidate["trigger"]["age_bars"] == 1
    assert candidate["trigger"]["triggered"] is False
    assert candidate["entry_state"] == "ENTRY_PROVISIONAL"
    assert candidate["state"] == "ARMED"
    assert candidate["decision"] == "WAIT"


def test_execution_block_still_revokes_trade(monkeypatch):
    candidate = _candidate(monkeypatch, tradeable=False)
    assert candidate is not None
    assert candidate["trigger"]["triggered"] is True
    assert candidate["execution_valid"] is False
    assert candidate["entry_state"] == "EXECUTION_BLOCKED"
    assert candidate["decision"] == "NO_TRADE"


def test_boundary_loss_removes_persistent_confirmation(monkeypatch):
    analysis = _analysis(follow_close=99.7)
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(day_worker, "nearest_structural_barrier", _no_barrier)
    candidate = build_day_candidate(analysis, "long", datetime(2026, 8, 22, tzinfo=timezone.utc))
    assert candidate is not None
    assert candidate["trigger"]["route"] != "STRUCTURALLY_HELD_5M_RANGE_BREAKOUT"
    assert candidate["decision"] != "TRADE"


def test_persistent_followthrough_reuses_origin_event_for_journal_dedupe(monkeypatch):
    c1 = _candidate(monkeypatch, follow_close=100.35)
    c2 = _candidate(monkeypatch, follow_close=100.45)
    assert c1 and c2
    bars1 = _analysis(follow_close=100.35).bars_5m
    bars2 = _analysis(follow_close=100.45).bars_5m
    regime = {"data_quality": "GOOD"}
    r1 = build_signal_record(c1, bars1, regime)
    r2 = build_signal_record(c2, bars2, regime)
    assert r1 is not None and r2 is not None
    assert c1["trigger"]["event_bar_start_ms"] == c2["trigger"]["event_bar_start_ms"]
    assert r1["signal_key"] == r2["signal_key"]
