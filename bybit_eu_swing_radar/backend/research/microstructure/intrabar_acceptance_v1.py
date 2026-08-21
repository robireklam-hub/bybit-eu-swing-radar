"""Prospective intrabar-acceptance comparison for day-trade v0.7.6.

RESEARCH ONLY. No live strategy, eligibility, score, RR, stop, target or execution
mutation is permitted by this module.

Question: after a technically valid setup reaches a breakout/barrier level, does
simple short-horizon price acceptance provide useful earlier information than
waiting for a full 5m close?

The comparison is frozen before outcomes:
- 15s acceptance = 3 consecutive closed 5s microstructure buckets on the trade side;
- 30s acceptance = 6 consecutive closed 5s buckets on the trade side;
- 5m-close comparator remains the existing closed-bar confirmation;
- order-flow/book-pressure fields are recorded as context, not optimized gates;
- no threshold search or outcome access is allowed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

SPEC_VERSION = "day-v076-intrabar-acceptance-v1"
STRATEGY_VERSION = "0.7.6"
BUCKET_SECONDS = 5
VARIANTS = {"ACCEPT_15S": 3, "ACCEPT_30S": 6}


@dataclass(frozen=True)
class AcceptanceResult:
    variant: str
    accepted: bool
    first_accept_bucket: int | None
    consecutive_buckets_required: int
    level: float
    side: str


def _mid(row: Any) -> float:
    if isinstance(row, dict):
        value = row.get("mid")
    else:
        value = getattr(row, "mid", None)
    return float(value or 0.0)


def _on_side(mid: float, level: float, side: str) -> bool:
    return mid > level if side == "long" else mid < level


def evaluate_acceptance(
    rows: Iterable[Any],
    *,
    level: float,
    side: str,
    variant: str,
) -> AcceptanceResult:
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    if variant not in VARIANTS:
        raise ValueError(f"unsupported variant: {variant}")
    if level <= 0:
        raise ValueError("level must be positive")

    required = VARIANTS[variant]
    streak = 0
    for index, row in enumerate(rows):
        if _on_side(_mid(row), level, side):
            streak += 1
            if streak >= required:
                return AcceptanceResult(
                    variant=variant,
                    accepted=True,
                    first_accept_bucket=index,
                    consecutive_buckets_required=required,
                    level=level,
                    side=side,
                )
        else:
            streak = 0
    return AcceptanceResult(
        variant=variant,
        accepted=False,
        first_accept_bucket=None,
        consecutive_buckets_required=required,
        level=level,
        side=side,
    )


def research_spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "threshold_search_allowed": False,
        "bucket_seconds": BUCKET_SECONDS,
        "variants": dict(VARIANTS),
        "comparator": "EXISTING_CLOSED_5M_CONFIRMATION",
        "flow_fields_role": "RECORDED_CONTEXT_ONLY_NOT_GATE",
        "execution_mutation": False,
    }
