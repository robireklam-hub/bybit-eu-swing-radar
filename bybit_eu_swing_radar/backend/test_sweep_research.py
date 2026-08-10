from sweep_research import (
    ResearchBar,
    SweepResearchConfig,
    evaluate_sweep_at_index,
)

FIVE = 5 * 60 * 1000


def bar(i, o, h, l, c, v=100.0):
    return ResearchBar(
        start_ms=i * FIVE,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=v,
        turnover=v * c,
    )


def base_history():
    # 24 calm bars around 100-101, enough for ATR + volume baseline.
    rows = []
    for i in range(24):
        base = 100.0 + (i % 3) * 0.10
        rows.append(bar(i, base, base + 0.45, base - 0.45, base + 0.05, 100.0))
    return rows


def build_valid_long():
    rows = base_history()
    # Prior 12-bar liquidity low is around 99.55.
    # Prior 6-bar structure high is around 100.65.
    i = len(rows)
    rows.append(bar(i, 100.0, 100.25, 99.25, 99.80, 180.0))   # sweep + reclaim
    rows.append(bar(i+1, 99.80, 100.45, 99.70, 100.35, 150.0))
    rows.append(bar(i+2, 100.35, 101.10, 100.20, 100.90, 180.0)) # 5m shift
    return rows, i


def build_valid_short():
    rows = base_history()
    i = len(rows)
    rows.append(bar(i, 100.2, 101.05, 99.95, 100.35, 180.0))  # sweep + reclaim
    rows.append(bar(i+1, 100.35, 100.45, 99.70, 99.85, 150.0))
    rows.append(bar(i+2, 99.85, 99.95, 98.90, 99.10, 180.0))  # 5m shift
    return rows, i


def test_valid_long_sequence():
    rows, sweep = build_valid_long()
    cfg = SweepResearchConfig(volume_confirmation_ratio=1.20)
    result = evaluate_sweep_at_index(rows, sweep, "long", config=cfg)
    assert result["sweep_detected"] is True
    assert result["reclaim_confirmed"] is True
    assert result["structure_shift_5m"] is True
    assert result["structure_15m_state"] == "BULLISH_SHIFT"
    assert result["structure_confirmed_15m"] is True
    assert result["volume_confirmed"] is True
    assert result["entry_ready"] is True
    assert result["candidate_entry"] is not None
    assert result["candidate_invalidation"] < result["candidate_entry"]
    assert result["bars_from_sweep_to_confirmation"] == 2


def test_valid_short_sequence():
    rows, sweep = build_valid_short()
    cfg = SweepResearchConfig(volume_confirmation_ratio=1.20)
    result = evaluate_sweep_at_index(rows, sweep, "short", config=cfg)
    assert result["sweep_detected"] is True
    assert result["reclaim_confirmed"] is True
    assert result["structure_shift_5m"] is True
    assert result["structure_15m_state"] == "BEARISH_SHIFT"
    assert result["structure_confirmed_15m"] is True
    assert result["volume_confirmed"] is True
    assert result["entry_ready"] is True
    assert result["candidate_entry"] is not None
    assert result["candidate_invalidation"] > result["candidate_entry"]


def test_shallow_sweep_rejected():
    rows = base_history()
    i = len(rows)
    prior_low = min(row.low for row in rows[-12:])
    rows.append(bar(i, 100.0, 100.2, prior_low - 0.01, prior_low + 0.10, 180.0))
    result = evaluate_sweep_at_index(rows, i, "long")
    assert result["sweep_detected"] is False
    assert "SWEEP_TOO_SHALLOW" in result["failure_reasons"]


def test_no_reclaim():
    rows = base_history()
    i = len(rows)
    rows.append(bar(i, 100.0, 100.1, 99.20, 99.30, 180.0))
    rows.append(bar(i+1, 99.30, 99.50, 99.10, 99.35, 120.0))
    rows.append(bar(i+2, 99.35, 99.50, 99.15, 99.40, 120.0))
    rows.append(bar(i+3, 99.40, 99.50, 99.20, 99.45, 120.0))
    result = evaluate_sweep_at_index(rows, i, "long")
    assert result["sweep_detected"] is True
    assert result["reclaim_confirmed"] is False
    assert "NO_RECLAIM_WITHIN_WINDOW" in result["failure_reasons"]


def test_no_structure_shift():
    rows = base_history()
    i = len(rows)
    rows.append(bar(i, 100.0, 100.20, 99.20, 99.80, 180.0))
    for j in range(1, 7):
        rows.append(bar(i+j, 99.8, 100.30, 99.65, 100.0, 130.0))
    result = evaluate_sweep_at_index(rows, i, "long")
    assert result["reclaim_confirmed"] is True
    assert result["structure_shift_5m"] is False
    assert "NO_5M_STRUCTURE_SHIFT" in result["failure_reasons"]


def test_low_volume_is_annotation_failure():
    rows, sweep = build_valid_long()
    # Lower confirmation volume beneath the 1.3x requirement.
    confirm = rows[sweep + 2]
    rows[sweep + 2] = ResearchBar(
        start_ms=confirm.start_ms,
        open=confirm.open,
        high=confirm.high,
        low=confirm.low,
        close=confirm.close,
        volume=105.0,
        turnover=confirm.turnover,
    )
    result = evaluate_sweep_at_index(rows, sweep, "long")
    assert result["structure_shift_5m"] is True
    assert result["volume_confirmed"] is False
    assert "VOLUME_NOT_CONFIRMED" in result["failure_reasons"]


def test_future_bars_do_not_change_confirmation_values():
    rows, sweep = build_valid_long()
    cfg = SweepResearchConfig(volume_confirmation_ratio=1.20)
    before = evaluate_sweep_at_index(rows, sweep, "long", config=cfg)
    # Add extreme future bars after the confirmation window.
    extended = rows + [
        bar(len(rows), 101.0, 120.0, 80.0, 110.0, 1000.0),
        bar(len(rows)+1, 110.0, 130.0, 70.0, 90.0, 1000.0),
    ]
    after = evaluate_sweep_at_index(extended, sweep, "long", config=cfg)
    keys = [
        "sweep_level",
        "sweep_price",
        "reclaim_close",
        "structure_shift_level_5m",
        "candidate_entry",
        "candidate_invalidation",
        "bars_from_sweep_to_confirmation",
    ]
    for key in keys:
        assert before[key] == after[key]


def test_scan_performance_smoke():
    from sweep_research import scan_sweep_setups
    rows = base_history()
    for i in range(len(rows), 2024):
        base = 100.0 + (i % 5) * 0.03
        rows.append(bar(i, base, base + 0.35, base - 0.35, base + 0.02, 100.0))
    result = scan_sweep_setups(rows, "long")
    assert isinstance(result, list)

if __name__ == "__main__":
    tests = [
        test_valid_long_sequence,
        test_valid_short_sequence,
        test_shallow_sweep_rejected,
        test_no_reclaim,
        test_no_structure_shift,
        test_low_volume_is_annotation_failure,
        test_future_bars_do_not_change_confirmation_values,
        test_scan_performance_smoke,
    ]
    for test in tests:
        test()
        print("PASS", test.__name__)
