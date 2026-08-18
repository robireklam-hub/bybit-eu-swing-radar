import asyncio

from app.research_sweep_effect_api import build_status_from_loaders


def _counts(total: int) -> dict:
    return {
        "closed_signal_count": total,
        "long_count": total // 2,
        "short_count": total - total // 2,
        "distinct_utc_days": 12 if total >= 60 else 5,
        "attribute_complete_count": total,
        "symbol_count": 8,
        "strict_count": 2,
        "shadow_count": max(0, total - 2),
        "first_opened_at": "2026-07-01T00:00:00+00:00",
        "last_opened_at": "2026-07-20T00:00:00+00:00",
    }


def _outcomes() -> list[dict]:
    rows = []
    for index in range(64):
        side = "long" if index % 2 == 0 else "short"
        aligned = index % 4 < 2
        rows.append(
            {
                "opened_at": f"2026-07-{index // 4 + 1:02d}T12:00:00+00:00",
                "side": side,
                "net_r": 0.1 + index * 0.01,
                "mfe_r": 0.5 + index * 0.01,
                "mae_r": 0.3,
                "sweep_depth_atr": 0.1 + index * 0.005,
                "bars_from_sweep_to_confirmation": 6 - (index % 6),
                "volume_ratio_5m": 1.3 + index * 0.01,
                "structure_15m_state": (
                    "BULLISH_SHIFT" if aligned and side == "long"
                    else "BEARISH_SHIFT" if aligned
                    else "NEUTRAL_NON_OPPOSING"
                ),
            }
        )
    return rows


def test_below_gate_never_loads_outcomes() -> None:
    called = False

    async def count_loader():
        return _counts(20)

    async def outcome_loader():
        nonlocal called
        called = True
        raise AssertionError("outcome loader must not run below label-blind gate")

    payload = asyncio.run(
        build_status_from_loaders(
            count_loader,
            outcome_loader,
            source_commit_sha="abc",
        )
    )
    assert payload["status"] == "WAITING_FOR_FORWARD_SAMPLE"
    assert payload["outcomes_loaded"] is False
    assert payload["effects"] is None
    assert called is False
    assert payload["source_commit_sha"] == "abc"
    assert payload["promotion_allowed"] is False


def test_ready_gate_loads_outcomes_but_never_promotes() -> None:
    called = False

    async def count_loader():
        return _counts(64)

    async def outcome_loader():
        nonlocal called
        called = True
        return _outcomes()

    payload = asyncio.run(
        build_status_from_loaders(
            count_loader,
            outcome_loader,
            source_commit_sha="def",
        )
    )
    assert called is True
    assert payload["status"] in {"COMPLETE", "WAITING_FOR_HYPOTHESIS_COVERAGE"}
    assert payload["outcomes_loaded"] is True
    assert payload["effects"]["outcome_sample_size"] == 64
    assert payload["promotion_allowed"] is False
    assert payload["effects"]["promotion_allowed"] is False
