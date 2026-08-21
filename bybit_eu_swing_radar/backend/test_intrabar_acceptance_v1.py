from research.microstructure.intrabar_acceptance_v1 import (
    evaluate_acceptance,
    research_spec,
)


def test_15s_acceptance_requires_three_consecutive_5s_buckets():
    rows = [{"mid": 100.1}, {"mid": 100.2}, {"mid": 100.3}]
    result = evaluate_acceptance(rows, level=100.0, side="long", variant="ACCEPT_15S")
    assert result.accepted is True
    assert result.first_accept_bucket == 2


def test_acceptance_streak_resets_on_rejection():
    rows = [
        {"mid": 100.1},
        {"mid": 100.2},
        {"mid": 99.9},
        {"mid": 100.1},
        {"mid": 100.2},
        {"mid": 100.3},
    ]
    result = evaluate_acceptance(rows, level=100.0, side="long", variant="ACCEPT_15S")
    assert result.accepted is True
    assert result.first_accept_bucket == 5


def test_short_acceptance_is_direction_symmetric():
    rows = [{"mid": 99.9}] * 6
    result = evaluate_acceptance(rows, level=100.0, side="short", variant="ACCEPT_30S")
    assert result.accepted is True
    assert result.first_accept_bucket == 5


def test_research_contract_is_label_blind_and_non_promoting():
    spec = research_spec()
    assert spec["strategy_version"] == "0.7.6"
    assert spec["research_only"] is True
    assert spec["label_blind"] is True
    assert spec["outcome_visible"] is False
    assert spec["promotion_allowed"] is False
    assert spec["threshold_search_allowed"] is False
    assert spec["execution_mutation"] is False
