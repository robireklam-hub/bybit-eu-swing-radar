import inspect
import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

import backtest
import day_worker
import journal_core
from app import repository


def test_live_day_version_moves_to_v077_while_v076_is_frozen():
    assert day_worker.V076_DAY_STRATEGY_VERSION == "0.7.6"
    assert day_worker.DAY_STRATEGY_VERSION == "0.7.7"
    assert repository.CURRENT_DAY_STRATEGY_VERSION == "0.7.7"
    assert journal_core.STRATEGY_VERSION == "0.7.7"


def test_historical_replay_remains_frozen_at_v075():
    assert day_worker.V075_DAY_STRATEGY_VERSION == "0.7.5"
    assert backtest.STRATEGY_VERSION == "0.7.5"
    assert backtest.DAY_BREAKOUT_ACTIVE_BARS == 2


def test_replay_calls_candidate_builder_with_explicit_historical_version():
    source = inspect.getsource(backtest.replay_symbol)
    assert "strategy_version=STRATEGY_VERSION" in source
