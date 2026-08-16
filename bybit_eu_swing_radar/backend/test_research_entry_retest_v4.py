from datetime import datetime, timedelta, timezone

from research_entry_retest_v4 import build_entry_retest_report, replay_entry_variant
from worker import Bar


def _bar(start_ms: int, o: float, h: float, l: float, c: float) -> Bar:
    return Bar(start_ms=start_ms, open=o, high=h, low=l, close=c, volume=1.0, turnover=1000.0)


def test_retest_is_placed_after_confirmation_and_replays_fill():
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t0 = int(opened.timestamp() * 1000)
    bars = [
        _bar(t0, 110, 111, 104, 106),
        _bar(t0 + 300_000, 106, 126, 104, 124),
        _bar(t0 + 600_000, 124, 125, 120, 122),
    ]
    row = {
        "opened_at": opened,
        "side": "long",
        "entry_price": 110.0,
        "stop_price": 95.0,
        "candidate_payload": {
            "sweep_event": {
                "structure_shift_level_5m": 105.0,
                "sweep_level": 100.0,
            }
        },
    }
    result = replay_entry_variant(
        row, bars, [b.start_ms for b in bars], variant="structure_break_retest"
    )
    assert result is not None
    assert result["filled"] is True
    assert result["entry"] == 105.0
    assert result["fill_delay_bars"] == 0
    assert result["net_r"] > 0


def test_report_selects_train_winner_and_requires_positive_holdout():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    development_end = start + timedelta(days=120)
    rows = []
    # 180 train opportunities with structure retest clearly best and PF > 1.
    for i in range(180):
        rows.append(
            {
                "opened_at": start + timedelta(hours=i * 10),
                "dataset_split": "DEVELOPMENT",
                "base_net_r": -0.4,
                "entry_retests": {
                    "structure_break_retest": {"filled": True, "net_r": -0.20 if i % 3 == 0 else 0.50},
                    "half_retrace_to_break": {"filled": True, "net_r": -0.10},
                    "sweep_level_retest": {"filled": True, "net_r": -0.20},
                },
            }
        )
    # 60 holdout opportunities preserve positive structure-retest edge and PF > 1.
    for i in range(60):
        rows.append(
            {
                "opened_at": start + timedelta(days=95, hours=i * 8),
                "dataset_split": "DEVELOPMENT",
                "base_net_r": -0.3,
                "entry_retests": {
                    "structure_break_retest": {"filled": True, "net_r": -0.20 if i % 3 == 0 else 0.40},
                    "half_retrace_to_break": {"filled": True, "net_r": -0.10},
                    "sweep_level_retest": {"filled": True, "net_r": -0.20},
                },
            }
        )
    report = build_entry_retest_report(rows, start_at=start, development_end_at=development_end)
    assert report["selected_on_train"] == "structure_break_retest"
    assert report["internal_holdout_result"]["n"] >= 50
    assert report["internal_holdout_edge_pass"] is True
    assert report["entry_architecture_edge_pass"] is True
    assert report["promotion_allowed"] is False
