from datetime import datetime, timedelta, timezone

from research_interactions_v1 import ANALYSIS_VERSION, build_interaction_report


def _row(split, opened_at, expansion, regime, bars, net):
    return {
        "dataset_split": split,
        "opened_at": opened_at,
        "expansion_score": expansion,
        "btc_volatility_regime": regime,
        "bars_from_sweep_to_confirmation": bars,
        "base_net_r": net,
    }


def test_interaction_selection_uses_discovery_only_and_freezes_validation():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []

    # Discovery: provide enough rows that the discovery-only upper quartile
    # can satisfy the production MIN_DISCOVERY_N guard without weakening it.
    for i in range(800):
        expansion = 90.0 if i >= 600 else 20.0 + (i % 50)
        regime = "EXPANDING" if i >= 600 else "NORMAL"
        bars = 3 if i % 2 == 0 else 4
        net = 1.2 if i >= 600 else -0.4
        rows.append(_row("DEVELOPMENT", start + timedelta(hours=3 * i), expansion, regime, bars, net))

    # Validation values are intentionally extreme and cannot affect discovery selection.
    for i in range(120):
        expansion = 95.0 if i < 60 else 10.0
        regime = "EXPANDING" if i < 60 else "NORMAL"
        bars = 3
        net = 0.8 if i < 60 else -0.8
        rows.append(_row("VALIDATION", development_end + timedelta(hours=8 * i), expansion, regime, bars, net))

    report = build_interaction_report(rows, start_at=start, development_end_at=development_end)
    assert report["analysis_version"] == ANALYSIS_VERSION
    assert report["research_only"] is True
    assert report["promotion_allowed"] is False
    assert report["selection_policy"]["winner_selected_on"] == "DEVELOPMENT only"
    assert report["selection_policy"]["validation_threshold_search"] is False
    assert report["selected_on_discovery"] is not None
    assert report["selected_validation_result"] is not None


def test_interaction_report_never_promotes_even_when_screen_passes():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []
    for i in range(800):
        high = i % 4 == 0
        rows.append(
            _row(
                "DEVELOPMENT",
                start + timedelta(hours=3 * i),
                100.0 if high else float(i % 70),
                "EXPANDING" if high else "NORMAL",
                3 if high else 1,
                1.0 if high else -0.5,
            )
        )
    for i in range(200):
        high = i % 2 == 0
        rows.append(
            _row(
                "VALIDATION",
                development_end + timedelta(hours=6 * i),
                100.0 if high else 10.0,
                "EXPANDING" if high else "NORMAL",
                3 if high else 1,
                1.0 if high else -0.5,
            )
        )
    report = build_interaction_report(rows, start_at=start, development_end_at=development_end)
    assert report["promotion_allowed"] is False
    assert len(report["candidate_discovery_results"]) == 4
    assert len(report["block_stability"]["discovery_30d_blocks"]) == 4
    assert len(report["block_stability"]["validation_30d_blocks"]) == 2
