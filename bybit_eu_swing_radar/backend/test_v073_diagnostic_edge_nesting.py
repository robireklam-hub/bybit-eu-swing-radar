from app.v073_diagnostics_api import _cohort_match


COHORTS = [
    "STRUCTURE_5M",
    "VOLUME_PASS",
    "STRUCTURE_15M_PASS",
    "LIQUID_EXECUTABLE",
    "SCORE_GATES_PASS",
    "STRICT_ELIGIBLE",
    "STRICT_TRADE",
]


def _row(**overrides):
    row = {
        "base_net_r": 0.5,
        "pass_structure_5m": False,
        "pass_volume_confirmation": False,
        "pass_structure_15m": False,
        "pass_tradeable": False,
        "pass_side_execution_model": False,
        "pass_score_gates": False,
        "pass_strict_eligible": False,
        "pass_strict_trade": False,
    }
    row.update(overrides)
    return row


def test_later_true_flags_do_not_bypass_earlier_nested_gates():
    row = _row(
        pass_tradeable=True,
        pass_side_execution_model=True,
        pass_score_gates=True,
        pass_strict_eligible=True,
        pass_strict_trade=True,
    )
    assert not any(_cohort_match(row, cohort) for cohort in COHORTS)


def test_fully_passing_row_belongs_to_every_nested_cohort():
    row = _row(
        pass_structure_5m=True,
        pass_volume_confirmation=True,
        pass_structure_15m=True,
        pass_tradeable=True,
        pass_side_execution_model=True,
        pass_score_gates=True,
        pass_strict_eligible=True,
        pass_strict_trade=True,
    )
    assert [_cohort_match(row, cohort) for cohort in COHORTS] == [True] * len(COHORTS)


def test_membership_is_prefix_only_for_partial_progression():
    rows = [
        _row(pass_structure_5m=True),
        _row(pass_structure_5m=True, pass_volume_confirmation=True),
        _row(
            pass_structure_5m=True,
            pass_volume_confirmation=True,
            pass_structure_15m=True,
        ),
        _row(
            pass_structure_5m=True,
            pass_volume_confirmation=True,
            pass_structure_15m=True,
            pass_tradeable=True,
            pass_side_execution_model=True,
        ),
        _row(
            pass_structure_5m=True,
            pass_volume_confirmation=True,
            pass_structure_15m=True,
            pass_tradeable=True,
            pass_side_execution_model=True,
            pass_score_gates=True,
            pass_strict_eligible=True,
            pass_strict_trade=False,
        ),
    ]
    counts = [sum(_cohort_match(row, cohort) for row in rows) for cohort in COHORTS]
    assert counts == [5, 4, 3, 2, 1, 1, 0]
    assert counts == sorted(counts, reverse=True)


def test_rows_without_outcomes_are_not_in_edge_cohorts():
    row = _row(
        base_net_r=None,
        pass_structure_5m=True,
        pass_volume_confirmation=True,
        pass_structure_15m=True,
        pass_tradeable=True,
        pass_side_execution_model=True,
        pass_score_gates=True,
        pass_strict_eligible=True,
        pass_strict_trade=True,
    )
    assert not any(_cohort_match(row, cohort) for cohort in COHORTS)
