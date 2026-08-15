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
import app.v073_diagnostics_api as diag_api
from diagnostics_v073 import gate_snapshot


BACKEND = Path(__file__).resolve().parent
REPO_ROOT = BACKEND.parent.parent


def _candidate(*, conflict_4h: bool = True) -> dict:
    return {
        "tradeable": True,
        "shortable": True,
        "expansion_score": 70.0,
        "side_direction_score": 50.0,
        "quality_score": 80.0,
        "setup_score": 80.0,
        "expected_rr": 2.0,
        "timeframe_conflict": conflict_4h,
        "metrics": {
            "target_path_valid": True,
            "volume_ratio_5m": 1.5,
        },
    }


def _sweep(**overrides) -> dict:
    payload = {
        "reclaim_confirmed": True,
        "structure_shift_5m": True,
        "volume_confirmed": True,
        "structure_confirmed_15m": True,
    }
    payload.update(overrides)
    return payload


def test_v073_gate_snapshot_does_not_reintroduce_4h_hard_veto():
    gates = gate_snapshot(
        _candidate(conflict_4h=True),
        "long",
        _sweep(),
        current_shortable_proxy=False,
    )
    assert gates["pass_no_timeframe_conflict"] is True
    assert gates["pass_strict_eligible"] is True
    assert gates["pass_strict_trade"] is True
    assert gates["first_failed_gate"] == "PASSED_STRICT_TRADE"


def test_v073_gate_snapshot_reports_trigger_stage_failure_first():
    gates = gate_snapshot(
        _candidate(),
        "long",
        _sweep(volume_confirmed=False),
        current_shortable_proxy=False,
    )
    assert gates["pass_strict_eligible"] is True
    assert gates["pass_strict_trade"] is False
    assert gates["first_failed_gate"] == "VOLUME_1_3X"


def test_v073_waterfall_is_sequential_and_keeps_rejected_sweeps():
    rows = [
        {
            "pass_reclaim": True,
            "pass_structure_5m": True,
            "pass_volume_confirmation": True,
            "pass_structure_15m": True,
            "candidate_built": True,
            "pass_tradeable": True,
            "pass_side_execution_model": True,
            "pass_expansion": True,
            "pass_direction": True,
            "pass_quality": True,
            "pass_setup": True,
            "pass_target_path": True,
            "pass_rr": True,
            "pass_strict_trade": True,
            "first_failed_gate": "PASSED_STRICT_TRADE",
        },
        {
            "pass_reclaim": True,
            "pass_structure_5m": True,
            "pass_volume_confirmation": False,
            "pass_structure_15m": True,
            "candidate_built": True,
            "pass_tradeable": True,
            "pass_side_execution_model": True,
            "pass_expansion": True,
            "pass_direction": True,
            "pass_quality": True,
            "pass_setup": True,
            "pass_target_path": True,
            "pass_rr": True,
            "pass_strict_trade": False,
            "first_failed_gate": "VOLUME_1_3X",
        },
        {
            "pass_reclaim": False,
            "pass_structure_5m": False,
            "pass_volume_confirmation": False,
            "pass_structure_15m": False,
            "candidate_built": False,
            "pass_tradeable": False,
            "pass_side_execution_model": False,
            "pass_expansion": False,
            "pass_direction": False,
            "pass_quality": False,
            "pass_setup": False,
            "pass_target_path": False,
            "pass_rr": False,
            "pass_strict_trade": False,
            "first_failed_gate": "RECLAIM",
        },
    ]
    waterfall, failures = diag_api.waterfall_from_rows(rows)
    by_gate = {row["gate"]: row for row in waterfall}
    assert by_gate["LIQUIDITY_SWEEP"]["passed_count"] == 3
    assert by_gate["RECLAIM"]["passed_count"] == 2
    assert by_gate["STRUCTURE_SHIFT_5M"]["passed_count"] == 2
    assert by_gate["VOLUME_1_3X"]["passed_count"] == 1
    assert by_gate["STRICT_TRADE"]["passed_count"] == 1
    assert {item["key"]: item["count"] for item in failures}["RECLAIM"] == 1
    assert {item["key"]: item["count"] for item in failures}["VOLUME_1_3X"] == 1


@pytest.mark.asyncio
async def test_v073_diagnostic_dispatch_is_single_flight(monkeypatch):
    route = next(
        route
        for route in main.app.routes
        if getattr(route, "path", None)
        == "/v1/day-trade/backtest/diagnostics/v073/run-batch"
    )
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_diagnostic_batch():
        started.set()
        await release.wait()
        return {"status": "RUNNING", "completed": 2}

    monkeypatch.setattr(diag_api, "run_diagnostic_batch", fake_run_diagnostic_batch)
    diag_api._diagnostic_task = None

    response = await route.endpoint()
    assert response["accepted"] is True
    assert response["strategy_version"] == "0.7.3"
    assert response["job_name"] == "v073-90d-sweep-gate-diagnostics"

    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(HTTPException) as exc_info:
        await route.endpoint()
    assert exc_info.value.status_code == 409

    task = diag_api._diagnostic_task
    assert task is not None
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert diag_api._diagnostic_task is None


def test_v073_diagnostic_routes_are_api_key_protected():
    paths = {
        "/v1/day-trade/backtest/diagnostics/v073/run-batch": "POST",
        "/v1/day-trade/backtest/diagnostics/v073/status": "GET",
        "/v1/day-trade/backtest/diagnostics/v073/waterfall": "GET",
        "/v1/day-trade/backtest/diagnostics/v073/edge": "GET",
    }
    for path, method in paths.items():
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == path
        )
        assert method in route.methods
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert main.require_api_key in dependency_calls


def test_v073_diagnostic_workflow_uses_railway_api_not_database_or_bybit():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "v073-diagnostics-manual.yml"
    ).read_text(encoding="utf-8")
    script = (
        BACKEND / "scripts" / "run_production_v073_diagnostics.py"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch" in workflow
    assert "PRODUCTION_RADAR_API_BASE_URL" in workflow
    assert "PRODUCTION_RADAR_API_KEY" in workflow
    assert "run_production_v073_diagnostics.py" in workflow
    assert "DATABASE_URL:" not in workflow
    assert "api.bybit.eu" not in script
    assert "asyncpg" not in script
    assert '"/v1/day-trade/backtest/diagnostics/v073/run-batch"' in script
    assert '"/v1/day-trade/backtest/diagnostics/v073/status"' in script
