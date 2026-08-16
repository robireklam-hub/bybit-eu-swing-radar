from datetime import datetime, timedelta, timezone

from research_historical_flow_analysis_v2 import build_historical_flow_report


def _row(opened_at, net_r, expansion, one, four, funding, side="long", split="DEVELOPMENT"):
    return {
        "opened_at": opened_at,
        "base_net_r": net_r,
        "expansion_score": expansion,
        "oi_change_1h_pct": one,
        "oi_change_4h_pct": four,
        "funding_rate": funding,
        "side": side,
        "dataset_split": split,
        "historical_flow_available": True,
    }


def test_report_selects_on_train_only_and_keeps_promotion_false():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dev_end = start + timedelta(days=120)
    rows = []

    # 90d train: make high-expansion + OI building the strongest candidate.
    for i in range(800):
        ts = start + timedelta(minutes=i * 150)
        high = i % 4 == 0
        if high:
            rows.append(_row(ts, 0.6, 90, 2.0, 4.0, -0.0001))
        else:
            rows.append(_row(ts, -0.4, 30 + (i % 20), -1.0, -2.0, 0.0001))

    # Internal holdout deliberately negative so no promotion can occur.
    holdout_start = start + timedelta(days=90)
    for i in range(240):
        ts = holdout_start + timedelta(minutes=i * 120)
        high = i % 3 == 0
        rows.append(
            _row(
                ts,
                -0.5 if high else -0.1,
                90 if high else 35,
                2.0 if high else -1.0,
                4.0 if high else -2.0,
                -0.0001 if high else 0.0001,
            )
        )

    # Reused validation is reference-only.
    validation_start = dev_end
    for i in range(120):
        rows.append(
            _row(
                validation_start + timedelta(minutes=i * 120),
                1.0,
                90,
                2.0,
                4.0,
                -0.0001,
                split="VALIDATION",
            )
        )

    report = build_historical_flow_report(rows, start_at=start, development_end_at=dev_end)
    assert report["selected_on_train"] == "high_expansion_x_oi_building"
    assert report["internal_holdout_edge_pass"] is False
    assert report["promotion_allowed"] is False
    assert report["split_policy"]["historical_validation_status"] == "REUSED_REFERENCE_NOT_UNTOUCHED_OOS"


def test_missing_flow_never_becomes_a_hard_gate():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dev_end = start + timedelta(days=120)
    rows = [
        {
            "opened_at": start + timedelta(days=1),
            "base_net_r": -0.2,
            "expansion_score": 50,
            "dataset_split": "DEVELOPMENT",
            "historical_flow_available": False,
        },
        {
            "opened_at": start + timedelta(days=95),
            "base_net_r": -0.1,
            "expansion_score": 55,
            "dataset_split": "DEVELOPMENT",
            "historical_flow_available": False,
        },
    ]
    report = build_historical_flow_report(rows, start_at=start, development_end_at=dev_end)
    assert report["coverage"]["rows"] == 2
    assert report["coverage"]["historical_flow_rows"] == 0
    assert report["promotion_allowed"] is False
