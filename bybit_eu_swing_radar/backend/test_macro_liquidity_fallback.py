from research.macro_liquidity_fallback import parse_h41_series, parse_nyfed_rrp


def test_parse_h41_walcl_official_series_column_layout() -> None:
    text = (
        "Series Description,Other,Total assets less eliminations\n"
        "Unit:,Currency,Currency\n"
        "Multiplier:,1000000,1000000\n"
        "Currency:,USD,USD\n"
        "Unique Identifier:,H41/H41/OTHER_N.WW,H41/H41/RESPPMA_N.WW\n"
        "Time Period,OTHER_N.WW,RESPPMA_N.WW\n"
        "2026-07-22,1,6580000\n"
        "2026-07-29,2,6595000\n"
        "2026-08-05,3,NA\n"
    )
    assert parse_h41_series(text, "RESPPMA_N.WW") == [
        ("2026-07-22", 6580000.0),
        ("2026-07-29", 6595000.0),
    ]


def test_parse_h41_rejects_missing_series() -> None:
    text = (
        "Unique Identifier:,H41/H41/OTHER_N.WW\n"
        "Time Period,OTHER_N.WW\n"
        "2026-07-22,1\n"
    )
    try:
        parse_h41_series(text, "RESPPMA_N.WW")
    except ValueError as exc:
        assert str(exc) == "H.4.1 series not found: RESPPMA_N.WW"
    else:
        raise AssertionError("expected missing-series failure")


def test_parse_nyfed_rrp_aggregates_overnight_operations_by_day() -> None:
    payload = {
        "repo": {
            "operations": [
                {
                    "operationDate": "2026-08-14",
                    "term": "Overnight",
                    "termCalendarDays": 1,
                    "totalAmtAccepted": "1.250",
                },
                {
                    "operationDate": "2026-08-14",
                    "term": "Overnight",
                    "termCalendarDays": "1",
                    "totalAmtAccepted": "0.750",
                },
                {
                    "operationDate": "2026-08-14",
                    "term": "7 Day",
                    "termCalendarDays": 7,
                    "totalAmtAccepted": "100.000",
                },
                {
                    "operationDate": "2026-08-17",
                    "term": "Overnight",
                    "totalAmtAccepted": 2.125,
                },
            ]
        }
    }
    assert parse_nyfed_rrp(payload) == [
        ("2026-08-14", 2.0),
        ("2026-08-17", 2.125),
    ]
