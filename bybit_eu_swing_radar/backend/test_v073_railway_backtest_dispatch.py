from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("RADAR_API_KEY", "test-radar-key")
os.environ.setdefault("COINALYZE_API_KEY", "test-coinalyze-key")

import app.main as main


BACKEND = Path(__file__).resolve().parent
REPO_ROOT = BACKEND.parent.parent


@pytest.mark.asyncio
async def test_railway_dispatch_starts_one_batch_and_blocks_overlap(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_backtest_batch():
        started.set()
        await release.wait()
        return {"status": "RUNNING", "completed": 2}

    monkeypatch.setattr(main, "run_backtest_batch", fake_run_backtest_batch)
    main._backtest_task = None

    response = await main.day_trade_backtest_run_batch()
    assert response["accepted"] is True
    assert response["strategy_version"] == "0.7.3"
    assert response["job_name"] == "v073-90d-netrr-structural-barrier"
    assert response["execution"] == "railway_background_batch"

    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(HTTPException) as exc_info:
        await main.day_trade_backtest_run_batch()
    assert exc_info.value.status_code == 409

    task = main._backtest_task
    assert task is not None
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert main._backtest_task is None


def test_dispatch_route_is_post_202_and_api_key_protected():
    route = next(
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/v1/day-trade/backtest/run-batch"
    )
    assert "POST" in route.methods
    assert route.status_code == 202
    dependency_calls = {
        dependency.call for dependency in route.dependant.dependencies
    }
    assert main.require_api_key in dependency_calls


def test_manual_workflow_uses_production_api_not_database_or_bybit_runner():
    workflow = (REPO_ROOT / ".github" / "workflows" / "v073-backtest-manual.yml").read_text(
        encoding="utf-8"
    )
    assert "PRODUCTION_RADAR_API_BASE_URL" in workflow
    assert "PRODUCTION_RADAR_API_KEY" in workflow
    assert "run_production_backtest_batches.py" in workflow
    assert "BACKTEST_DATABASE_URL" not in workflow
    assert "DATABASE_URL:" not in workflow
    assert "backtest_worker.py" not in workflow


def test_production_batch_client_dispatches_and_polls_existing_status_endpoint():
    script = (BACKEND / "scripts" / "run_production_backtest_batches.py").read_text(
        encoding="utf-8"
    )
    assert '"POST", "/v1/day-trade/backtest/run-batch"' in script
    assert '"GET", "/v1/day-trade/backtest/status"' in script
    assert "api.bybit.eu" not in script
    assert "asyncpg" not in script
    assert "DATABASE_URL" not in script
