from __future__ import annotations

import json
from pathlib import Path

from scripts.run_swing_liquidity_shadow_guarded import run_capture


def _snapshot() -> dict:
    return {
        "captured_at": "2026-08-21T06:00:00+00:00",
        "feature_available_at": "2026-08-21T06:00:01+00:00",
        "trial_fingerprint": "a" * 64,
        "scan_data_as_of": "2026-08-21T05:59:59+00:00",
        "candidate_count": 1,
        "orderbooks": {"BTCUSDC": {}},
        "orderbook_errors": {},
    }


def _persisted(*, execution_authorized: bool = False) -> dict:
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": "swing-liquidity-validation-v1",
        "captured_at": "2026-08-21T06:00:00+00:00",
        "inserted": True,
        "candidate_count": 1,
        "orderbook_count": 1,
        "orderbook_error_count": 0,
        "lifecycle_adoption": {
            "attempted": True,
            "inserted": False,
            "event_type": "DATA_QUALITY_GATE_RECORDED",
            "reason": "waiting_for_fresh_post_data_quality_lineage_capture",
            "prospective_adoption": True,
            "historical_backfill": False,
            "research_only": True,
            "live_strategy_mutated": False,
            "production_eligibility_mutated": False,
            "execution_authorized": execution_authorized,
        },
    }


def _run(tmp_path: Path, result: dict) -> int:
    return run_capture(
        "https://example.test",
        "secret",
        "https://api.bybit.eu",
        tmp_path / "capture.json",
        collect=lambda *_: _snapshot(),
        persist=lambda *_: result,
    )


def test_guarded_runner_accepts_one_safe_natural_capture(tmp_path: Path):
    output = tmp_path / "capture.json"
    calls = {"collect": 0, "persist": 0}

    def collect(base_url: str, api_key: str, bybit_base_url: str) -> dict:
        calls["collect"] += 1
        assert base_url == "https://example.test"
        assert api_key == "secret"
        assert bybit_base_url == "https://api.bybit.eu"
        return _snapshot()

    def persist(base_url: str, api_key: str, snapshot: dict) -> dict:
        calls["persist"] += 1
        assert snapshot["candidate_count"] == 1
        return _persisted()

    assert run_capture(
        "https://example.test",
        "secret",
        "https://api.bybit.eu",
        output,
        collect=collect,
        persist=persist,
    ) == 0
    assert calls == {"collect": 1, "persist": 1}
    assert json.loads(output.read_text())["captured_at"] == "2026-08-21T06:00:00+00:00"


def test_guarded_runner_fails_closed_on_unsafe_lifecycle_response(tmp_path: Path):
    assert _run(tmp_path, _persisted(execution_authorized=True)) == 1


def test_guarded_runner_fails_closed_when_lifecycle_response_is_missing(tmp_path: Path):
    result = _persisted()
    result.pop("lifecycle_adoption")
    assert _run(tmp_path, result) == 1


def test_guarded_runner_fails_closed_on_persisted_capture_timestamp_mismatch(tmp_path: Path):
    result = _persisted()
    result["captured_at"] = "2026-08-21T06:00:05+00:00"
    assert _run(tmp_path, result) == 1


def test_guarded_runner_fails_closed_on_persisted_candidate_count_mismatch(tmp_path: Path):
    result = _persisted()
    result["candidate_count"] = 2
    assert _run(tmp_path, result) == 1


def test_guarded_runner_fails_closed_on_persisted_orderbook_count_mismatch(tmp_path: Path):
    result = _persisted()
    result["orderbook_count"] = 0
    assert _run(tmp_path, result) == 1


def test_guarded_runner_fails_closed_on_persisted_orderbook_error_count_mismatch(tmp_path: Path):
    result = _persisted()
    result["orderbook_error_count"] = 1
    assert _run(tmp_path, result) == 1
