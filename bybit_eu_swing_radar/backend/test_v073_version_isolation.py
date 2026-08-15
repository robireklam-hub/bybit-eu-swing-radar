from pathlib import Path


BACKEND = Path(__file__).resolve().parent


def test_repository_reads_current_v073_journal_only():
    text = (BACKEND / "app" / "repository.py").read_text(encoding="utf-8")
    assert 'CURRENT_DAY_STRATEGY_VERSION = "0.7.3"' in text
    assert 'CURRENT_DAY_STRATEGY_VERSION = "0.7.2"' not in text
    assert 'strategy_version = $3' in text
    assert 'strategy_version = $4' in text


def test_backtest_endpoints_filter_latest_job_by_v073_strategy_version():
    text = (BACKEND / "app" / "repository.py").read_text(encoding="utf-8")
    filtered_all = 'SELECT * FROM day_trade_backtest_jobs WHERE strategy_version=$1 ORDER BY id DESC LIMIT 1'
    filtered_id = 'SELECT id FROM day_trade_backtest_jobs WHERE strategy_version=$1 ORDER BY id DESC LIMIT 1'
    assert text.count(filtered_all) == 2
    assert text.count(filtered_id) == 1
    assert 'SELECT * FROM day_trade_backtest_jobs ORDER BY id DESC LIMIT 1' not in text
    assert 'SELECT id FROM day_trade_backtest_jobs ORDER BY id DESC LIMIT 1' not in text
    assert 'strategy_version=str(job.get("strategy_version", CURRENT_DAY_STRATEGY_VERSION))' in text


def test_backtest_writer_and_default_job_are_v073():
    text = (BACKEND / "backtest.py").read_text(encoding="utf-8")
    assert 'STRATEGY_VERSION = "0.7.3"' in text
    assert 'v073-90d-netrr-structural-barrier' in text
    assert 'v072-90d-netrr-structural-barrier' not in text
    assert 'replay v0.7.2' not in text


def test_version_isolation_keeps_flow_feature_0722():
    flow = (BACKEND / "flow_context.py").read_text(encoding="utf-8")
    assert 'Day-trade derivatives flow context v0.7.2.2.' in flow
    assert 'does NOT change the v0.7.3 STRICT gates' in flow
