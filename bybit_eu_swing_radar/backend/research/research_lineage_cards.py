"""Deterministic dataset/feature cards and capture lineage for research artifacts.

The lineage layer references existing immutable snapshot fingerprints and data
quality contracts. It does not create execution proof, infer provider
availability, or mutate live strategy/eligibility/scoring behavior.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from research.research_data_quality import CONTRACT_VERSION, source_contract

CARD_VERSION = "research-lineage-card-v1"
LINEAGE_VERSION = "research-capture-lineage-v1"


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _aware_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("lineage timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def dataset_card(source: str) -> dict[str, Any]:
    contract = source_contract(source)
    core = {
        "card_version": CARD_VERSION,
        "card_type": "DATASET",
        "dataset_id": contract["research_family"],
        "source": source,
        "research_family": contract["research_family"],
        "spec_version": contract["spec_version"],
        "data_quality_contract_version": CONTRACT_VERSION,
        "record_identity": ["research_family", "spec_version", "captured_at"],
        "raw_history_identity": ["research_family", "spec_version", "captured_at"],
        "freshness_budget_seconds": int(contract["max_age_seconds"]),
        "coverage_semantics": contract["coverage_semantics"],
        "time_semantics": {
            "capture_time_field": "captured_at",
            "provider_availability_inference": contract["provider_availability_inference"],
            "provider_availability_required_for_future_production_use": True,
        },
        "provenance_fields": [
            "captured_at",
            "source_commit_sha",
            "immutable_history.payload_fingerprint",
            "immutable_history.research_family",
            "immutable_history.spec_version",
            "immutable_history.point_in_time_verified",
            "provider_availability_verified",
        ],
        "research_only": True,
        "production_eligible": False,
        "execution_proof": False,
    }
    return {**core, "card_fingerprint": _fingerprint(core)}


def dataset_cards(sources: Sequence[str]) -> dict[str, dict[str, Any]]:
    return {source: dataset_card(source) for source in sources}


def feature_card(
    *,
    feature_id: str,
    spec_version: str,
    input_sources: Sequence[str],
    max_symbols: int | None = None,
) -> dict[str, Any]:
    cards = dataset_cards(input_sources)
    core = {
        "card_version": CARD_VERSION,
        "card_type": "FEATURE_SET",
        "feature_id": feature_id,
        "spec_version": spec_version,
        "input_datasets": [
            {
                "source": source,
                "dataset_id": cards[source]["dataset_id"],
                "dataset_card_fingerprint": cards[source]["card_fingerprint"],
                "spec_version": cards[source]["spec_version"],
            }
            for source in input_sources
        ],
        "output_grain": "one research context snapshot with per-USDC-symbol rows plus global context",
        "max_symbols": max_symbols,
        "join_time_rule": "input captured_at must be at or before output captured_at",
        "microstructure_join_policy": "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED",
        "transformation_semantics": [
            "compact descriptive joins only",
            "missing/stale source state remains explicit",
            "no imputation of missing evidence to zero or neutral",
            "no cross-layer composite score",
            "no eligibility, execution, trigger, entry, stop or target mutation",
        ],
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "production_eligible": False,
        "promotion_allowed": False,
        "execution_proof": False,
    }
    return {**core, "card_fingerprint": _fingerprint(core)}


def cards_manifest(
    *,
    feature_id: str,
    spec_version: str,
    input_sources: Sequence[str],
    max_symbols: int | None = None,
) -> dict[str, Any]:
    datasets = dataset_cards(input_sources)
    feature = feature_card(
        feature_id=feature_id,
        spec_version=spec_version,
        input_sources=input_sources,
        max_symbols=max_symbols,
    )
    core = {
        "card_version": CARD_VERSION,
        "datasets": datasets,
        "feature": feature,
        "research_only": True,
        "production_eligible": False,
    }
    return {**core, "manifest_fingerprint": _fingerprint(core)}


def _input_reference(
    source: str,
    record: Mapping[str, Any] | None,
    quality: Mapping[str, Any] | None,
) -> dict[str, Any]:
    card = dataset_card(source)
    payload = (record or {}).get("payload") if record else None
    payload = payload if isinstance(payload, Mapping) else {}
    history = payload.get("immutable_history")
    history = history if isinstance(history, Mapping) else {}
    quality = quality or {}
    lineage_quality = quality.get("lineage")
    lineage_quality = lineage_quality if isinstance(lineage_quality, Mapping) else {}

    return {
        "source": source,
        "dataset_id": card["dataset_id"],
        "dataset_card_fingerprint": card["card_fingerprint"],
        "spec_version": card["spec_version"],
        "captured_at": (record or {}).get("captured_at") if record else None,
        "source_commit_sha": (record or {}).get("source_commit_sha") if record else None,
        "snapshot_payload_fingerprint": history.get("payload_fingerprint"),
        "immutable_history_present": bool(history),
        "immutable": history.get("immutable") is True,
        "immutable_history_family": history.get("research_family"),
        "immutable_history_spec_version": history.get("spec_version"),
        "point_in_time_verified": lineage_quality.get("point_in_time_verified") is True,
        "provider_availability_verified": lineage_quality.get("provider_availability_verified") is True,
        "availability_semantics": lineage_quality.get("availability_semantics"),
        "data_quality_status": quality.get("status"),
        "data_quality_severity": quality.get("severity"),
        "research_usable": quality.get("research_usable") is True,
    }


def build_capture_lineage(
    records: Mapping[str, Mapping[str, Any] | None],
    quality_by_source: Mapping[str, Mapping[str, Any]],
    *,
    feature_id: str,
    feature_spec_version: str,
    input_sources: Sequence[str],
    captured_at: datetime,
    source_commit_sha: str | None,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    output_time = _aware_iso(captured_at)
    feature = feature_card(
        feature_id=feature_id,
        spec_version=feature_spec_version,
        input_sources=input_sources,
        max_symbols=max_symbols,
    )
    inputs = [
        _input_reference(source, records.get(source), quality_by_source.get(source))
        for source in input_sources
    ]

    references_complete = all(
        row["captured_at"] is not None
        and row["source_commit_sha"] is not None
        and row["snapshot_payload_fingerprint"] is not None
        and row["immutable"] is True
        for row in inputs
    )
    all_research_usable = all(row["research_usable"] for row in inputs)
    provider_availability_complete = all(
        row["provider_availability_verified"] for row in inputs
    )
    pit_complete = all(row["point_in_time_verified"] for row in inputs)

    core = {
        "lineage_version": LINEAGE_VERSION,
        "feature_id": feature_id,
        "feature_spec_version": feature_spec_version,
        "feature_card_fingerprint": feature["card_fingerprint"],
        "output": {
            "captured_at": output_time,
            "source_commit_sha": source_commit_sha,
            "research_only": True,
            "production_eligible": False,
            "execution_proof": False,
        },
        "inputs": inputs,
        "input_count": len(inputs),
        "immutable_input_fingerprint_count": sum(
            1 for row in inputs if row["snapshot_payload_fingerprint"] is not None
        ),
        "references_complete": references_complete,
        "all_inputs_research_usable": all_research_usable,
        "point_in_time_verified_for_all_inputs": pit_complete,
        "provider_availability_verified_for_all_inputs": provider_availability_complete,
        "provider_availability_inferred_from_capture_time": False,
        "production_usable": False,
        "production_block_reason": (
            "research lineage is descriptive provenance only; all required provider availability/PIT "
            "evidence must be independently verified before any future production promotion"
        ),
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
    }
    return {**core, "lineage_fingerprint": _fingerprint(core)}


def copy_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a defensive copy for API/spec callers."""
    return deepcopy(dict(payload))
