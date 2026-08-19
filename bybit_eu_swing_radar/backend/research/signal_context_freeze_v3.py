"""Prospective Signal-Time Context Freeze v3 using immutable raw history.

V1/V2 cohorts remain immutable. V3 admits only signals opened after the first
append-only Cross-Layer v2 raw-history capture and never reconstructs pre-v3
signals from later materialized state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from research.signal_context_freeze_v2 import (
    CROSS_LAYER_MAX_AGE_SECONDS,
    CROSS_LAYER_SPEC_VERSION,
    MICROSTRUCTURE_ALIGNMENT_SPEC_VERSION,
    STRATEGY_VERSION,
    build_freeze_payload as _build_v2_payload,
    cross_layer_context as _cross_layer_v2,
)

SPEC_VERSION = "signal-context-freeze-v3"
HISTORY_FAMILY = "cross-layer-context-v2"
HISTORY_SOURCE = "immutable_raw_history_v1"


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
        "cross_layer_storage_source": HISTORY_SOURCE,
        "cross_layer_history_family": HISTORY_FAMILY,
        "immutable_history_required": True,
        "prospective_start_rule": "only signals opened_at >= first immutable Cross-Layer v2 raw-history captured_at",
        "historical_backfill_allowed": False,
        "v1_preserved": True,
        "v2_preserved": True,
        "temporal_contract": {
            "cross_layer_rule": "latest immutable Cross-Layer v2 raw-history capture with captured_at <= signal opened_at",
            "cross_layer_max_age_seconds": CROSS_LAYER_MAX_AGE_SECONDS,
            "source_capture_time_safe": True,
            "provider_availability_verified": False,
            "provider_availability_note": "raw-history capture time prevents future-capture leakage but does not by itself prove every upstream provider field was available at source time",
            "microstructure_rule": "existing preregistered alignment features use bucket_start < signal opened_at only",
            "freeze_may_run_after_signal": True,
            "source_payloads_are_copied_immutably_into_freeze_rows": True,
        },
        "principles": [
            "no pre-v3 signal is admitted into the v3 cohort",
            "v1 and v2 freeze rows remain immutable",
            "no outcome/status/net-R/exit fields are selected from the journal",
            "future cross-layer captures are structurally excluded by history lookup",
            "provider availability is not overstated",
            "microstructure remains optional and strictly pre-signal",
            "no composite score or trade recommendation is emitted",
        ],
    }


def cross_layer_context(record: Mapping[str, Any] | None, *, opened_at: datetime, symbol: str) -> dict[str, Any]:
    result = _cross_layer_v2(record, opened_at=opened_at, symbol=symbol)
    result["history_source"] = HISTORY_SOURCE
    result["history_family"] = HISTORY_FAMILY
    result["history_payload_fingerprint"] = record.get("payload_fingerprint") if record else None
    result["history_immutable"] = bool(record)
    result["source_capture_time_safe"] = bool(record)
    result["provider_availability_verified"] = False
    return result


def build_freeze_payload(
    signal: Mapping[str, Any],
    *,
    cross_layer_record: Mapping[str, Any] | None,
    microstructure_feature: Mapping[str, Any] | None,
    recorder_symbols: Iterable[str],
    frozen_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    payload = _build_v2_payload(
        signal,
        cross_layer_record=cross_layer_record,
        microstructure_feature=microstructure_feature,
        recorder_symbols=recorder_symbols,
        frozen_at=frozen_at,
        source_commit_sha=source_commit_sha,
    )
    payload["spec_version"] = SPEC_VERSION
    payload["cross_layer_context"] = cross_layer_context(
        cross_layer_record,
        opened_at=datetime.fromisoformat(str(payload["opened_at"]).replace("Z", "+00:00")),
        symbol=str(payload["symbol"]),
    )
    payload["cross_layer_storage_source"] = HISTORY_SOURCE
    return payload
