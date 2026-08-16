from datetime import datetime, timedelta, timezone

from research_premium_microstructure_v3 import (
    build_premium_report,
    enrich_with_premium,
    normalize_premium_klines,
)


def test_premium_join_is_strictly_backward_looking_and_builds_signed_features():
    opened = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    ts = int(opened.timestamp())
    rows = []
    for i in range(30):
        point_ts = ts - (29 - i) * 3600
        rows.append([str(point_ts * 1000), "0", "0", "0", str((i - 15) / 10000)])
    rows.append([str((ts + 3600) * 1000), "0", "0", "0", "9.0"])
    points = normalize_premium_klines(rows)
    result = enrich_with_premium(
        {"opened_at": opened.isoformat(), "side": "long"},
        derivative_symbol="BTCUSDT",
        points=points,
    )
    assert result["premium_available"] is True
    assert result["premium_close"] == 0.0014
    assert result["signed_premium"] == 0.0014
    assert result["premium_age_seconds"] == 0
    assert result["premium_change_4h"] is not None
    assert result["premium_z_24h"] is not None
    assert result["premium_close"] != 9.0


def test_short_flips_premium_sign():
    opened = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    ts = int(opened.timestamp())
    points = normalize_premium_klines(
        [[str((ts - i * 3600) * 1000), "0", "0", "0", "0.001"] for i in range(24)]
    )
    result = enrich_with_premium(
        {"opened_at": opened, "side": "short"},
        derivative_symbol="BTCUSDT",
        points=points,
    )
    assert result["signed_premium"] == -0.001


def _event(ts, net_r, expansion, signed, z, delta, split="DEVELOPMENT"):
    return {
        "opened_at": ts,
        "base_net_r": net_r,
        "expansion_score": expansion,
        "dataset_split": split,
        "premium_available": True,
        "signed_premium": signed,
        "signed_premium_z_24h": z,
        "signed_premium_change_4h": delta,
    }


def test_report_selects_only_on_train_and_negative_holdout_blocks_promotion():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dev_end = start + timedelta(days=120)
    rows = []
    for i in range(800):
        ts = start + timedelta(minutes=i * 150)
        high = i % 4 == 0
        if high:
            rows.append(_event(ts, 0.7, 90, -0.001, -1.5, 0.0002))
        else:
            rows.append(_event(ts, -0.4, 35, 0.001, 0.2, -0.0001))

    holdout_start = start + timedelta(days=90)
    for i in range(240):
        ts = holdout_start + timedelta(minutes=i * 120)
        high = i % 3 == 0
        rows.append(
            _event(
                ts,
                -0.5 if high else -0.1,
                90 if high else 35,
                -0.001 if high else 0.001,
                -1.5 if high else 0.2,
                0.0002 if high else -0.0001,
            )
        )

    validation_start = dev_end
    for i in range(120):
        rows.append(
            _event(
                validation_start + timedelta(minutes=i * 120),
                1.0,
                90,
                -0.001,
                -1.5,
                0.0002,
                split="VALIDATION",
            )
        )

    report = build_premium_report(rows, start_at=start, development_end_at=dev_end)
    assert report["selected_on_train"] is not None
    assert report["internal_holdout_edge_pass"] is False
    assert report["promotion_allowed"] is False
    assert report["split_policy"]["historical_validation_status"] == "REUSED_REFERENCE_NOT_UNTOUCHED_OOS"
