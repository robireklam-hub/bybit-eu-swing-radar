import backtest
import day_worker
import journal_core
from app import repository


def test_live_day_version_moves_to_v076_only():
    assert day_worker.DAY_STRATEGY_VERSION == "0.7.6"
    assert repository.CURRENT_DAY_STRATEGY_VERSION == "0.7.6"
    assert journal_core.STRATEGY_VERSION == "0.7.6"


def test_historical_replay_remains_frozen_at_v075():
    assert day_worker.V075_DAY_STRATEGY_VERSION == "0.7.5"
    assert backtest.STRATEGY_VERSION == "0.7.5"
    assert backtest.DAY_BREAKOUT_ACTIVE_BARS == 2
