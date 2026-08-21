from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure import effect_test_v3

SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")


def _rows(counts):
    base = datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)
    rows = []
    signal_id = 1
    for symbol, count in zip(SYMBOLS, counts):
        for _ in range(count):
            value = float(signal_id)
            rows.append({
                "signal_id": signal_id,
                "strategy_version": "0.7.5",
                "symbol": symbol,
                "opened_at": base + timedelta(minutes=signal_id),
                "flow_book_concordance_60s": value,
                "side_microprice_displacement_bps_15s": value * 0.5,
                "side_book_pressure_ratio_60s": value * 0.25,
                "spread_bps_mean_15s": 100.0 - value,
            })
            signal_id += 1
    return rows


def test_outcome_sql_uses_actual_cost_adjusted_journal_column():
    normalized = " ".join(effect_test_v3.OUTCOME_SQL.split())
    assert "SELECT id AS signal_id, symbol, opened_at, net_r" in normalized
    assert "AND net_r IS NOT NULL" in normalized
    assert "net_r_after_costs" not in effect_test_v3.OUTCOME_SQL


def test_earliest_ready_prefix_is_frozen_at_60():
    cohort, gate = effect_test_v3.select_earliest_ready_cohort(_rows((30, 15, 25)), SYMBOLS)
    assert gate["cohort_frozen"] is True
    assert len(cohort) == 60
    counts = {symbol: sum(row["symbol"] == symbol for row in cohort) for symbol in SYMBOLS}
    assert all(counts[symbol] >= 10 for symbol in SYMBOLS)


def test_strategy_contamination_fails_closed():
    rows = _rows((20, 20, 20))
    rows[0]["strategy_version"] = "0.7.4"
    with pytest.raises(ValueError, match="strategy contamination"):
        effect_test_v3.select_earliest_ready_cohort(rows, SYMBOLS)


@pytest.mark.asyncio
async def test_outcomes_are_not_queried_before_60_10(monkeypatch):
    called = False

    async def _connect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("DB must remain unopened")

    monkeypatch.setattr(effect_test_v3.asyncpg, "connect", _connect)
    with pytest.raises(RuntimeError, match="below preregistered minimum"):
        await effect_test_v3.load_closed_outcomes("postgres://example", _rows((19, 20, 20)), SYMBOLS)
    assert called is False


def test_complete_analysis_is_descriptive_deterministic_and_never_promotes():
    cohort = _rows((20, 20, 20))
    outcomes = [{"signal_id": row["signal_id"], "net_r": float(row["signal_id"])} for row in cohort]
    first = effect_test_v3.analyze_preregistered_effects(cohort, outcomes, SYMBOLS)
    second = effect_test_v3.analyze_preregistered_effects(cohort, outcomes, SYMBOLS)
    assert first == second
    assert first["status"] == "COMPLETE"
    assert first["outcome_visible"] is True
    assert first["promotion_allowed"] is False
    assert first["threshold_search_allowed"] is False
    assert first["model_search_allowed"] is False
    assert [item["id"] for item in first["results"]] == [
        "H1_FLOW_BOOK_CONCORDANCE",
        "H2_MICROPRICE_DISPLACEMENT",
        "H3_BOOK_CHURN_PRESSURE",
        "H4_SPREAD_COST",
    ]
    assert all(item["measured_effect_is_descriptive"] is True for item in first["results"])


def test_missing_closed_outcome_produces_no_partial_effects():
    cohort = _rows((20, 20, 20))
    outcomes = [{"signal_id": row["signal_id"], "net_r": 0.1} for row in cohort[:-1]]
    result = effect_test_v3.analyze_preregistered_effects(cohort, outcomes, SYMBOLS)
    assert result["status"] == "WAITING_FOR_CLOSED_OUTCOMES"
    assert result["outcome_visible"] is False
    assert result["results"] == []
    assert result["missing_outcomes"] == 1
