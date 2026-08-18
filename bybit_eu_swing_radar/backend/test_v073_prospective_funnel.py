from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from research import prospective_funnel_v073 as funnel


def _base_snapshot(*, strict: bool = True) -> dict:
    return {
        "candidate_built": True,
        "pass_reclaim": True,
        "pass_structure_5m": True,
        "pass_volume_confirmation": True,
        "pass_structure_15m": True,
        "pass_tradeable": True,
        "pass_side_execution_model": True,
        "pass_expansion": True,
        "pass_direction": True,
        "pass_quality": True,
        "pass_setup": True,
        "pass_target_path": True,
        "pass_rr": True,
        "pass_score_gates": True,
        "pass_strict_eligible": strict,
        "pass_strict_trade": strict,
        "near_strict": True,
        "first_failed_gate": "PASSED_STRICT_TRADE",
        "borrowability_status": "HISTORICAL_UNVERIFIED_TECHNICAL_ONLY",
    }


def test_forward_short_execution_is_fail_closed_without_current_borrowability():
    result = funnel._apply_current_execution_semantics(
        _base_snapshot(),
        side="short",
        current_shortable=False,
    )
    assert result["pass_tradeable"] is True
    assert result["pass_side_execution_model"] is False
    assert result["pass_strict_eligible"] is False
    assert result["pass_strict_trade"] is False
    assert result["first_failed_gate"] == "SIDE_EXECUTION_MODEL"
    assert result["borrowability_status"] == "CURRENT_USDC_MARGIN_BLOCKED"


def test_forward_short_execution_passes_with_current_usdc_margin_borrowability():
    result = funnel._apply_current_execution_semantics(
        _base_snapshot(),
        side="short",
        current_shortable=True,
    )
    assert result["pass_side_execution_model"] is True
    assert result["pass_strict_trade"] is True
    assert result["first_failed_gate"] == "PASSED_STRICT_TRADE"
    assert result["borrowability_status"] == "CURRENT_USDC_MARGIN_CONFIRMED"


def test_long_gate_snapshot_is_not_reinterpreted():
    original = _base_snapshot()
    result = funnel._apply_current_execution_semantics(
        original,
        side="long",
        current_shortable=False,
    )
    assert result == original
    assert result is not original


def test_schema_is_label_free_and_version_pinned():
    schema = funnel.PROSPECTIVE_SCHEMA_SQL.lower()
    assert funnel.STRATEGY_VERSION == "0.7.3"
    for forbidden in (
        "realized_pnl",
        "gross_r",
        "mfe_r",
        "mae_r",
        "win_loss",
        "outcome_label",
    ):
        assert forbidden not in schema


def test_first_run_boundary_prevents_historical_backfill(monkeypatch):
    captured_at = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    before = captured_at - timedelta(minutes=5)
    after = captured_at + timedelta(minutes=5)

    events = [
        {
            "sweep_time": before.isoformat(),
            "sweep_index": 1,
            "sweep_depth_atr": 0.2,
            "reclaim_confirmed": True,
            "structure_shift_5m": True,
            "volume_confirmed": True,
            "structure_confirmed_15m": True,
            "volume_ratio_5m": 2.0,
            "failure_reasons": [],
        },
        {
            "sweep_time": after.isoformat(),
            "sweep_index": 2,
            "sweep_depth_atr": 0.2,
            "reclaim_confirmed": True,
            "structure_shift_5m": True,
            "volume_confirmed": True,
            "structure_confirmed_15m": True,
            "volume_ratio_5m": 2.0,
            "failure_reasons": [],
        },
    ]
    monkeypatch.setattr(funnel, "scan_sweep_setups", lambda *args, **kwargs: events)

    fake_diagnostics = SimpleNamespace(
        build_research_candidate=lambda analysis, side, event: {
            "expansion_score": 80.0,
            "side_direction_score": 60.0,
            "quality_score": 80.0,
            "setup_score": 80.0,
            "expected_rr": 2.0,
            "metrics": {"target_path_valid": True},
        },
        gate_snapshot=lambda candidate, side, event, current_shortable_proxy: _base_snapshot(),
    )
    monkeypatch.setitem(sys.modules, "diagnostics_v073", fake_diagnostics)

    analysis = SimpleNamespace(
        instrument=SimpleNamespace(symbol="BTCUSDC"),
        shortable=True,
        bars_5m=[],
        bars_15m=[],
    )
    rows = funnel._analysis_snapshot_rows(
        [analysis],
        captured_at=captured_at,
        prospective_start_at=captured_at,
        source_commit_sha="abc",
    )
    # The pre-boundary event is excluded, and a future event is excluded too.
    assert rows == []


def test_post_boundary_event_is_captured_without_outcome_fields(monkeypatch):
    prospective_start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    captured_at = prospective_start + timedelta(minutes=30)
    sweep_at = prospective_start + timedelta(minutes=10)
    event = {
        "sweep_time": sweep_at.isoformat(),
        "sweep_index": 7,
        "sweep_depth_atr": 0.3,
        "reclaim_confirmed": True,
        "structure_shift_5m": True,
        "volume_confirmed": True,
        "structure_confirmed_15m": True,
        "volume_ratio_5m": 1.8,
        "failure_reasons": [],
    }
    monkeypatch.setattr(funnel, "scan_sweep_setups", lambda *args, **kwargs: [event])
    fake_diagnostics = SimpleNamespace(
        build_research_candidate=lambda analysis, side, event: {
            "expansion_score": 70.0,
            "side_direction_score": 50.0,
            "quality_score": 75.0,
            "setup_score": 74.0,
            "expected_rr": 1.9,
            "metrics": {"target_path_valid": True},
        },
        gate_snapshot=lambda candidate, side, event, current_shortable_proxy: _base_snapshot(),
    )
    monkeypatch.setitem(sys.modules, "diagnostics_v073", fake_diagnostics)
    analysis = SimpleNamespace(
        instrument=SimpleNamespace(symbol="BTCUSDC"),
        shortable=True,
        bars_5m=[],
        bars_15m=[],
    )

    rows = funnel._analysis_snapshot_rows(
        [analysis],
        captured_at=captured_at,
        prospective_start_at=prospective_start,
        source_commit_sha="abc",
    )
    assert len(rows) == 2  # one long + one short snapshot for the same mocked event
    for row in rows:
        payload = row["snapshot_payload"]
        assert payload["label_free"] is True
        assert "outcome" not in payload
        assert "realized_pnl" not in payload
        assert row["sweep_time"] == sweep_at
