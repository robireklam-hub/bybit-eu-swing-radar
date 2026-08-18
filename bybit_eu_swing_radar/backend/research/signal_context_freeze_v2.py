"""Prospective-only Signal-Time Context Freeze v2.

V2 starts only after the first Cross-Layer Context v2 snapshot exists. Existing
v1 freezes remain immutable and no pre-v2 signal is backfilled with later data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from research.signal_context_freeze import microstructure_context, sample_gate

SPEC_VERSION = "signal-context-freeze-v2"
STRATEGY_VERSION = "0.7.3"
CROSS_LAYER_SPEC_VERSION = "cross-layer-context-shadow-v2"
MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION = "microstructure-forward-alignment-v1"
CROSS_LAYER_MAX_AGE_SECONDS = 2 * 3600


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "source_layers": {
            "cross_layer_context": CROSS_LAYER_SPEC_VERSION,
            "microstructure_alignment": MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
        },
        "prospective_start_rule": "only signals opened_at >= first persisted Cross-Layer v2 captured_at",
        "historical_backfill_allowed": False,
        "v1_preserved": True,
        "temporal_contract": {
            "cross_layer_rule": "latest persisted Cross-Layer v2 snapshot with captured_at <= signal opened_at",
            "cross_layer_max_age_seconds": CROSS_LAYER_MAX_AGE_SECONDS,
            "microstructure_rule": "existing preregistered alignment features use bucket_start < signal opened_at only",
            "freeze_may_run_after_signal": True,
            "source_payloads_are_copied_immutably_into_freeze_rows": True,
        },
        "principles": [
            "no pre-v2 signal is admitted into the v2 cohort",
            "no outcome/status/net-R/exit fields are selected from the journal",
            "future cross-layer snapshots are rejected rather than treated as neutral",
            "stale or missing context remains explicit",
            "microstructure remains optional and strictly pre-signal",
            "no composite score or trade recommendation is emitted",
        ],
    }


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _symbol_context(payload: Mapping[str, Any], symbol: str) -> dict[str, Any] | None:
    raw = payload.get("symbols")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, Mapping) and str(item.get("symbol") or "").upper() == symbol:
                return dict(item)
    elif isinstance(raw, Mapping):
        item = raw.get(symbol)
        if isinstance(item, Mapping):
            return dict(item)
    return None


def cross_layer_context(record: Mapping[str, Any] | None, *, opened_at: datetime, symbol: str) -> dict[str, Any]:
    if not record:
        return {"status": "MISSING", "captured_at": None, "age_seconds": None, "source_commit_sha": None, "data_quality": None, "layer_status": {}, "symbol_context": None, "global_context": None}
    captured_at = _dt(record.get("captured_at"))
    if captured_at is None:
        return {"status": "INVALID_TIMESTAMP", "captured_at": record.get("captured_at"), "age_seconds": None, "source_commit_sha": record.get("source_commit_sha"), "data_quality": None, "layer_status": {}, "symbol_context": None, "global_context": None}
    age = (opened_at - captured_at).total_seconds()
    if age < -1e-6:
        return {"status": "FUTURE_REJECTED", "captured_at": captured_at.isoformat(), "age_seconds": round(age, 3), "source_commit_sha": record.get("source_commit_sha"), "data_quality": None, "layer_status": {}, "symbol_context": None, "global_context": None}
    status = "FRESH" if age <= CROSS_LAYER_MAX_AGE_SECONDS else "STALE"
    payload = record.get("payload")
    payload = dict(payload) if isinstance(payload, Mapping) else {}
    return {
        "status": status,
        "captured_at": captured_at.isoformat(),
        "age_seconds": round(age, 3),
        "source_commit_sha": record.get("source_commit_sha"),
        "data_quality": payload.get("data_quality"),
        "layer_status": dict(payload.get("layers") or {}),
        "symbol_context": _symbol_context(payload, symbol),
        "global_context": dict(payload.get("global_context") or {}),
    }


def build_freeze_payload(
    signal: Mapping[str, Any],
    *,
    cross_layer_record: Mapping[str, Any] | None,
    microstructure_feature: Mapping[str, Any] | None,
    recorder_symbols: Iterable[str],
    frozen_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    opened_at = _dt(signal.get("opened_at"))
    if opened_at is None:
        raise ValueError("signal opened_at must be timezone-aware/parseable")
    symbol = str(signal.get("symbol") or "").upper()
    if not symbol.endswith("USDC"):
        raise ValueError("signal context freeze v2 is USDC-only")
    side = str(signal.get("side") or "")
    if side not in {"long", "short"}:
        raise ValueError("signal side must be long or short")
    now = (frozen_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cross = cross_layer_context(cross_layer_record, opened_at=opened_at, symbol=symbol)
    micro = microstructure_context(
        microstructure_feature,
        opened_at=opened_at,
        symbol=symbol,
        recorder_symbols=recorder_symbols,
    )
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": str(signal.get("strategy_version") or STRATEGY_VERSION),
        "signal_id": int(signal.get("id") or signal.get("signal_id") or 0),
        "signal_key": str(signal.get("signal_key") or ""),
        "signal_class": str(signal.get("signal_class") or ""),
        "symbol": symbol,
        "side": side,
        "setup_type": str(signal.get("setup_type") or ""),
        "opened_at": opened_at.isoformat(),
        "frozen_at": now.isoformat(),
        "freeze_delay_seconds": round(max(0.0, (now - opened_at).total_seconds()), 3),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "cross_layer_context": cross,
        "microstructure": micro,
        "composite_score_emitted": False,
    }
