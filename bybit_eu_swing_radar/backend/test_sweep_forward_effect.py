from research.sweep_forward_effect import (
    SPEC_VERSION,
    evaluate_effects,
    sample_gate,
    spearman,
    spec,
)


def test_spec_is_research_only_and_frozen() -> None:
    payload = spec()
    assert payload["spec_version"] == SPEC_VERSION
    assert payload["strategy_version"] == "0.7.3"
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_gate_before_outcomes"] is True
    assert payload["promotion_allowed"] is False
    assert payload["sample_gate"] == {
        "minimum_closed_signals": 60,
        "minimum_per_side": 10,
        "minimum_distinct_utc_days": 10,
        "minimum_attribute_coverage_pct": 95.0,
    }
    assert len(payload["hypotheses"]) == 4


def test_sample_gate_fails_closed_and_then_opens() -> None:
    waiting = sample_gate(
        {
            "closed_signal_count": 59,
            "long_count": 30,
            "short_count": 29,
            "distinct_utc_days": 10,
            "attribute_complete_count": 59,
        }
    )
    assert waiting["ready"] is False
    assert any("closed_signals" in reason for reason in waiting["reasons"])

    ready = sample_gate(
        {
            "closed_signal_count": 60,
            "long_count": 30,
            "short_count": 30,
            "distinct_utc_days": 10,
            "attribute_complete_count": 57,
        }
    )
    assert ready["ready"] is True
    assert ready["attribute_coverage_pct"] == 95.0


def test_spearman_handles_ties() -> None:
    assert round(float(spearman([1, 2, 3, 4], [10, 20, 30, 40])), 6) == 1.0
    assert round(float(spearman([1, 2, 3, 4], [40, 30, 20, 10])), 6) == -1.0
    tied = spearman([1, 1, 2, 3], [1, 2, 2, 4])
    assert tied is not None
    assert 0 < tied < 1


def _forward_rows() -> list[dict]:
    rows = []
    for index in range(80):
        day = index // 4 + 1
        side = "long" if index % 2 == 0 else "short"
        aligned = index % 4 in {0, 1}
        depth = 0.15 + (index % 20) * 0.03
        bars = 1 + (index % 6)
        volume = 1.30 + (index % 16) * 0.08
        # Fixed synthetic relationship used only to test statistic direction.
        net_r = 1.6 * depth - 0.22 * bars + 0.45 * volume + (0.45 if aligned else 0.0)
        rows.append(
            {
                "opened_at": f"2026-07-{day:02d}T12:00:00+00:00",
                "side": side,
                "net_r": net_r,
                "mfe_r": max(0.0, net_r + 0.4),
                "mae_r": max(0.0, 0.5 - net_r * 0.05),
                "sweep_depth_atr": depth,
                "bars_from_sweep_to_confirmation": bars,
                "volume_ratio_5m": volume,
                "structure_15m_state": (
                    "BULLISH_SHIFT" if aligned and side == "long"
                    else "BEARISH_SHIFT" if aligned
                    else "NEUTRAL_NON_OPPOSING"
                ),
            }
        )
    return rows


def test_effect_evaluation_is_deterministic_and_never_promotes() -> None:
    rows = _forward_rows()
    first = evaluate_effects(rows)
    second = evaluate_effects(rows)
    assert first == second
    assert first["outcome_sample_size"] == 80
    assert len(first["hypotheses"]) == 4
    assert first["promotion_allowed"] is False
    by_id = {row["id"]: row for row in first["hypotheses"]}
    assert by_id["H1_SWEEP_DEPTH"]["estimate"] > 0
    assert by_id["H2_CONFIRMATION_SPEED"]["estimate"] < 0
    assert by_id["H3_CONFIRMATION_VOLUME"]["estimate"] > 0
    assert by_id["H4_15M_ALIGNMENT"]["estimate"] > 0
