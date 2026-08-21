import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

from app.models import DayTradeCandidate, PriceZone
from app.repository import _rankable_day_watch


def _candidate(**overrides):
    payload = {
        "symbol": "BTCUSDC",
        "base_asset": "BTC",
        "quote_asset": "USDC",
        "strategy_mode": "DAY_TRADE",
        "side": "long",
        "category": "WATCH_ONLY",
        "state": "WATCH",
        "grade": "B",
        "technical_grade": "B",
        "watch_bucket": "BARRIER_BLOCKED_VALID_SETUP",
        "decision": "WAIT",
        "setup_type": "IMPULSE_BREAKOUT",
        "setup_state": "VALID",
        "entry_state": "BLOCKED_BY_BARRIER",
        "execution_valid": True,
        "rr_valid": False,
        "reference_entry": 69_961.4,
        "last_price": 69_961.4,
        "tradeable": True,
        "shortable": True,
        "execution_status": "DAY_TRADE_EXECUTABLE",
        "execution_modes": ["USDC_SPOT"],
        "expansion_score": 62.05,
        "direction_score": 57.6,
        "side_direction_score": 57.6,
        "quality_score": 99.99,
        "setup_score": 71.88,
        "context_4h": "bullish",
        "structure_1h": "bullish",
        "structure_15m": "bullish",
        "timeframe_conflict": False,
        "trigger": {"triggered": True, "price": 69_863.5},
        "entry_zone": PriceZone(low=69_961.4, high=69_980.0),
        "stop": 69_569.8,
        "invalidation": "hard stop",
        "targets": [70_300.0, 70_700.0, 71_000.0],
        "expected_rr": 0.0,
        "expected_holding_time": "30 minutes to 8 hours",
        "metrics": {"target_path_valid": False, "setup_valid": True},
        "derivatives": {},
        "why_now": [],
        "bullish_scenario": "x",
        "bearish_scenario": "y",
        "weakest_point": "barrier",
        "risks": [],
        "data_quality": "GOOD",
        "missing_data": [],
        "data_as_of": "2026-08-21T12:00:00+00:00",
    }
    payload.update(overrides)
    return DayTradeCandidate.model_validate(payload)


def test_valid_barrier_blocked_setup_is_rankable_even_with_zero_current_rr():
    assert _rankable_day_watch(_candidate()) is True


def test_4h_conflict_does_not_hide_valid_day_setup():
    assert _rankable_day_watch(_candidate(timeframe_conflict=True)) is True
