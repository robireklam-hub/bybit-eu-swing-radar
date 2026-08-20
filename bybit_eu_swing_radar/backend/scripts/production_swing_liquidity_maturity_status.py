from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

# This verifier is invoked directly as `python scripts/...py` in production
# workflows. In that mode Python puts the scripts directory, not the backend
# root, on sys.path. Bootstrap the backend root before importing sibling
# research contracts so standalone execution matches module-import execution.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from research.swing_liquidity_event_contract import maturity_at


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}_missing")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _event_identity(event: dict[str, Any], index: int) -> tuple[str, str, datetime]:
    symbol = str(event.get("symbol") or "").strip().upper()
    side = str(event.get("side") or "").strip().lower()
    if not symbol:
        raise ValueError(f"event_{index}_symbol_missing")
    if side not in {"long", "short"}:
        raise ValueError(f"event_{index}_side_invalid")
    trigger_close = _parse_timestamp(event.get("trigger_close_at"), f"event_{index}_trigger_close_at")
    return symbol, side, trigger_close


def summarize_maturity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive label-blind maturity readiness without reading any trade outcome.

    The maturity clock is recomputed from each immutable trigger close using the
    preregistered 10-day event contract. The payload's ``matures_at`` value is
    therefore evidence to verify, not an authoritative input. Event identities
    must also be unique so duplicate forward events cannot inflate readiness.
    """
    if payload.get("research_only") is not True:
        raise ValueError("research_only_not_true")
    if payload.get("label_blind") is not True:
        raise ValueError("label_blind_not_true")
    if payload.get("outcome_visible") is not False:
        raise ValueError("outcome_visible_not_false")
    if payload.get("promotion_allowed") is not False:
        raise ValueError("promotion_allowed_not_false")

    checked_at = _parse_timestamp(payload.get("checked_at"), "checked_at")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("events_not_list")

    declared_event_count = int(payload.get("event_count", -1))
    if declared_event_count != len(events):
        raise ValueError(f"event_count_mismatch:{declared_event_count}/{len(events)}")

    matured = 0
    pending_times: list[datetime] = []
    seen_event_ids: set[str] = set()
    seen_trigger_identities: set[tuple[str, str, datetime]] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"event_{index}_not_object")

        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise ValueError(f"event_{index}_event_id_missing")
        if event_id in seen_event_ids:
            raise ValueError(f"duplicate_event_id:{event_id}")
        seen_event_ids.add(event_id)

        identity = _event_identity(event, index)
        if identity in seen_trigger_identities:
            symbol, side, trigger_close = identity
            raise ValueError(
                "duplicate_symbol_side_trigger_bar:"
                f"{symbol}:{side}:{trigger_close.isoformat()}"
            )
        seen_trigger_identities.add(identity)

        trigger_close = identity[2]
        expected_maturity = maturity_at(trigger_close)
        declared_maturity = _parse_timestamp(event.get("matures_at"), f"event_{index}_matures_at")
        if declared_maturity != expected_maturity:
            raise ValueError(
                f"event_{index}_wrong_maturity_horizon:"
                f"{declared_maturity.isoformat()}/{expected_maturity.isoformat()}"
            )

        if expected_maturity <= checked_at:
            matured += 1
        else:
            pending_times.append(expected_maturity)

    declared_matured = int(payload.get("matured_event_count", -1))
    if declared_matured != matured:
        raise ValueError(f"matured_event_count_mismatch:{declared_matured}/{matured}")

    next_maturity = min(pending_times) if pending_times else None
    within_24h = sum(1 for value in pending_times if value <= checked_at + timedelta(hours=24))
    within_72h = sum(1 for value in pending_times if value <= checked_at + timedelta(hours=72))

    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "checked_at": checked_at.isoformat(),
        "event_count": len(events),
        "matured_event_count": matured,
        "pending_maturity_event_count": len(pending_times),
        "next_maturity_at": next_maturity.isoformat() if next_maturity else None,
        "maturities_next_24h": within_24h,
        "maturities_next_72h": within_72h,
        "development_target_matured_events": 60,
        "validation_target_matured_events": 40,
        "development_maturity_count_ready": matured >= 60,
        "maturity_contract_verified": True,
        "event_identity_uniqueness_verified": True,
        "note": "Maturity timing is recomputed from trigger_close_at under the frozen 10-day contract; no outcome access or live promotion is authorized.",
    }


def _get_json(base_url: str, path: str, api_key: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Radar-Key"] = api_key
    request = Request(f"{base_url.rstrip('/')}{path}", headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base_url = os.getenv("PRODUCTION_RADAR_API_BASE_URL", "").strip()
    api_key = os.getenv("PRODUCTION_RADAR_API_KEY", "").strip()
    expected_sha = os.getenv("EXPECTED_SHA", "").strip()
    if not base_url or not api_key:
        print("Missing production API configuration", file=sys.stderr)
        return 2

    version = _get_json(base_url, "/version")
    actual_sha = str(version.get("commit_sha") or "")
    if expected_sha and actual_sha != expected_sha:
        print(f"production_sha_mismatch:{actual_sha}/{expected_sha}", file=sys.stderr)
        return 1

    payload = _get_json(
        base_url,
        "/v1/research/swing-liquidity/forward-event-status",
        api_key,
    )
    try:
        summary = summarize_maturity_payload(payload)
    except (TypeError, ValueError) as exc:
        print(f"SWING_LIQUIDITY_MATURITY_STATUS_FAIL={exc}", file=sys.stderr)
        return 1

    summary["source_commit_sha"] = actual_sha
    print("SWING_LIQUIDITY_MATURITY_STATUS=" + json.dumps(summary, sort_keys=True))
    print("SWING LIQUIDITY LABEL-BLIND MATURITY STATUS VERIFIED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
