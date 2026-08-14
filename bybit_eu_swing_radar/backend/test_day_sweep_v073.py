from __future__ import annotations

from datetime import datetime, timezone

import backtest
import day_worker
import journal
from sweep_research import ResearchBar, SweepResearchConfig, latest_bar_sweep_setup
from worker import Bar, Instrument

FIVE_MS = 5 * 60 * 1000
FIFTEEN_MS = 15 * 60 * 1000


def _research_bar(i: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> ResearchBar:
    return ResearchBar(
        start_ms=i * FIVE_MS,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        turnover=v * c,
    )


def _valid_long_sweep() -> list[ResearchBar]:
    rows: list[ResearchBar] = []
    for i in range(24):
        base = 100.0 + (i % 3) * 0.10
        rows.append(_research_bar(i, base, base + 0.45, base - 0.45, base + 0.05, 100.0))
    i = len(rows)
    rows.append(_research_bar(i, 100.0, 100.25, 99.25, 99.80, 180.0))
    rows.append(_research_bar(i + 1, 99.80, 100.45, 99.70, 100.35, 150.0))
    rows.append(_research_bar(i + 2, 100.35, 101.10, 100.20, 100.90, 180.0))
    return rows


def _bar(start_ms: int, close: float, *, high: float | None = None, low: float | None = None, volume: float = 100.0) -> Bar:
    h = close + 0.2 if high is None else high
    l = close - 0.2 if low is None else low
    return Bar(
        start_ms=start_ms,
        open=close,
        high=h,
        low=l,
        close=close,
        volume=volume,
        turnover=volume * close,
    )


def _analysis(*, side: str = "long", shortable: bool = True, structure_4h: str | None = None) -> day_worker.DayAnalysis:
    bars_5m = [
        _bar(i * FIVE_MS, 99.0 + i * 0.01, volume=100.0)
        for i in range(100)
    ]
    # Make the latest bar a strong legacy breakout too. v0.7.3 must still refuse
    # TRADE when the sweep detector does not confirm.
    bars_5m[-1] = _bar(99 * FIVE_MS, 102.0, high=102.2, low=101.8, volume=250.0)
    bars_15m = [
        _bar(i * FIFTEEN_MS, 90.0 + i * 0.05, high=90.3 + i * 0.05, low=89.7 + i * 0.05)
        for i in range(120)
    ]
    bars_1h = [
        _bar(i * 60 * 60 * 1000, 95.0 + i * 0.02)
        for i in range(120)
    ]
    bars_4h = [
        _bar(i * 4 * 60 * 60 * 1000, 100.0)
        for i in range(100)
    ]
    instrument = Instrument(
        symbol="TESTUSDC",
        base="TEST",
        quote="USDC",
        margin_trading="both",
        tick_size=0.01,
        turnover_24h=5_000_000.0,
        volume_24h=50_000.0,
        last_price=102.0,
        bid=101.99,
        ask=102.01,
        spread_bps=2.0,
        price_change_24h_pct=1.0,
        tradeable=True,
        liquidity_reasons=[],
        discovery_source="test",
    )
    direction = 100.0 if side == "long" else -100.0
    return day_worker.DayAnalysis(
        instrument=instrument,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        bars_1h=bars_1h,
        bars_4h=bars_4h,
        atr_5m=0.5,
        atr_15m=1.0,
        rolling_vwap_24h=100.0,
        ema20_15m=100.0,
        ema50_15m=99.0,
        ema20_1h=100.0,
        ema50_1h=99.0,
        ema20_4h=100.0,
        ema50_4h=100.0,
        return_15m_pct=1.0,
        return_1h_pct=1.0,
        return_4h_pct=0.0,
        relative_strength_1h=1.0,
        relative_strength_4h=0.0,
        volume_ratio_5m=2.5,
        volume_ratio_15m=1.5,
        atr_ratio_15m=1.0,
        structure_15m="bullish" if side == "long" else "bearish",
        structure_1h="bullish" if side == "long" else "bearish",
        structure_4h=structure_4h or ("bearish" if side == "long" else "bullish"),
        expansion_score=100.0,
        direction_score=direction,
        quality_score=100.0,
        derivatives={},
        missing_data=[],
        shortable=shortable,
        max_borrowing_amount=10_000.0 if shortable else 0.0,
    )


def _sweep_event(side: str) -> dict:
    if side == "long":
        entry, invalidation = 102.0, 101.0
    else:
        entry, invalidation = 102.0, 103.0
    return {
        "research_version": "sweep-research-0.1",
        "research_only": True,
        "side": side,
        "sweep_index": 94,
        "sweep_detected": True,
        "sweep_level": 101.2 if side == "long" else 102.8,
        "sweep_price": invalidation,
        "sweep_depth": 0.2,
        "sweep_depth_atr": 0.4,
        "sweep_time": datetime.fromtimestamp(94 * FIVE_MS / 1000, tz=timezone.utc).isoformat(),
        "reclaim_confirmed": True,
        "reclaim_close": 101.5 if side == "long" else 102.5,
        "reclaim_time": datetime.fromtimestamp(96 * FIVE_MS / 1000, tz=timezone.utc).isoformat(),
        "structure_shift_5m": True,
        "structure_shift_level_5m": 101.8 if side == "long" else 102.2,
        "structure_shift_time_5m": datetime.fromtimestamp(99 * FIVE_MS / 1000, tz=timezone.utc).isoformat(),
        "structure_15m_state": "BULLISH_SHIFT" if side == "long" else "BEARISH_SHIFT",
        "structure_confirmed_15m": True,
        "volume_ratio_5m": 2.5,
        "volume_confirmed": True,
        "bars_from_sweep_to_confirmation": 5,
        "candidate_entry": entry,
        "candidate_invalidation": invalidation,
        "entry_ready": True,
        "failure_reasons": [],
    }


def test_latest_bar_helper_only_returns_current_confirmation():
    rows = _valid_long_sweep()
    cfg = SweepResearchConfig(volume_confirmation_ratio=1.20)
    result = latest_bar_sweep_setup(rows, "long", config=cfg)
    assert result is not None
    assert result["entry_ready"] is True
    assert result["structure_shift_time_5m"].startswith("1970-01-01T02:10:00")

    # Once a later closed bar exists, the older confirmation must not trigger again.
    rows.append(_research_bar(len(rows), 100.9, 101.0, 100.7, 100.8, 100.0))
    assert latest_bar_sweep_setup(rows, "long", config=cfg) is None


def test_4h_conflict_is_context_only_for_confirmed_long(monkeypatch):
    analysis = _analysis(side="long", structure_4h="bearish")
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: _sweep_event("long"))

    candidate = day_worker.build_day_candidate(
        analysis,
        "long",
        datetime.now(timezone.utc),
    )
    assert candidate is not None
    assert candidate["timeframe_conflict"] is True
    assert candidate["category"] == "STRICT"
    assert candidate["state"] == "TRIGGERED"
    assert candidate["decision"] == "TRADE"
    assert candidate["execution_status"] == "DAY_TRADE_EXECUTABLE"
    assert candidate["setup_type"] == "LIQUIDITY_SWEEP_RECLAIM"
    assert candidate["trigger"]["price"] == 102.0
    assert candidate["stop"] == 101.0
    assert candidate["metrics"]["four_hour_conflict_context_only"] is True


