from datetime import datetime, timedelta, timezone

from scripts.production_swing_liquidity_forward_status import run_check, validate_status


def _payload(**overrides):
    now = datetime(2026, 8, 18, 0, 0, tzinfo=timezone.utc)
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "study": "swing-liquidity-validation-v1",
        "capture_count": 3,
        "first_capture_at": (now - timedelta(hours=2)).isoformat(),
        "last_capture_at": (now - timedelta(minutes=5)).isoformat(),
        "candidate_observations": 72,
        "orderbook_errors": 0,
        "turnover_tiers": {"50K_100K": 4, "100K_250K": 7},
        "spread_tiers": {"LE_10": 10},
    }
    payload.update(overrides)
    return payload, now


def test_validate_status_accepts_fresh_exact_durable_capture():
    payload, now = _payload()
    expected = datetime.fromisoformat(payload["last_capture_at"])
    assert validate_status(payload, now=now, expected_capture_at=expected) == []


def test_validate_status_rejects_stale_or_empty_state():
    payload, now = _payload(
        capture_count=0,
        candidate_observations=0,
        last_capture_at=(datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc) - timedelta(hours=1)).isoformat(),
        turnover_tiers={},
        spread_tiers={},
    )
    errors = validate_status(payload, now=now)
    assert "no_durable_captures" in errors
    assert "no_durable_observations" in errors
    assert any(error.startswith("stale_last_capture:") for error in errors)
    assert "missing_turnover_tier_coverage" in errors
    assert "missing_spread_tier_coverage" in errors


def test_validate_status_requires_both_preregistered_turnover_exposures():
    payload, now = _payload(turnover_tiers={"100K_250K": 7, "GE_1M": 3})
    errors = validate_status(payload, now=now)
    assert "missing_below_100k_research_exposure" in errors
    assert "missing_current_gate_comparator_exposure" not in errors

    payload, now = _payload(turnover_tiers={"25K_50K": 5, "50K_100K": 6})
    errors = validate_status(payload, now=now)
    assert "missing_below_100k_research_exposure" not in errors
    assert "missing_current_gate_comparator_exposure" in errors


def test_validate_status_rejects_previous_fresh_capture_when_exact_capture_expected():
    payload, now = _payload(last_capture_at=(datetime(2026, 8, 17, 23, 55, tzinfo=timezone.utc)).isoformat())
    expected = datetime(2026, 8, 17, 23, 58, tzinfo=timezone.utc)
    errors = validate_status(payload, now=now, expected_capture_at=expected)
    assert any(error.startswith("exact_capture_not_persisted:") for error in errors)


def test_run_check_requires_research_only_nonpromotion_contract():
    payload, now = _payload(promotion_allowed=True)

    def fetch(url, api_key, timeout):
        return payload

    expected = datetime.fromisoformat(payload["last_capture_at"])
    assert run_check(
        "https://example.test",
        "secret",
        fetch=fetch,
        now=now,
        expected_capture_at=expected,
    ) == 1
