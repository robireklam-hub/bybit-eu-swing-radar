"""Prospective label-blind event detector for controlled-pullback research v2.

This module operationalizes the already-preregistered MOMENTUM ->
CONTROLLED_PULLBACK -> ORDER_FLOW_REACCELERATION sequence before experiment
activation and before any outcome inspection. The operational windows below are
frozen by this detector spec and must not be tuned on forward outcomes.

Research-only: no live strategy/scoring/ranking/eligibility/execution path.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Mapping

from research.microstructure.controlled_pullback_calibration_v2 import CALIBRATION_ID
from research.microstructure.controlled_pullback_v2 import (
    EXPERIMENT_ID,
    STRATEGY_VERSION,
    SYMBOLS,
)

DETECTOR_ID = "microstructure-controlled-pullback-detector-v2"
DETECTOR_SPEC_VERSION = "2.0.0"
BUCKET_SECONDS = 5
MOMENTUM_LOOKBACK_SECONDS = 60
PRE_IMPULSE_BASELINE_SECONDS = 60
PULLBACK_SEARCH_SECONDS = 60
REACCELERATION_SEARCH_SECONDS = 30
EVENT_COOLDOWN_SECONDS = 60

_FORBIDDEN_OUTCOME_FIELDS = {
    "net_r",
    "gross_r",
    "exit_reason",
    "closed_at",
    "direction_normalized_return_5m",
    "direction_normalized_return_15m",
    "mae_15m",
    "mfe_15m",
    "future_return",
    "forward_return",
    "pnl",
}


def detector_contract() -> dict[str, Any]:
    return {
        "detector_id": DETECTOR_ID,
        "detector_spec_version": DETECTOR_SPEC_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "strategy_version": STRATEGY_VERSION,
        "calibration_id": CALIBRATION_ID,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "post_trigger_data_used_for_trigger": False,
        "live_strategy_mutation": False,
        "promotion_allowed": False,
        "bucket_seconds": BUCKET_SECONDS,
        "momentum_lookback_seconds": MOMENTUM_LOOKBACK_SECONDS,
        "pre_impulse_baseline_seconds": PRE_IMPULSE_BASELINE_SECONDS,
        "pullback_search_seconds": PULLBACK_SEARCH_SECONDS,
        "reacceleration_search_seconds": REACCELERATION_SEARCH_SECONDS,
        "event_cooldown_seconds": EVENT_COOLDOWN_SECONDS,
        "pullback_selection": "FIRST_VALID_CLOSED_BUCKET",
        "opposite_structure_break_definition": (
            "Any closed pullback-path mid crossing the momentum-origin mid in the "
            "opposite direction invalidates that momentum sequence."
        ),
        "trigger_timestamp_definition": "close_of_reacceleration_bucket",
        "threshold_search_allowed": False,
    }


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("bucket_start must be an ISO-8601 string or datetime")
    if result.tzinfo is None:
        raise ValueError("bucket_start must be timezone-aware")
    return result.astimezone(timezone.utc)


def _finite(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _normalize_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    allowed = set(SYMBOLS)
    for raw in rows:
        forbidden = _FORBIDDEN_OUTCOME_FIELDS.intersection(raw)
        if forbidden:
            raise ValueError(
                "detector input contains forbidden outcome fields: "
                + ", ".join(sorted(forbidden))
            )
        symbol = str(raw.get("symbol") or "").upper()
        if symbol not in allowed:
            raise ValueError(f"unexpected detector symbol: {symbol or '<missing>'}")
        bucket_seconds = int(raw.get("bucket_seconds") or BUCKET_SECONDS)
        if bucket_seconds != BUCKET_SECONDS:
            raise ValueError(
                f"unexpected detector bucket_seconds for {symbol}: {bucket_seconds}"
            )
        at = _utc(raw.get("bucket_start"))
        mid = _finite(raw.get("mid"), "mid")
        if mid <= 0:
            raise ValueError("mid must be positive")
        output.append(
            {
                "symbol": symbol,
                "bucket_start": at,
                "bucket_seconds": bucket_seconds,
                "mid": mid,
                "spread_bps": raw.get("spread_bps"),
                "bid_depth_5_quote": raw.get("bid_depth_5_quote"),
                "ask_depth_5_quote": raw.get("ask_depth_5_quote"),
                "signed_quote_flow": raw.get("signed_quote_flow"),
                "total_quote_volume": raw.get("total_quote_volume"),
                "bid_added_quote": raw.get("bid_added_quote"),
                "bid_removed_quote": raw.get("bid_removed_quote"),
                "ask_added_quote": raw.get("ask_added_quote"),
                "ask_removed_quote": raw.get("ask_removed_quote"),
                "book_ready": raw.get("book_ready"),
            }
        )
    output.sort(key=lambda row: (row["symbol"], row["bucket_start"]))
    return output


def _flow_share(row: Mapping[str, Any]) -> float | None:
    volume = _finite(row.get("total_quote_volume", 0.0) or 0.0, "total_quote_volume")
    if volume <= 0:
        return None
    signed = _finite(row.get("signed_quote_flow", 0.0) or 0.0, "signed_quote_flow")
    return max(-1.0, min(1.0, signed / volume))


def _book_pressure(row: Mapping[str, Any]) -> float | None:
    bid_added = _finite(row.get("bid_added_quote", 0.0) or 0.0, "bid_added_quote")
    bid_removed = _finite(row.get("bid_removed_quote", 0.0) or 0.0, "bid_removed_quote")
    ask_added = _finite(row.get("ask_added_quote", 0.0) or 0.0, "ask_added_quote")
    ask_removed = _finite(row.get("ask_removed_quote", 0.0) or 0.0, "ask_removed_quote")
    churn = bid_added + bid_removed + ask_added + ask_removed
    if churn <= 0:
        return None
    return (bid_added + ask_removed - bid_removed - ask_added) / churn


def _top5_depth(row: Mapping[str, Any]) -> float | None:
    bid = _finite(row.get("bid_depth_5_quote", 0.0) or 0.0, "bid_depth_5_quote")
    ask = _finite(row.get("ask_depth_5_quote", 0.0) or 0.0, "ask_depth_5_quote")
    total = bid + ask
    return total if total > 0 else None


def _spread(row: Mapping[str, Any]) -> float | None:
    value = row.get("spread_bps")
    if value is None:
        return None
    spread = _finite(value, "spread_bps")
    return spread if spread >= 0 else None


def _exact_window(
    by_time: Mapping[datetime, Mapping[str, Any]],
    end_exclusive: datetime,
    seconds: int,
) -> list[Mapping[str, Any]] | None:
    count = seconds // BUCKET_SECONDS
    start = end_exclusive - timedelta(seconds=seconds)
    expected = [start + timedelta(seconds=BUCKET_SECONDS * i) for i in range(count)]
    rows = [by_time.get(at) for at in expected]
    if any(row is None for row in rows):
        return None
    return [row for row in rows if row is not None]


def _validate_calibration_snapshot(snapshot: Mapping[str, Any]) -> tuple[datetime, Mapping[str, Any], Mapping[str, Any]]:
    if snapshot.get("calibration_id") != CALIBRATION_ID:
        raise ValueError("unexpected controlled-pullback v2 calibration_id")
    if snapshot.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("unexpected controlled-pullback v2 experiment_id")
    if snapshot.get("strategy_version") != STRATEGY_VERSION:
        raise ValueError("unexpected controlled-pullback v2 strategy_version")
    if snapshot.get("research_only") is not True or snapshot.get("label_blind") is not True:
        raise ValueError("calibration snapshot is not label-blind research")
    if snapshot.get("outcome_visible") is not False or snapshot.get("promotion_allowed") is not False:
        raise ValueError("calibration snapshot opened outcome/promotion gate")
    if snapshot.get("threshold_recalibration_allowed") is not False:
        raise ValueError("calibration snapshot allows recalibration")
    forward_start = _utc(snapshot.get("forward_start_utc"))
    thresholds = snapshot.get("thresholds_by_symbol")
    structural = snapshot.get("structural_thresholds")
    if not isinstance(thresholds, Mapping) or not isinstance(structural, Mapping):
        raise ValueError("calibration snapshot threshold contract is incomplete")
    return forward_start, thresholds, structural


def _momentum_candidates_for_symbol(
    rows: list[Mapping[str, Any]],
    symbol_thresholds: Mapping[str, Any],
    forward_start: datetime,
) -> list[dict[str, Any]]:
    by_time = {row["bucket_start"]: row for row in rows}
    output: list[dict[str, Any]] = []
    last_trigger: datetime | None = None
    price_min = _finite(
        symbol_thresholds.get("momentum_mid_return_60s_abs_min"),
        "momentum_mid_return_60s_abs_min",
    )
    flow_min = _finite(
        symbol_thresholds.get("momentum_aggressive_flow_share_abs_min"),
        "momentum_aggressive_flow_share_abs_min",
    )
    for row in rows:
        impulse_end = row["bucket_start"]
        trigger_at = impulse_end + timedelta(seconds=BUCKET_SECONDS)
        if trigger_at < forward_start:
            continue
        predecessor = by_time.get(impulse_end - timedelta(seconds=MOMENTUM_LOOKBACK_SECONDS))
        if predecessor is None:
            continue
        baseline = _exact_window(
            by_time,
            impulse_end - timedelta(seconds=MOMENTUM_LOOKBACK_SECONDS),
            PRE_IMPULSE_BASELINE_SECONDS,
        )
        if baseline is None:
            continue
        displacement = row["mid"] / predecessor["mid"] - 1.0
        if abs(displacement) < price_min or displacement == 0:
            continue
        direction = "long" if displacement > 0 else "short"
        direction_sign = 1.0 if direction == "long" else -1.0
        flow_share = _flow_share(row)
        if flow_share is None or direction_sign * flow_share < flow_min:
            continue
        baseline_spreads = [_spread(item) for item in baseline]
        baseline_depths = [_top5_depth(item) for item in baseline]
        if any(value is None for value in baseline_spreads + baseline_depths):
            continue
        if row.get("book_ready") is not True:
            continue
        if last_trigger is not None and (trigger_at - last_trigger).total_seconds() < EVENT_COOLDOWN_SECONDS:
            continue
        last_trigger = trigger_at
        output.append(
            {
                "symbol": row["symbol"],
                "direction": direction,
                "momentum_start_at": predecessor["bucket_start"],
                "momentum_end_at": impulse_end,
                "momentum_trigger_at": trigger_at,
                "momentum_origin_mid": predecessor["mid"],
                "momentum_end_mid": row["mid"],
                "momentum_return_60s": displacement,
                "momentum_flow_share": flow_share,
                "baseline_spread_bps": median(value for value in baseline_spreads if value is not None),
                "baseline_top5_depth_quote": median(value for value in baseline_depths if value is not None),
            }
        )
    return output


def detect_research_events(
    raw_rows: Iterable[Mapping[str, Any]],
    calibration_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Detect prospective research events without reading any post-trigger outcome."""
    forward_start, thresholds_by_symbol, structural = _validate_calibration_snapshot(calibration_snapshot)
    rows = _normalize_rows(raw_rows)
    by_symbol: dict[str, list[Mapping[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    for row in rows:
        by_symbol[row["symbol"]].append(row)

    retracement_min = _finite(
        structural.get("pullback_retracement_fraction_min"),
        "pullback_retracement_fraction_min",
    )
    retracement_max = _finite(
        structural.get("pullback_retracement_fraction_max"),
        "pullback_retracement_fraction_max",
    )
    spread_ratio_max = _finite(
        structural.get("spread_ratio_to_pre_impulse_max"),
        "spread_ratio_to_pre_impulse_max",
    )
    depth_ratio_min = _finite(
        structural.get("top5_depth_ratio_to_pre_impulse_min"),
        "top5_depth_ratio_to_pre_impulse_min",
    )
    if not (0 < retracement_min < retracement_max < 1):
        raise ValueError("invalid frozen pullback retracement bounds")
    if structural.get("opposite_structure_break_allowed") is not False:
        raise ValueError("opposite structure break gate must remain closed")

    momentum_candidates: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    last_event_trigger: dict[str, datetime] = {}

    for symbol in SYMBOLS:
        symbol_rows = by_symbol[symbol]
        symbol_thresholds = thresholds_by_symbol.get(symbol)
        if not isinstance(symbol_thresholds, Mapping):
            raise ValueError(f"missing calibrated thresholds for {symbol}")
        candidates = _momentum_candidates_for_symbol(
            symbol_rows,
            symbol_thresholds,
            forward_start,
        )
        momentum_candidates.extend(candidates)
        by_time = {row["bucket_start"]: row for row in symbol_rows}
        reaccel_flow_min = _finite(
            symbol_thresholds.get("reacceleration_aggressive_flow_share_abs_min"),
            "reacceleration_aggressive_flow_share_abs_min",
        )
        reaccel_pressure_min = _finite(
            symbol_thresholds.get("reacceleration_book_pressure_abs_min"),
            "reacceleration_book_pressure_abs_min",
        )

        for candidate in candidates:
            prior_event = last_event_trigger.get(symbol)
            if prior_event is not None and (
                candidate["momentum_trigger_at"] - prior_event
            ).total_seconds() < EVENT_COOLDOWN_SECONDS:
                continue
            sign = 1.0 if candidate["direction"] == "long" else -1.0
            origin = candidate["momentum_origin_mid"]
            end_mid = candidate["momentum_end_mid"]
            leg = abs(end_mid - origin)
            if leg <= 0:
                continue

            pullback_row = None
            pullback_fraction = None
            pullback_path: list[Mapping[str, Any]] = []
            for step in range(1, PULLBACK_SEARCH_SECONDS // BUCKET_SECONDS + 1):
                at = candidate["momentum_end_at"] + timedelta(seconds=step * BUCKET_SECONDS)
                row = by_time.get(at)
                if row is None:
                    break
                pullback_path.append(row)
                if sign * (row["mid"] - origin) <= 0:
                    pullback_row = None
                    break
                retracement = sign * (end_mid - row["mid"]) / leg
                if retracement > retracement_max:
                    pullback_row = None
                    break
                if retracement < retracement_min:
                    continue
                spread = _spread(row)
                depth = _top5_depth(row)
                if spread is None or depth is None or row.get("book_ready") is not True:
                    continue
                if spread > candidate["baseline_spread_bps"] * spread_ratio_max:
                    continue
                if depth < candidate["baseline_top5_depth_quote"] * depth_ratio_min:
                    continue
                pullback_row = row
                pullback_fraction = retracement
                break
            if pullback_row is None or pullback_fraction is None:
                continue

            reaccel_row = None
            for step in range(1, REACCELERATION_SEARCH_SECONDS // BUCKET_SECONDS + 1):
                at = pullback_row["bucket_start"] + timedelta(seconds=step * BUCKET_SECONDS)
                row = by_time.get(at)
                if row is None:
                    break
                if sign * (row["mid"] - origin) <= 0:
                    break
                retracement = sign * (end_mid - row["mid"]) / leg
                if retracement > retracement_max:
                    break
                flow_share = _flow_share(row)
                pressure = _book_pressure(row)
                if flow_share is None or pressure is None:
                    continue
                if sign * flow_share < reaccel_flow_min:
                    continue
                if sign * pressure < reaccel_pressure_min:
                    continue
                reaccel_row = row
                break
            if reaccel_row is None:
                continue

            trigger_at = reaccel_row["bucket_start"] + timedelta(seconds=BUCKET_SECONDS)
            if trigger_at < forward_start:
                continue
            prior_event = last_event_trigger.get(symbol)
            if prior_event is not None and (trigger_at - prior_event).total_seconds() < EVENT_COOLDOWN_SECONDS:
                continue
            last_event_trigger[symbol] = trigger_at
            events.append(
                {
                    "event_key": (
                        f"{EXPERIMENT_ID}:{symbol}:{candidate['direction']}:"
                        f"{int(trigger_at.timestamp())}"
                    ),
                    "experiment_id": EXPERIMENT_ID,
                    "strategy_version": STRATEGY_VERSION,
                    "detector_id": DETECTOR_ID,
                    "symbol": symbol,
                    "direction": candidate["direction"],
                    "momentum_start_at": candidate["momentum_start_at"].isoformat(),
                    "momentum_end_at": candidate["momentum_end_at"].isoformat(),
                    "pullback_at": pullback_row["bucket_start"].isoformat(),
                    "trigger_at": trigger_at.isoformat(),
                    "trigger_bucket_start": reaccel_row["bucket_start"].isoformat(),
                    "momentum_return_60s": candidate["momentum_return_60s"],
                    "momentum_flow_share": candidate["momentum_flow_share"],
                    "pullback_retracement_fraction": pullback_fraction,
                    "reacceleration_flow_share": _flow_share(reaccel_row),
                    "reacceleration_book_pressure": _book_pressure(reaccel_row),
                    "baseline_spread_bps": candidate["baseline_spread_bps"],
                    "baseline_top5_depth_quote": candidate["baseline_top5_depth_quote"],
                    "pullback_spread_bps": _spread(pullback_row),
                    "pullback_top5_depth_quote": _top5_depth(pullback_row),
                    "research_only": True,
                    "label_blind": True,
                    "outcome_visible": False,
                    "promotion_allowed": False,
                    "live_strategy_mutation": False,
                }
            )

    momentum_output = []
    for item in momentum_candidates:
        momentum_output.append(
            {
                **{
                    key: (value.isoformat() if isinstance(value, datetime) else value)
                    for key, value in item.items()
                },
                "experiment_id": EXPERIMENT_ID,
                "strategy_version": STRATEGY_VERSION,
                "comparator_class": "MOMENTUM_ONLY_SAME_DIRECTION_SAME_SYMBOL",
                "research_only": True,
                "label_blind": True,
                "outcome_visible": False,
            }
        )
    return {
        "detector": detector_contract(),
        "forward_start_utc": forward_start.isoformat(),
        "momentum_candidates": momentum_output,
        "controlled_pullback_events": events,
        "outcome_visible": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
    }