def test_legacy_breakout_without_sweep_confirmation_cannot_trade(monkeypatch):
    analysis = _analysis(side="long", structure_4h="bullish")
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: None)

    candidate = day_worker.build_day_candidate(
        analysis,
        "long",
        datetime.now(timezone.utc),
    )
    assert candidate is not None
    assert candidate["category"] == "STRICT"
    assert candidate["trigger"]["triggered"] is False
    assert candidate["decision"] == "WAIT"
    assert candidate["state"] != "TRIGGERED"


def test_short_borrowability_remains_hard_gate(monkeypatch):
    analysis = _analysis(side="short", shortable=False, structure_4h="bullish")
    monkeypatch.setattr(day_worker, "latest_bar_sweep_setup", lambda *args, **kwargs: _sweep_event("short"))

    candidate = day_worker.build_day_candidate(
        analysis,
        "short",
        datetime.now(timezone.utc),
    )
    assert candidate is not None
    assert candidate["trigger"]["triggered"] is True
    assert candidate["shortable"] is False
    assert candidate["category"] == "WATCH_ONLY"
    assert candidate["decision"] == "NO_TRADE"
    assert candidate["execution_status"] == "DAY_TRADE_BLOCKED"


def test_v073_strategy_versions_are_separated():
    assert day_worker.DAY_STRATEGY_VERSION == "0.7.3"
    assert journal.STRATEGY_VERSION == "0.7.3"
    assert backtest.STRATEGY_VERSION == "0.7.3"
