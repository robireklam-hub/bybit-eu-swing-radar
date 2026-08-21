from copy import deepcopy

from app.day_barrier_clear_watch import enrich_barrier_clear_watch


def _candidate(**overrides):
    item = {
        "symbol": "BTCUSDC",
        "side": "long",
        "category": "WATCH_ONLY",
        "state": "WATCH",
        "decision": "NO_TRADE",
        "tradeable": True,
        "shortable": True,
        "execution_status": "DAY_TRADE_EXECUTABLE",
        "setup_score": 71.88,
        "side_direction_score": 57.6,
        "expansion_score": 62.05,
        "quality_score": 99.99,
        "entry_zone": {"low": 69863.5, "high": 69877.6},
        "trigger": {
            "triggered": True,
            "route": "CLOSED_5M_RANGE_BREAKOUT",
            "price": 69863.5,
            "event_bar_time": "2026-08-20T07:05:00+00:00",
            "condition": "Closed 5m breakout above anchored range",
        },
        "metrics": {
            "nearest_structural_barrier": 69998.4,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "barrier_rr_net": 0.0,
            "expected_rr_without_barrier": 1.8,
        },
        "why_now": ["15m structure: bullish"],
        "risks": ["5m signals are vulnerable to false breakouts"],
    }
    item.update(overrides)
    return item


def test_yesterday_btc_case_becomes_armed_barrier_clear_watch_without_authorizing_trade():
    payload = {"watch_only_longs": [_candidate()]}
    output = enrich_barrier_clear_watch(payload)
    item = output["watch_only_longs"][0]

    assert item["decision"] == "NO_TRADE"
    assert item["category"] == "WATCH_ONLY"
    assert item["state"] == "WATCH"
    watch = item["barrier_clear_watch"]
    assert watch["status"] == "ARMED_BARRIER_CLEAR"
    assert watch["side"] == "long"
    assert watch["barrier_price"] == 69998.4
    assert watch["confirmation_condition"] == "closed 5m > 69998.4"
    assert watch["execution_authorized"] is False
    assert watch["fresh_recalculation_required"] is True
    assert watch["recalculate"] == ["entry", "stop", "targets", "target_path", "net_rr"]
    assert "CONDITIONAL WATCH ONLY: closed 5m > 69998.4" in item["trigger"]["condition"]
    assert any("ARMED_BARRIER_CLEAR" in text for text in item["why_now"])


def test_compact_audit_payload_is_also_mirrored_into_legacy_trigger_condition():
    compact = _candidate()
    compact.pop("metrics")
    compact.pop("expansion_score")
    compact.pop("quality_score")
    compact.pop("why_now")
    compact.pop("risks")
    compact.update(
        {
            "nearest_structural_barrier": 69998.4,
            "barrier_before_tp2": True,
            "target_path_valid": False,
            "expected_rr_without_barrier": 1.8,
        }
    )
    output = enrich_barrier_clear_watch({"long": compact})
    item = output["long"]
    assert item["barrier_clear_watch"]["status"] == "ARMED_BARRIER_CLEAR"
    assert "closed 5m > 69998.4" in item["trigger"]["condition"]
    assert item["decision"] == "NO_TRADE"


def test_barrier_watch_is_not_emitted_when_non_barrier_strict_evidence_is_weak():
    weak_score = _candidate(setup_score=64.0)
    weak_rr = _candidate()
    weak_rr["metrics"]["expected_rr_without_barrier"] = 1.79
    no_trigger = _candidate()
    no_trigger["trigger"]["triggered"] = False

    output = enrich_barrier_clear_watch(
        {"items": [weak_score, weak_rr, no_trigger]}
    )
    assert all("barrier_clear_watch" not in item for item in output["items"])


def test_short_requires_verified_borrowability_and_uses_below_confirmation():
    blocked = _candidate(side="short", shortable=False)
    blocked["metrics"]["nearest_structural_barrier"] = 69252.4
    blocked["trigger"]["price"] = 69300.0
    output = enrich_barrier_clear_watch({"items": [blocked]})
    assert "barrier_clear_watch" not in output["items"][0]

    allowed = deepcopy(blocked)
    allowed["shortable"] = True
    output = enrich_barrier_clear_watch({"items": [allowed]})
    watch = output["items"][0]["barrier_clear_watch"]
    assert watch["confirmation_condition"] == "closed 5m < 69252.4"
    assert watch["execution_authorized"] is False


def test_enrichment_never_mutates_source_payload():
    payload = {"watch_only_longs": [_candidate()]}
    before = deepcopy(payload)
    output = enrich_barrier_clear_watch(payload)
    assert payload == before
    assert output is not payload
    assert "barrier_clear_watch" in output["watch_only_longs"][0]
