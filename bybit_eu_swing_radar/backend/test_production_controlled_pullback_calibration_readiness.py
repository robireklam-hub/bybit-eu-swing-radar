from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from scripts.production_controlled_pullback_calibration_readiness import summarize_payloads
from research.microstructure.controlled_pullback_calibration_v1 import MIN_ROWS_PER_SYMBOL


def _rows(symbol: str, count: int):
    rows = []
    base = 1_775_000_000
    for index in range(count + 12):
        second = base + index * 5
        from datetime import datetime, timezone
        at = datetime.fromtimestamp(second, tz=timezone.utc).isoformat()
        rows.append({
            "symbol": symbol,
            "bucket_start": at,
            "bucket_seconds": 5,
            "mid": 100.0 + index * 0.01,
            "signed_quote_flow": 25.0,
            "total_quote_volume": 100.0,
            "bid_added_quote": 50.0,
            "bid_removed_quote": 10.0,
            "ask_added_quote": 10.0,
            "ask_removed_quote": 30.0,
        })
    return rows


def _payload(symbol: str, eligible_count: int):
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "promotion_allowed": False,
        "rows": _rows(symbol, eligible_count),
    }


def test_readiness_requires_minimum_eligible_rows_for_every_symbol():
    payloads = {
        "BTCUSDC": _payload("BTCUSDC", MIN_ROWS_PER_SYMBOL),
        "ETHUSDC": _payload("ETHUSDC", MIN_ROWS_PER_SYMBOL),
        "SOLUSDC": _payload("SOLUSDC", MIN_ROWS_PER_SYMBOL - 1),
    }
    status = summarize_payloads(payloads)
    assert status["calibration_sample_ready"] is False
    assert status["missing_sample_symbols"] == ["SOLUSDC"]
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert status["live_strategy_mutation"] is False


def test_readiness_passes_when_all_symbols_meet_frozen_sample_gate():
    payloads = {
        symbol: _payload(symbol, MIN_ROWS_PER_SYMBOL)
        for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    }
    status = summarize_payloads(payloads)
    assert status["calibration_sample_ready"] is True
    assert status["missing_sample_symbols"] == []
    assert all(count >= MIN_ROWS_PER_SYMBOL for count in status["eligible_rows_per_symbol"].values())


def test_contract_failures_are_rejected():
    import pytest
    payloads = {
        symbol: _payload(symbol, MIN_ROWS_PER_SYMBOL)
        for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    }
    payloads["ETHUSDC"]["outcome_fields_read"] = True
    with pytest.raises(ValueError, match="label-blind contract failed"):
        summarize_payloads(payloads)


def test_readiness_script_bootstraps_backend_imports_when_run_directly(tmp_path):
    script = Path(__file__).resolve().parent / "scripts" / "production_controlled_pullback_calibration_readiness.py"
    env = os.environ.copy()
    env.pop("PRODUCTION_RADAR_API_BASE_URL", None)
    env.pop("PRODUCTION_RADAR_API_KEY", None)
    env.pop("EXPECTED_SHA", None)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "FAIL required calibration readiness configuration is missing" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
