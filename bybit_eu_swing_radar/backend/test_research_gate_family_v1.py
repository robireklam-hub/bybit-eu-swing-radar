from datetime import datetime, timedelta, timezone

from research_gate_family_v1 import build_gate_family_report


def _row(opened_at, *, net_r, expansion=60.0, confirmations=True, reward=True, split="DEVELOPMENT"):
    return {
        "opened_at": opened_at,
        "dataset_split": split,
        "base_net_r": net_r,
        "expansion_score": expansion,
        "pass_volume_confirmation": confirmations,
        "pass_structure_15m": confirmations,
        "pass_target_path": reward,
        "pass_rr": reward,
    }


def test_gate_family_uses_nested_development_holdout_and_never_promotes():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []

    # First 90d train: full chain has a strong relative effect and enough sample.
    for i in range(600):
        opened = start + timedelta(hours=i * 3)
        selected = i % 3 == 0
        rows.append(
            _row(
                opened,
                net_r=0.45 if selected else -0.55,
                expansion=75.0 if selected else 35.0,
                confirmations=selected,
                reward=selected,
            )
        )

    # Final 30d internal holdout: frozen full chain remains positive.
    holdout_start = start + timedelta(days=90)
    for i in range(240):
        opened = holdout_start + timedelta(hours=i * 2)
        selected = i % 2 == 0
        rows.append(
            _row(
                opened,
                net_r=0.30 if selected else -0.50,
                expansion=80.0 if selected else 30.0,
                confirmations=selected,
                reward=selected,
            )
        )

    # Previously seen historical validation: intentionally extreme; it must not
    # affect train winner selection or promotion eligibility.
    validation_start = development_end
    for i in range(120):
        rows.append(
            _row(
                validation_start + timedelta(hours=i * 4),
                net_r=3.0,
                expansion=99.0,
                confirmations=True,
                reward=True,
                split="VALIDATION",
            )
        )

    report = build_gate_family_report(rows, start_at=start, development_end_at=development_end)

    assert report["status"] == "OK"
    assert report["selected_on_train"] is not None
    assert report["internal_holdout_result"]["selected"]["n"] >= 50
    assert report["split_policy"]["historical_validation_status"] == "REUSED_REFERENCE_NOT_UNTOUCHED_OOS"
    assert report["promotion_allowed"] is False
    assert report["reused_external_validation_reference"]["selected"]["average_net_r"] == 3.0


def test_gate_family_fails_internal_edge_when_holdout_expectancy_is_negative():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []
    for i in range(900):
        opened = start + timedelta(hours=i * 2)
        selected = i % 3 == 0
        rows.append(
            _row(
                opened,
                net_r=0.4 if selected else -0.6,
                expansion=70.0 if selected else 30.0,
                confirmations=selected,
                reward=selected,
            )
        )
    holdout_start = start + timedelta(days=90)
    for i in range(180):
        selected = i % 2 == 0
        rows.append(
            _row(
                holdout_start + timedelta(hours=i * 3),
                net_r=-0.2 if selected else -0.5,
                expansion=75.0 if selected else 25.0,
                confirmations=selected,
                reward=selected,
            )
        )

    report = build_gate_family_report(rows, start_at=start, development_end_at=development_end)
    assert report["internal_holdout_edge_pass"] is False
    assert "do not promote" in report["next_step"].lower()
