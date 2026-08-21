from datetime import datetime, timezone
from types import SimpleNamespace

import research.day_barrier_clear_observer_v1 as observer
from worker import Bar


FIVE = 5 * 60 * 1000
FIFTEEN = 15 * 60 * 1000
BASE = 1_787_322_000_000  # stable synthetic epoch in 2026


def _bar(index: int, close: float, *, low: float | None = None, high: float | None = None) -> Bar:
    low = close - 0.5 if low is None else low
    high = close + 0.5 if high is None else high
    return Bar(
        start_ms=BASE + index * FIVE,
        open=close - 0.1,
        high=high,
        low=low,
        close=close,
        volume=100.0 + index,
        turnover=(100.0 + index) * close,
    )


def _bar15(index: int, close: float = 100.0) -> Bar:
    return Bar(
        start_ms=BASE - 20 * FIFTEEN + index * FIFTEEN,
        open=close - 0.2,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=300.0 + index,
        turnover=(300.0 + index) * close,
    )


def _analysis(closes, *, future_extreme_low: float | None = None):
    bars = [_bar(i, value) for i, value in enumerate(closes)]
    if future_extreme_low is not None:
        bars.append(_bar(len(bars), closes[-1], low=future_extreme_low))
    return SimpleNamespace(
        bars_5m=bars,
        bars_15m=[_bar15(i, 100.0 + (i % 3) * 0.2) for i in range(20)],
        instrument=SimpleNamespace(tick_size=0.1, symbol="BTCUSDC"),
    )


def _captured_after_bar(index: int) -> datetime:
    return datetime.fromtimestamp((BASE + index * FIVE + FIVE) / 1000.0, tz=timezone.utc)


def _parent(*, side="long", boundary=100.0, barrier=105.0, captured_index=0):
    return {
        "event_key": "event-1",
        "captured_at": _captured_after_bar(captured_index),
        "symbol": "BTCUSDC",
        "side": side,
        "trigger_route": "CLOSED_5M_RANGE_BREAKOUT",
        "trigger_boundary": boundary,
        "boundary_kind": "RANGE_BREAKOUT_BOUNDARY",
        "frozen_barrier_price": barrier,
        "resolution_status": "PENDING",
    }


def test_long_first_later_closed_5m_barrier_clear_is_terminal(monkeypatch):
    analysis = _analysis([101.0, 102.0, 106.0, 107.0])
    monkeypatch.setattr(observer, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL_NON_OPPOSING")
    monkeypatch.setattr(
        observer,
        "fresh_geometry_as_of_clear",
        lambda analysis, side, index: {
            "status": "COMPLETE",
            "reference_entry": analysis.bars_5m[index].close,
            "inherited_parent_geometry": None,
            "execution_authorized": False,
        },
    )
    result = observer.resolve_parent_against_analysis(_parent(), analysis)
    assert result is not None
    assert result["status"] == "CLEARED"
    assert result["bars_to_resolution"] == 2
    assert result["clear_close"] == 106.0
    assert result["fresh_geometry"]["reference_entry"] == 106.0
    assert result["fresh_geometry"]["inherited_parent_geometry"] is None
    assert result["fresh_geometry"]["execution_authorized"] is False


def test_boundary_loss_wins_before_later_barrier_clear(monkeypatch):
    analysis = _analysis([101.0, 99.5, 106.0])
    monkeypatch.setattr(observer, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL_NON_OPPOSING")
    result = observer.resolve_parent_against_analysis(_parent(), analysis)
    assert result is not None
    assert result["status"] == "INVALIDATED_BOUNDARY"
    assert result["bars_to_resolution"] == 1
    assert result["resolution_reason"] == "ORIGINAL_TRIGGER_OR_RECLAIM_BOUNDARY_LOST"


def test_opposing_closed_15m_structure_wins_before_later_clear(monkeypatch):
    analysis = _analysis([101.0, 102.0, 106.0])
    monkeypatch.setattr(observer, "classify_15m_structure", lambda *args, **kwargs: "BEARISH_SHIFT")
    result = observer.resolve_parent_against_analysis(_parent(), analysis)
    assert result is not None
    assert result["status"] == "INVALIDATED_STRUCTURE"
    assert result["bars_to_resolution"] == 1
    assert result["structure_state_15m"] == "BEARISH_SHIFT"


def test_short_clear_uses_close_below_frozen_barrier(monkeypatch):
    analysis = _analysis([99.0, 98.0, 94.0])
    monkeypatch.setattr(observer, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL_NON_OPPOSING")
    monkeypatch.setattr(observer, "fresh_geometry_as_of_clear", lambda *args, **kwargs: {"status": "COMPLETE"})
    result = observer.resolve_parent_against_analysis(
        _parent(side="short", boundary=100.0, barrier=95.0), analysis
    )
    assert result is not None
    assert result["status"] == "CLEARED"
    assert result["clear_close"] == 94.0


def test_bar_closed_at_or_before_parent_capture_is_not_forward_observation(monkeypatch):
    analysis = _analysis([106.0, 104.0])
    monkeypatch.setattr(observer, "classify_15m_structure", lambda *args, **kwargs: "NEUTRAL_NON_OPPOSING")
    # Parent is captured only after bar 0 has closed. Its already-known 106 close
    # must not be reused as a prospective barrier-clear event.
    result = observer.resolve_parent_against_analysis(_parent(captured_index=0), analysis)
    assert result is None


def test_fresh_geometry_uses_clear_time_prefix_not_future_extreme():
    closes = [100.0 + i * 0.15 for i in range(25)]
    analysis = _analysis(closes, future_extreme_low=1.0)
    clear_index = 24
    result = observer.fresh_geometry_as_of_clear(analysis, "long", clear_index)
    assert result["status"] == "COMPLETE"
    assert result["reference_entry"] == analysis.bars_5m[clear_index].close
    assert result["stop"] > 90.0
    assert result["inherited_parent_geometry"] is None
    assert result["research_only"] is True
    assert result["execution_authorized"] is False


def test_outcome_sanitizer_is_recursive_and_keeps_context():
    payload = {
        "session": "US",
        "pnl": 100,
        "nested": {"mfe": 3.0, "funding": 0.001},
        "rows": [{"win": True, "oi": 123}],
    }
    assert observer._sanitize(payload) == {
        "session": "US",
        "nested": {"funding": 0.001},
        "rows": [{"oi": 123}],
    }
